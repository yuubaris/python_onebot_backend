# -*- coding: utf-8 -*-
"""地下城玩法核心逻辑。

规则要点：
- 用户持有任意 1 件装备即可进入地下城；进入后仅允许 /签到 与 /地下城（退出/状态）。
- 第 1 层总进度条 = 1000，推进速度按【公式】随时间流逝自动减少，每个用户单独核算。
- 难度曲线（防数值膨胀）：第 n 层总进度 = 1000 × n^0.75（幂函数，远慢于指数）。
- 产币曲线：每秒铜币 = (1/60) × n^0.6（增速低于难度曲线，更稳健），按 5 秒为单位核算。
- 同类型装备只取在【公式】中收益最大的一个，其余类型可叠加。
- 职业（v3）：已转职只吃该职业 line + 通用(any)；未转职自动择优（物理/魔法取高）。
- 阶级（v3/v5）：商店/稀有装备只能装备 tier ≤ 当前阶级 的；锻造装备按「铁匠铺 Lv」穿戴
  （历史最高层 ≥ 该 Lv 解锁层即可用，不卡当前阶级）。
- 称号加成（v5）：全属性乘 TIER_ATTR_BONUS[tier]（每阶 +5%，T7=+35%），同装备下高阶称号更强。
- Boss 关（v4）：10 层精英 / 50 层小Boss / 100 层大Boss，Boss 层进度放大，通关随机掉落。
- 封顶（v8）：地下城最高 3600 层，通关 3600 晋 T7，之后驻留 3600 持续产出收益。
- 命名 Boss（/boss 挑战）：1000 前每 100 / 1000 后每 200 / 2000 后每 500 层的守关 Boss（共 18 个），
  自动推进首次经过其层时触发首通（boss 材料），重复经过只走现有掉落。
"""
import threading
import time

from models import db, UserItem
from equipment import load_equipment
from classes import class_line, item_line, LINE_PHYSICAL, LINE_MAGIC, LINE_ANY
from tiers import tier_of_price
import ore
import material
import classes as _classes_mod
import tiers as _tiers_mod

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

# 地下城最高层（封顶层）：通关 3600 层即晋升 T7（tiers.TIER_LAYER[7]=3600）。
# 达到封顶层后不再向更高层推进，保留在 3600 层驻留；挂机收益（铜币/矿/材料）持续产出。
DUNGEON_MAX_LAYER = 3600

# Boss 关分布（v4）：10 层精英 / 50 层小Boss / 100 层大Boss
BOSS_ELITE_EVERY = 10
BOSS_MINOR_EVERY = 50
BOSS_MAJOR_EVERY = 100
# Boss 层总进度倍率（血量提升）
BOSS_HP_MULT = {"elite": 1.5, "minor": 3.0, "major": 6.0}

# 待播报的 Boss 掉落汇总（user_id -> 文本行列表），由 dispatch 消费
_boss_report_lock = threading.Lock()
_boss_report = {}


def boss_type(layer):
    """返回该层是否为 Boss 关：None / 'elite' / 'minor' / 'major'。"""
    if layer % BOSS_MAJOR_EVERY == 0:
        return "major"
    if layer % BOSS_MINOR_EVERY == 0:
        return "minor"
    if layer % BOSS_ELITE_EVERY == 0:
        return "elite"
    return None


