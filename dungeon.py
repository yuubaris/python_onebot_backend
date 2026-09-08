# -*- coding: utf-8 -*-
"""地下城玩法核心逻辑。

规则要点：
- 用户持有任意 1 件装备即可进入地下城；进入后仅允许 /签到 与 /地下城（退出/状态）。
- 第 1 层总进度条 = 1000，推进速度按【公式】随时间流逝自动减少，每个用户单独核算。
- 难度曲线（防数值膨胀）：第 n 层总进度 = 1000 × n^0.75（幂函数，远慢于指数）。
- 产币曲线：每秒铜币 = (1/60) × n^0.6（增速低于难度曲线，更稳健），按 5 秒为单位核算。
- 同类型装备只取在【公式】中收益最大的一个，其余类型可叠加。
"""
import time

from models import db, UserItem
from equipment import load_equipment
import ore

LAYER1_TOTAL = 1000.0

# 用户初始属性（合理基础值，所有用户一致）
BASE_STATS = {
    "attack": 5,        # 攻击力
    "defense": 2,       # 防御力
    "hp": 20,           # 生命值
    "mp": 5,            # 魔力值
    "agility": 5,       # 敏捷
    "intelligence": 5,  # 智力
}

# 单次结算的最大循环保护（防止异常长时间导致的死循环）
_MAX_SETTLE_LAYERS = 100000


def layer_total(layer):
    """第 layer 层的总进度条（幂函数曲线：1000 × n^0.75，防止后期膨胀）。"""
    return round(LAYER1_TOTAL * (layer ** 0.75))


def coin_rate_per_sec(layer):
    """第 layer 层每秒获得铜币数（(1/60) × n^0.6，增速低于难度曲线）。"""
    return (1.0 / 60.0) * (layer ** 0.6)


def coin_per_5sec(layer):
    """第 layer 层每 5 秒获得的铜币数（按 5 秒为单位核算）。"""
    return coin_rate_per_sec(layer) * 5


def item_formula_score(item):
    """单个装备套入进度公式的收益分（用于同类型取最优）。"""
    atk = item.get("attack", 0)
    agi = item.get("agility", 0)
    inte = item.get("intelligence", 0)
    mp = item.get("mp", 0)
    defense = item.get("defense", 0)
    hp = item.get("hp", 0)
    return (atk * 2 + agi * 1.5 + inte * 1.25 + mp * 1.2) \
        * (1 + defense / 400) * (1 + hp / 600)


def effective_stats(user, owned):
    """用户的有效属性 = 初始属性 + 各类型中公式收益最高的那件装备属性之和。

    「同类型装备只取在【公式】中收益最大的一个」：
    对每件装备按其自身属性套用公式计算收益分，每个类型只取收益分最高的一件，
    再把各类型选中的装备属性与初始属性相加。
    """
    stats = dict(BASE_STATS)
    best = {}
    for it in owned:
        score = item_formula_score(it)
        t = it.get("type", "other")
        if t not in best or score > best[t]["score"]:
            best[t] = {"item": it, "score": score}
    for d in best.values():
        it = d["item"]
        for k in ("attack", "defense", "hp", "mp", "agility", "intelligence"):
            stats[k] += it.get(k, 0)
    return stats


def dungeon_speed(stats):
    """地下城推进速度【公式】。"""
    return (
        stats["attack"] * 2
        + stats["agility"] * 1.5
        + stats["intelligence"] * 1.25
        + stats["mp"] * 1.2
    ) * (1 + stats["defense"] / 400) * (1 + stats["hp"] / 600)


def _all_item_meta():
    """合并武具店(equipment.json)与铁匠铺(forge.json)的装备定义表（铁匠铺装备优先级更高）。"""
    by_id = {it["id"]: it for it in load_equipment()}
    try:
        from forge import load_forges
        for r in load_forges():
            by_id[r["id"]] = r
    except Exception:
        pass
    return by_id


def owned_items(user):
    """查询用户拥有的装备（从 JSON 中补全属性），仅返回存在定义的装备。"""
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == user.user_id)
    ).scalars().all()
    by_id = _all_item_meta()
    return [by_id[r.item_id] for r in rows if r.item_id in by_id]


def settle_dungeon(user):
    """结算地下城：按真实流逝时间推进层数进度并累积铜币（每个用户独立核算）。

    调用方需处于应用上下文内；本函数会提交数据库。
    """
    if user.dungeon_layer <= 0 or not user.dungeon_last_update:
        return
    now = time.time()
    dt = now - user.dungeon_last_update
    if dt <= 0:
        return

    stats = effective_stats(user, owned_items(user))
    speed = dungeon_speed(stats)
    if speed <= 0:
        speed = 1.0

    remaining = dt
    coins = user.dungeon_coin_acc
    cleared = 0
    guard = 0
    while remaining > 1e-9 and guard < _MAX_SETTLE_LAYERS:
        guard += 1
        total = layer_total(user.dungeon_layer)
        prog = max(user.dungeon_progress, 0.0)
        time_to_clear = prog / speed
        layer_time = min(remaining, time_to_clear)
        coins += coin_rate_per_sec(user.dungeon_layer) * layer_time
        user.dungeon_progress = prog - speed * layer_time
        remaining -= layer_time
        if user.dungeon_progress <= 1e-9 and remaining > 1e-9:
            user.dungeon_layer += 1
            user.dungeon_progress = layer_total(user.dungeon_layer)
            cleared += 1

    coin_int = int(coins)
    if coin_int > 0:
        user.copper += coin_int
        user.dungeon_coins_earned += coin_int
        user.dungeon_run_coins += coin_int
        coins -= coin_int
    user.dungeon_coin_acc = coins
    user.dungeon_cleared += cleared
    user.dungeon_last_update = now

    # 稀有矿石结算（400 层以上资格；每 15 分钟一个周期，余数直接丢弃）
    if user.dungeon_ore_eligible and user.dungeon_ore_last:
        dt_ore = now - user.dungeon_ore_last
        cycles = int(dt_ore // ore.ORE_CYCLE_SECONDS)
        if cycles > 0:
            layer_now = max(user.dungeon_layer, 1)
            gained = {}
            for _ in range(cycles):
                oid = ore.roll_ore(layer_now)
                if oid:
                    gained[oid] = gained.get(oid, 0) + 1
            user.dungeon_ore_last = now   # 不足一周期的时间直接丢弃
            if gained:
                ore.grant_ores(user.user_id, gained)

    db.session.commit()