def effective_layer_total(layer):
    """该层实际总进度：Boss 层按类型放大血量；普通层 = layer_total。"""
    total = layer_total(layer)
    bt = boss_type(layer)
    if bt:
        total = int(total * BOSS_HP_MULT.get(bt, 1.0))
    return total


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
    """单个装备套入进度公式的收益分（用于同类型取最优）。

    评分 = (攻击×2 + 魔力×2 + 敏捷×1.5 + 智力×1.5) × (1+防御/400) × (1+生命/600)。

    （2026-09-08 方案A）输出权重对称化：攻击=魔力、敏捷=智力（攻=魔=2.0、敏=智=1.5），
    使物理/魔法两线每点输出词条价值相等，法师不再需要堆超高智/魔来对齐战士。

    对纯防御向装备（攻击/敏捷/智力/魔力均为 0，如 防具类），分子恒为 0，
    会导致同类型多件装备评分全部相同而永远选中第一件（最常见误选）——
    因此分子为 0 时改以防御/生命折算为保底分：defense×2 + hp×1.2，
    使其在类型内仍能区分强弱（系数与公式乘子权重一致）。
    """
    atk = item.get("attack", 0)
    agi = item.get("agility", 0)
    inte = item.get("intelligence", 0)
    mp = item.get("mp", 0)
    defense = item.get("defense", 0)
    hp = item.get("hp", 0)
    offence = atk * 2 + mp * 2 + agi * 1.5 + inte * 1.5
    if offence <= 0:
        # 纯防御向：按防御/生命折算保底分，保证同类型内能选出最强
        return (defense * 2 + hp * 1.2) * 1.0
    return offence * (1 + defense / 400) * (1 + hp / 600)


def _item_tier(item):
    """装备 tier：优先取字段；旧数据按价格兜底推断。"""
    t = item.get("tier")
    if t is not None:
        return int(t)
    return tier_of_price(item.get("price", 0))


def _forge_lv(item):
    """返回装备的锻造 Lv（forge 配方有 level 字段）；非锻造装备返回 None。"""
    lv = item.get("level")
    try:
        return int(lv) if lv is not None else None
    except (TypeError, ValueError):
        return None


def _item_usable(it, tier_max, best_layer):
    """装备是否对当前玩家生效：商店/稀有按「tier ≤ 当前阶级」；锻造按「铁匠铺 Lv 已解锁」。

    forge 配方都有 level 字段（Lv1~Lv4），其解锁层见 forge.FORGE_LV_LAYER：
    玩家历史最高层达到对应解锁层即可穿戴该锻造装（不受当前阶级 tier 限制）。
    """
    lv = _forge_lv(it)
    if lv is not None:
        # 锻造装备：以铁匠铺 Lv 解锁层为准（历史最高层达标即可用）
        from forge import FORGE_LV_LAYER
        return (best_layer or 0) >= FORGE_LV_LAYER.get(lv, 10 ** 9)
    return _item_tier(it) <= tier_max


def _stats_for_line(owned, line, tier_max, best_layer):
    """按「职业 line + 阶级/锻造解锁」过滤后计算有效属性（不乘称号加成）。

    可用 = line 为该职业(或 any 通用) 且（商店/稀有：tier ≤ 当前阶级；锻造：Lv 已按历史层解锁）；
    再按 type 取公式收益最高一件叠加。
    """
    stats = dict(BASE_STATS)
    best = {}
    for it in owned:
        if not _item_usable(it, tier_max, best_layer):
            continue
        if item_line(it) not in (line, LINE_ANY):
            continue
        score = item_formula_score(it)
        t = it.get("type", "other")
        if t not in best or score > best[t]["score"]:
            best[t] = {"item": it, "score": score}
    for d in best.values():
        it = d["item"]
        for k in ("attack", "defense", "hp", "mp", "agility", "intelligence"):
            stats[k] += it.get(k, 0)
    return stats


def effective_stats(user, owned):
    """用户的有效属性 = (初始属性 + 各类型收益最高装备之和) × 称号加成。

    - 同类型只取收益最高 1 件，跨类型叠加；
    - 已转职：只吃该职业 line + 通用(any) 的装备（不能混搭）；
    - 未转职：自动择优（分别按物理组/魔法组算速度，取高）；
    - 商店/稀有装备：只计入 tier ≤ 当前阶级 的装备；
      锻造装备：只计入「铁匠铺对应 Lv 已按历史最高层解锁」的装备（不卡当前阶级）；
    - 称号加成：全属性乘 TIER_ATTR_BONUS[user.tier]（每阶 +5%，T7=+35%）——
      同装备下，高阶称号实力更强。
    """
    tier_max = 0
    prof = ""
    best_layer = 0
    tier_bonus = 1.0
    if user is not None:
        tier_max = getattr(user, "tier", 0) or 0
        prof = getattr(user, "profession", "") or ""
        best_layer = historical_best_layer(user)
        from tiers import TIER_ATTR_BONUS
        tier_bonus = TIER_ATTR_BONUS.get(tier_max, 1.0)
    if prof and class_line(prof):
        stats = _stats_for_line(owned, class_line(prof), tier_max, best_layer)
    else:
        # 未转职 → 自动择优（any 通用件两组都能用，按各自专属件收益定胜负）
        s_phy = _stats_for_line(owned, LINE_PHYSICAL, tier_max, best_layer)
        s_mag = _stats_for_line(owned, LINE_MAGIC, tier_max, best_layer)
        stats = s_phy if dungeon_speed(s_phy) >= dungeon_speed(s_mag) else s_mag
    if tier_bonus != 1.0:
        for k in stats:
            stats[k] *= tier_bonus
    # 属性药水 BUFF（v8）：生效中的 buff_stat 点数叠加到各属性（不乘称号，独立叠加）
    if user is not None:
        try:
            import consumable as _consumable
            extra = _consumable.buff_stat_bonus(user.user_id)
            if extra:
                for k, v in extra.items():
                    stats[k] = stats.get(k, 0) + v
        except Exception:
            pass
    return stats


def dungeon_speed(stats):
    """地下城推进速度【公式】。

    （2026-09-08 方案A）输出权重对称化：攻击=魔力=2.0、敏捷=智力=1.5，
    物理/魔法两线每点输出词条价值相等（法师无需堆超高智/魔）；保留生存乘区。
    """
    return (
        stats["attack"] * 2
        + stats["mp"] * 2
        + stats["agility"] * 1.5
        + stats["intelligence"] * 1.5
    ) * (1 + stats["defense"] / 400) * (1 + stats["hp"] / 600)


def historical_best_layer(user):
    """历史最高层（用于定阶 / 晋升门槛）：以「当前层 / 退出保存层」为准。

    dungeon_layer（在线推进层）与 saved_dungeon_layer（退出时保存层）反映真实到达层，
    两者均为 0（如仅剩旧累计）时才用 dungeon_cleared 兜底 —— 避免“反复刷低层累计数”
    被误当成真实最高层而把老玩家一次性顶到顶阶。
    """
    if user is None:
        return 0
    layer = max(getattr(user, "dungeon_layer", 0) or 0,
                getattr(user, "saved_dungeon_layer", 0) or 0)
    if layer <= 0:
        layer = getattr(user, "dungeon_cleared", 0) or 0
    return layer


def _all_item_meta():
    """合并 武器库(equipment.json) + 铁匠铺(forge.json) + Boss 稀有池(rare_drops.json)
    的装备定义表（forge 优先级高于商店；rare 仅用于已掉落件的属性展示/结算）。
    """
    by_id = {it["id"]: it for it in load_equipment()}
    try:
        from forge import load_forges
        for r in load_forges():
            by_id[r["id"]] = r
    except Exception:
        pass
    try:
        for r in _load_rare_pool():
            by_id.setdefault(r["id"], r)  # 稀有 id 不与商店冲突则加入
    except Exception:
        pass
    return by_id


def find_any_item(name_or_id):
    """从装备全集（商店 equipment.json + 铁匠铺 forge.json + Boss 稀有池 rare_drops.json）
    按名称或 id 精确查找（大小写不敏感），找不到返回 None。

    用途：出售等需要识别「非商店来源」装备（如稀有装备）的场合；
    购买请继续使用 equipment.find_item（商店买不到稀有/锻造装）。
    """
    key = (name_or_id or "").strip().lower()
    if not key:
        return None
    for it in _all_item_meta().values():
        if str(it.get("name", "")).lower() == key or str(it.get("id", "")).lower() == key:
            return it
    return None


def owned_items(user):
    """查询用户拥有的装备（从 JSON 中补全属性），仅返回存在定义的装备。"""
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == user.user_id)
    ).scalars().all()
    by_id = _all_item_meta()
    return [by_id[r.item_id] for r in rows if r.item_id in by_id]


def owned_item_rows(user):
    """查询用户持有装备的 (meta, is_new) 列表（供背包展示 new! 与来源提示）。"""
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == user.user_id)
    ).scalars().all()
    by_id = _all_item_meta()
    out = []
    for r in rows:
        if r.item_id in by_id:
            out.append((by_id[r.item_id], r.is_new or 0))
    return out


# ---------- Boss 掉落（v4） ----------

_BOSS_NAME = {"elite": "精英 Boss", "minor": "小 Boss", "major": "大 Boss"}


def _queue_boss_report(user_id, lines):
    """把本次 Boss 通关掉落行入队，待 dispatch 播报。"""
    if not lines:
        return
    with _boss_report_lock:
        _boss_report.setdefault(user_id, []).extend(lines)


def take_boss_report(user_id):
    """取出并清空某用户的待播报 Boss 掉落行。"""
    with _boss_report_lock:
        return _boss_report.pop(user_id, [])


# 稀有池缓存（mtime 判断，热重载与 equipment/forge 一致）
_rare_cache = {"mtime": None, "items": []}


def _load_rare_pool():
    """读取稀有装备独立池（rare_drops.json）；文件缺失/损坏返回 []。

    带 mtime 缓存：修改 JSON 后自动生效（与 equipment/forge 加载器一致）。
    """
    try:
        import json as _json
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "rare_drops.json")
        mtime = _os.path.getmtime(path)
        if _rare_cache["mtime"] == mtime:
            return _rare_cache["items"]
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        items = data.get("items", []) if isinstance(data, dict) else data
        _rare_cache["mtime"] = mtime
        _rare_cache["items"] = items
        return items
    except Exception:
        return []


def rare_item_ids():
    """返回稀有池全部装备 id 集合（供背包/播报标注 ✦ 稀有）。"""
    return {r.get("id") for r in _load_rare_pool() if r.get("id")}


def _roll_boss_ore(layer, btype):
    """Boss 通关掉落矿石：按 Boss 类型给稀有度权重，返回 ore_id。

    相对普通层（ore.roll_ore，档位概率固定）Boss 层掉率概率更高：
    高稀有档占比随 Boss 类型提升（精英→小Boss→大Boss 递增），且 Boss 必掉。
    矿石从对应档位全池随机（含扩充后的新矿）；神话档仅大 Boss 掉落。
    """
    import random
    from ore import load_ores
    ores = load_ores()
    if not ores:
        return None
    by_rar = {}
    for o in ores:
        by_rar.setdefault(o.get("rarity", "common"), []).append(o["id"])
    # 权重和为 1（必掉）；高稀有档占比：精英 < 小Boss < 大Boss
    if btype == "major":
        weights = [("myth", 0.18), ("legendary", 0.55), ("rare", 0.22), ("common", 0.05)]
    elif btype == "minor":
        weights = [("legendary", 0.20), ("rare", 0.50), ("common", 0.30)]
    else:
        weights = [("rare", 0.45), ("common", 0.55)]
    r = random.random()
    acc = 0.0
    for rarity, w in weights:
        acc += w
        if r <= acc and by_rar.get(rarity):
            return random.choice(by_rar[rarity])
    return None


def _grant_boss_rare(user, layer, btype):
    """按稀有掉落池抽一件「玩家可用」的稀有装备并写入背包（标 new）。

    可用 = 职业 line 匹配（未转职→仅 any）+ tier ≤ 玩家当前阶级。
    掉落偏向（v6）：以「玩家当前阶级档」为目标，越接近目标档的稀有越易出，
    大 Boss（major）比小 Boss / 精英更收窄到当前档；低档稀有保留小概率作为保底。
    返回稀有装备 meta dict 或 None。
    """
    import random
    pool = _load_rare_pool()
    if not pool:
        return None
    prof = getattr(user, "profession", "") or ""
    cl = class_line(prof) if prof else None
    tier_max = getattr(user, "tier", 0) or 0
    cand = []  # (item, tier)
    for it in pool:
        line = item_line(it)
        if cl:
            # 已转职：接受 本职业 line 或 通用(any)
            if line not in (cl, LINE_ANY):
                continue
        else:
            # 未转职：仅通用(any) 可用（职业专属先引导转职）
            if line != LINE_ANY:
                continue
        t = _item_tier(it)
        if t > tier_max:
            continue
        cand.append((it, t))
    if not cand:
        return None

    # 目标档：优先玩家当前阶级档；若该档无可用稀有，逐级下探到最近可用档
    by_tier = {}
    for it, t in cand:
        by_tier.setdefault(t, []).append((it, t))
    target = tier_max
    while target > 0 and target not in by_tier:
        target -= 1

    # 偏向权重：离目标档越远越难出；Boss 越强收窄越狠（大Boss最贴当前档）
    spread = {"major": 3.0, "minor": 2.0, "elite": 1.5}.get(btype, 1.5)
    weighted = []
    for it, t in cand:
        dist = max(0, target - t)          # 恒 ≥0（t ≤ tier_max）
        w = 1.0 / (1.0 + dist * spread)    # 目标档权重 1.0，越远越小
        weighted.append((w, it))
    total = sum(w for w, _ in weighted)
    r = random.random() * total
    acc = 0.0
    picked = weighted[-1][1]
    for w, it in weighted:
        acc += w
        if r <= acc:
            picked = it
            break
    db.session.add(UserItem(user_id=user.user_id, item_id=picked["id"], is_new=1))
    return picked


# 额外金钱奖励：通关 Boss 层即给（与矿石并列、不依赖稀有装备概率）。
# 金额 ≈ 该层挂机产币速率 × 奖励时长，随层数成长、按 Boss 类型分级：
#   精英 = 5 分钟产币 / 小Boss = 15 分钟 / 大Boss = 40 分钟（等价 层数^0.6 × 分钟数），
# 让「打 Boss」有可感的通关收益（大致相当于多送一段挂机时长），且不与产币曲线脱钩、不易通胀。
BOSS_COIN_MINUTES = {"elite": 5, "minor": 15, "major": 40}


def _boss_coin_bonus(layer, btype):
    """通关某 Boss 层的额外铜币奖励（即时入账，不随挂机 coins 小数累积）。"""
    rate = coin_rate_per_sec(layer)                    # (1/60) × n^0.6 铜/秒
    minutes = BOSS_COIN_MINUTES.get(btype, 2)
    bonus = int(rate * 60.0 * minutes)                 # 秒 → 铜币
    return max(bonus, 1)


def _roll_boss_drop(user, layer, btype):
    """通关某 Boss 层：掷掉落（金钱 / 矿石 / 稀有装备），写库并返回播报行。

    通关即给：额外金钱 + 必掉矿石；稀有装备按概率（精英 25% / 小Boss 55% / 大Boss 100%）。
    """
    import random
    from ore import ore_meta
    lines = []
    # 额外金钱（Boss 专属通关奖励，即时入账）
    bonus = _boss_coin_bonus(layer, btype)
    if bonus > 0:
        user.copper += bonus
        user.dungeon_coins_earned += bonus
        user.dungeon_run_coins += bonus
        lines.append(f"💰 +{bonus} 铜币")
    ore_id = _roll_boss_ore(layer, btype)
    if ore_id:
        ore.grant_ores(user.user_id, {ore_id: 1})
        meta = ore_meta(ore_id)
        nm = meta["name"] if meta else ore_id
        lines.append(f"💎 矿石 ×1（{nm}）")
    # 特殊物品（精英 25% / 小Boss 45% / 大Boss 80%，R17；掉落增益 scope=special）
    special_id = material.roll_special(btype)
    if special_id:
        scnt = 1
        try:
            import consumable as _consumable
            _ms = _consumable.drop_bonus(user.user_id, "material", scope="special")
            if _ms > 1.0:
                scnt = max(1, int(_ms))
        except Exception:
            pass
        material.grant_materials(user.user_id, {special_id: scnt})
        smeta = material.material_meta(special_id)
        snm = smeta["name"] if smeta else special_id
        lines.append(f"✨ 特殊物品 ×{scnt}（{snm}）")
    # 稀有装备概率：精英 25% / 小Boss 55% / 大Boss 100%
    p = {"elite": 0.25, "minor": 0.55, "major": 1.0}.get(btype, 0.25)
    if random.random() < p:
        from classes import TYPE_NAMES as _tn
        it = _grant_boss_rare(user, layer, btype)
        if it:
            tn = _tn.get(it.get("type", "other"), it.get("type", ""))
            lines.append(f"✦ {it['name']}({tn}·稀有) new！")
    return lines


def _auto_named_boss_first(user, layer):
    """自动推进「首次经过」某命名守关 Boss 层 → 触发首通（给 boss 材料）。

    复用 boss 模块的自动推进首通判定（该 Boss 未被 /boss 挑战/推进首通过才给，
    且只给一次）。为避免 dungeon↔boss 循环 import，这里延迟 import boss。
    返回播报文本行列表（可为空）。
    """
    try:
        import boss as _boss
    except Exception:
        return []
    try:
        return _boss.on_auto_first_clear(user, layer)
    except Exception:
        return []


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
    report = []
    # 掉落增益倍率（道具 drop_bonus）：金钱/矿石/草药/特殊（无 buff 则 1.0）
    try:
        import consumable as _consumable
        uid = user.user_id
        mult_coin = _consumable.drop_bonus(uid, "coin")
        mult_ore = _consumable.drop_bonus(uid, "material", scope="ore")
        mult_herb = _consumable.drop_bonus(uid, "material", scope="herb")
        mult_special = _consumable.drop_bonus(uid, "material", scope="special")
        mult_myriad = _consumable.drop_bonus(uid, "material", scope="all")
    except Exception:
        mult_coin = mult_ore = mult_herb = mult_special = mult_myriad = 1.0
    while remaining > 1e-9 and guard < _MAX_SETTLE_LAYERS:
        guard += 1
        total = effective_layer_total(user.dungeon_layer)
        prog = max(user.dungeon_progress, 0.0)
        time_to_clear = prog / speed
        layer_time = min(remaining, time_to_clear)
        coins += coin_rate_per_sec(user.dungeon_layer) * layer_time
        user.dungeon_progress = prog - speed * layer_time
        remaining -= layer_time
        if user.dungeon_progress <= 1e-9 and remaining > 1e-9:
            cleared_layer = user.dungeon_layer  # 刚通关的这层
            bt = boss_type(cleared_layer)
            # 命名 Boss 关（有名字的守关：100/200/…/3000/3600）：
            # 进度跑完 → 按战力胜率判定胜负（我方战力高于 Boss 战力则胜率上升、反之下降，保留 3% 保底）；
            # 判定失败 → 进度重置（重新计算这一关进度），本关收益继续计算；
            # 其余 Boss 关（精英/小Boss/无名字的整百层 Boss）进度跑完即通关。
            import random as _random
            try:
                import boss as _boss_mod
                named = _boss_mod.boss_for_layer(cleared_layer)
            except Exception:
                named = None
            if named is not None:
                p = _boss_mod._success_rate(user, named)
                if _random.random() >= p:
                    # 挑战失败：重置本关进度（重新攻略），收益照常继续计算；不推进、不结算掉落
                    user.dungeon_progress = effective_layer_total(cleared_layer)
                    report.append(f"💀 第{cleared_layer}层 {named['name']} 挑战失败（胜率 {p * 100:.0f}%），进度重置，收益继续计算。")
                    continue
            if cleared_layer >= DUNGEON_MAX_LAYER:
                # —— 封顶层（3600）：不再向更高层推进 ——
                if not user.dungeon_capped:
                    # 首次通关 3600：晋 T7 依据达成（历史最高层=3600），给一次最终 Boss 掉落
                    user.dungeon_capped = 1
                    cleared += 1
                    if bt:
                        drops = _roll_boss_drop(user, cleared_layer, bt)
                        if drops:
                            report.append(f"🎉 通关 第{cleared_layer}层 {_BOSS_NAME.get(bt, bt)}（封顶）！" + "；".join(drops))
                # 命名 Boss 自动推进首通（100/200/…/3600 守关层）→ boss 材料（未首通才给）
                _auto_lines = _auto_named_boss_first(user, cleared_layer)
                if _auto_lines:
                    report.append("；".join(_auto_lines))
                # 驻留：进度重置为满值，收益（铜币/矿石/材料）持续产出；不再推进、不再重复触发 Boss 掉落
                user.dungeon_progress = effective_layer_total(DUNGEON_MAX_LAYER)
            else:
                user.dungeon_layer += 1
                user.dungeon_progress = effective_layer_total(user.dungeon_layer)
                cleared += 1
                if bt:
                    # 通关 Boss 层：掉落（通关瞬间结算）
                    drops = _roll_boss_drop(user, cleared_layer, bt)
                    if drops:
                        report.append(f"🎉 通关 第{cleared_layer}层 {_BOSS_NAME.get(bt, bt)}！" + "；".join(drops))
                # 命名 Boss 自动推进首通（100/200/…/3600 守关层）→ boss 材料（未首通才给）
                _auto_lines = _auto_named_boss_first(user, cleared_layer)
                if _auto_lines:
                    report.append("；".join(_auto_lines))

    coins *= mult_coin                       # 金钱掉落增益
    coin_int = int(coins)
    if coin_int > 0:
        user.copper += coin_int
        user.dungeon_coins_earned += coin_int
        user.dungeon_run_coins += coin_int
        coins -= coin_int                    # 保留 <1 铜币小数（mult=1 时与原逻辑一致）
    user.dungeon_coin_acc = coins
    user.dungeon_cleared += cleared
    user.dungeon_last_update = now

    # 层数型 BUFF（v8）：本结算推进 cleared 层 → 扣减各层数型 buff 的剩余层数（耗尽即清理）
    if cleared > 0:
        try:
            import consumable as _consumable
            _consumable.consume_layers(user.user_id, cleared)
        except Exception:
            pass

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
            # 矿石掉率增益（采掘符 scope=ore）
            if mult_ore > 1.0 and gained:
                gained = {oid: max(1, int(cnt * mult_ore)) for oid, cnt in gained.items()}
            user.dungeon_ore_last = now   # 不足一周期的时间直接丢弃
            if gained:
                ore.grant_ores(user.user_id, gained)

    # 材料结算（≥150 层资格；草药+特殊同频同概率，与矿石同周期并列，互不抢占）
    if user.dungeon_herb_eligible and user.dungeon_herb_last:
        dt_herb = now - user.dungeon_herb_last
        herb_cycles = int(dt_herb // material.HERB_CYCLE_SECONDS)
        if herb_cycles > 0:
            layer_now = max(user.dungeon_layer, 1)
            h_gained, s_gained, m_gained = {}, {}, {}
            for _ in range(herb_cycles):
                mid = material.roll_material(layer_now)
                if not mid:
                    continue
                meta = material.material_meta(mid)
                cat = meta.get("category") if meta else ""
                if cat == "special":
                    s_gained[mid] = s_gained.get(mid, 0) + 1
                elif cat == "boss":
                    m_gained[mid] = m_gained.get(mid, 0) + 1   # 万宝源晶(神话档)
                else:
                    h_gained[mid] = h_gained.get(mid, 0) + 1
            # 掉率增益：草药乘采药符(scope=herb)、特殊乘祈灵符(scope=special)、
            # 万宝源晶只乘全材料增益(万宝符 scope=all，不吃采药/祈灵符)
            if mult_herb > 1.0 and h_gained:
                h_gained = {mid: max(1, int(cnt * mult_herb)) for mid, cnt in h_gained.items()}
            if mult_special > 1.0 and s_gained:
                s_gained = {mid: max(1, int(cnt * mult_special)) for mid, cnt in s_gained.items()}
            if mult_myriad > 1.0 and m_gained:
                m_gained = {mid: max(1, int(cnt * mult_myriad)) for mid, cnt in m_gained.items()}
            user.dungeon_herb_last = now
            gained = {**h_gained, **s_gained, **m_gained}
            if gained:
                material.grant_materials(user.user_id, gained)
                names = []
                for mid, cnt in gained.items():
                    hm = material.material_meta(mid)
                    names.append(f"{hm['name'] if hm else mid}×{cnt}")
                if names:
                    report.append("📦 材料掉落：" + "、".join(names))

    if report:
        _queue_boss_report(user.user_id, report)
    db.session.commit()
