# -*- coding: utf-8 -*-
"""命名守关 Boss 挑战模块（/boss 列表、/boss 挑战 <名>）。

Boss 数据在 bosses.json（18 个命名守关 Boss：1000 前每100 / 1000 后每200 /
2000 后每500 / 3000 大 Boss「千界魔尊·裂界」/ 3600 最终「万瓜之主·夕张」），player 进度记于 user_boss 表。

规则：
- 解锁：历史最高层 ≥ boss.layer；
- 成功率：玩家强度 S = dungeon_speed(effective_stats(...))；Boss 基准 B0 =
  effective_layer_total(layer)/3600；p = clamp(0.03, 0.97, x^3/(1+x^3))，x=S/B0；
- 每日挑战额度（2026-09-08 更新）：普通守关(除 1000/2000/3000/3600 外的命名 Boss)
  每日组内共 3 次（跨 Boss 共享）；大 Boss(1000/2000/3000/3600) 每日各 1 次（按日期重置，胜败均扣）；
- 掉落：Boss 材料 首通必出、重复成功固定 60%（不随次数递减）；
  铜币 = 基准×max(0.2, 0.8^n)；稀有掉率 = 基础×max(0.1, 0.8^n)（n=累计成功次数，
  永久递减跨日不清零）；稀有 tier ≤ min(玩家阶级, boss.tier_cap)（低级 Boss 只掉低级装）；
- 3600 最终 Boss 挑战成功额外掉 1 个特殊道具（用途暂空，占位收藏）；
- 自动推进首通：settle_dungeon 经过命名 Boss 层且首次击破 → 给 Boss 材料一次（dungeon 调用）。
"""
import json
import os
import random
import time
from datetime import datetime

from .models import db, UserBoss
from . import material
from . import dungeon

BOSSES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bosses.json")

# —— Boss 挑战每日额度（2026-09-08 更新）——
# 普通守关 = 除 1000/2000/3000/3600 外的全部命名 Boss（“100 倍数”档）；每日组内总计 3 次（跨 Boss 共享）。
# 大 Boss   = 1000 / 2000 / 3000 / 3600；每日各 1 次（每 Boss 独立计数）。
BIG_BOSS_LAYERS = (1000, 2000, 3000, 3600)
GROUP_DAILY_LIMIT = {"normal": 3, "big": 1}
GROUP_LABEL = {"normal": "守关 Boss", "big": "大 Boss（1000/2000/3000/3600）"}
# 四大 Boss 专属特殊道具（收藏级，独立 category=souvenir，v2.12.4 起统一极低掉率）：
# 3600「万瓜圣契」原为挑战成功必掉，按用户「掉率极低」口径统一为 SOUVENIR_DROP_RATE。
FINAL_BOSS_RELIC = "souvenir_wangua"
SOUVENIR_LAYERS = {1000: "souvenir_final", 2000: "souvenir_abyss", 3000: "souvenir_rift", 3600: FINAL_BOSS_RELIC}
SOUVENIR_DROP_RATE = 0.001   # 专属特殊道具掉率（极低 0.1%，与 4 大 Boss 专属装备同档）
# 究极大 Boss(1000/2000/3000) 挑战成功必掉的专属材料「万宝源晶」；3600 最终 Boss 在其外额外掉落（万宝符原料）
MYRIAD_GEM = "boss_myriad"

REPEAT_DROP_RATE = 0.60       # 重复挑战成功时 Boss 材料随机掉率（不随次数递减）
COIN_DECAY_BASE = 0.8         # 铜币永久衰减速率
COIN_DECAY_FLOOR = 0.2        # 铜币衰减下限
RARE_DECAY_BASE = 0.8         # 稀有掉率永久衰减速率
RARE_DECAY_FLOOR = 0.1        # 稀有掉率衰减下限
RARE_BASE_RATE = 1.0          # 挑战稀有基础掉率（大Boss级，首次 100%）

_cache = {"mtime": None, "bosses": [], "by_id": {}, "by_name": {}, "by_layer": {}}


def load_bosses(force=False):
    """读取命名守关 Boss 列表（带 mtime 缓存）。"""
    mtime = os.path.getmtime(BOSSES_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["bosses"]
    with open(BOSSES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    bosses = data.get("bosses", [])
    _cache["mtime"] = mtime
    _cache["bosses"] = bosses
    _cache["by_id"] = {b["id"]: b for b in bosses}
    _cache["by_name"] = {b["name"]: b for b in bosses}
    _cache["by_layer"] = {int(b["layer"]): b for b in bosses}
    return bosses


def all_bosses():
    """按层升序返回全部命名 Boss。"""
    return sorted(load_bosses(), key=lambda b: b.get("layer", 0))


def find_boss(name_or_id):
    """统一 Boss 匹配（供 /挑战 <Boss名|层数|称号> 与 /boss 挑战 使用）。

    兼容写法：完整名 / id / 层数（"1000"、"1000层"）/ 称号 tag（"究极"）/ 部分名唯一命中；
    部分名多命中返回 None（由调用方列建议）。
    """
    load_bosses()
    key = (name_or_id or "").strip()
    if not key:
        return None
    # 1) 精确名/id
    if key in _cache["by_name"]:
        return _cache["by_name"][key]
    if key in _cache["by_id"]:
        return _cache["by_id"][key]
    low = key.lower()
    for iid, b in _cache["by_id"].items():
        if iid.lower() == low:
            return b
    for name, b in _cache["by_name"].items():
        if name.lower() == low:
            return b
    # 2) 层数（"1000"、"1000层"）
    num = key.replace("层", "").strip()
    if num.isdigit():
        return _cache["by_layer"].get(int(num))
    # 3) 去 "boss" 前缀壳（"boss裂风狼王"）后按称号/部分名匹配
    bare = key
    if bare.lower().startswith("boss") and len(bare) > 4:
        bare = bare[4:].strip()
    for b in _cache["bosses"]:
        if (b.get("tag") or "").strip() == bare:
            return b
    hits = [b for b in _cache["bosses"] if bare in b["name"] or bare.lower() in b["id"].lower()]
    return hits[0] if len(hits) == 1 else None


def suggest_bosses(keyword, limit=5):
    """按名称/id 包含关系给建议。"""
    load_bosses()
    kw = (keyword or "").strip()
    if not kw:
        return []
    hits = [b for b in _cache["bosses"] if kw in b["name"] or kw in b["id"]]
    return hits[:limit]


def boss_for_layer(layer):
    """返回指定层（自动推进通关层）对应的命名 Boss；非命名层返回 None。"""
    load_bosses()
    return _cache["by_layer"].get(int(layer or 0))


def boss_by_id(boss_id):
    load_bosses()
    return _cache["by_id"].get(boss_id)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _get_state(user, boss):
    """取 (UserBoss, is_new) —— 无记录则建新行（不提交，由调用方 commit）。"""
    row = db.session.execute(
        db.select(UserBoss).where(UserBoss.user_id == user.user_id,
                                  UserBoss.boss_id == boss["id"])
    ).scalars().first()
    if row is None:
        row = UserBoss(user_id=user.user_id, boss_id=boss["id"])
        db.session.add(row)
    return row


def boss_group(boss):
    """Boss 分组：layer ∈ {1000,2000,3000,3600} → 'big'；其余（100 倍数普通守关）→ 'normal'。"""
    return "big" if int(boss.get("layer", 0) or 0) in BIG_BOSS_LAYERS else "normal"


def group_used_today(user, boss):
    """该 Boss 今日已挑战次数：普通守关跨 Boss 共享聚合；大 Boss(1000/2000/3000/3600) 每 Boss 独立计数。"""
    g = boss_group(boss)
    today = _today()
    if g == "big":
        # 大 Boss 每日各 1 次：只看该 Boss 自身当日计数
        row = db.session.execute(
            db.select(UserBoss).where(
                UserBoss.user_id == user.user_id,
                UserBoss.boss_id == boss["id"],
                UserBoss.fight_date == today,
            )
        ).scalars().first()
        return row.fight_count if row else 0
    # 普通守关：组内跨 Boss 共享（同组多个 Boss 的当日计数求和）
    member_ids = [b["id"] for b in all_bosses() if boss_group(b) == "normal"]
    if not member_ids:
        return 0
    rows = db.session.execute(
        db.select(UserBoss).where(
            UserBoss.user_id == user.user_id,
            UserBoss.boss_id.in_(member_ids),
            UserBoss.fight_date == today,
        )
    ).scalars().all()
    return sum(r.fight_count for r in rows)


def _success_rate(user, boss):
    """按当前装备强度计算挑战成功率（0.03 ~ 0.97）。"""
    S = dungeon.dungeon_speed(dungeon.effective_stats(user, dungeon.owned_items(user)))
    if S <= 0:
        S = 1.0
    layer = int(boss.get("layer", 100))
    # 命名 Boss 战力锚点：穿满对应装备档 ≈70% 胜率（3000/3600 对应锻造装难度）
    B0 = dungeon.named_boss_b0(user, layer) if boss.get("layer") else dungeon.effective_layer_total(layer) / 3600.0
    if B0 <= 0:
        B0 = 1.0
    x = S / B0
    p = (x ** 3) / (1 + x ** 3)
    return max(0.03, min(0.97, p))


def _coin_bonus(layer, wins):
    """挑战成功铜币 = 大Boss 40 分钟产币基准 × 永久衰减（n=累计成功次数）。"""
    base = dungeon._boss_coin_bonus(layer, "major")
    mult = max(COIN_DECAY_FLOOR, COIN_DECAY_BASE ** wins)
    return max(1, int(base * mult))


def _roll_rare(user, boss, wins):
    """稀有装备掉落（概率随成功次数永久递减；tier ≤ min(玩家阶级, boss.tier_cap)）。"""
    if wins and random.random() >= RARE_BASE_RATE * max(RARE_DECAY_FLOOR, RARE_DECAY_BASE ** wins):
        return None
    from .classes import item_line, LINE_ANY, class_line
    prof = (user.profession or "") or ""
    cl = class_line(prof) if prof else None
    tier_cap = min(user.tier or 0, int(boss.get("tier_cap", 0) or 0))
    pool = dungeon._load_rare_pool()
    cand = []
    for it in pool:
        line = item_line(it)
        if cl:
            if line not in (cl, LINE_ANY):
                continue
        elif line != LINE_ANY:
            continue
        t = int(it.get("tier", 0) or 0)
        if t > tier_cap:
            continue
        cand.append((it, t))
    if not cand:
        return None
    # 偏向当前可用的最高档（目标 = tier_cap 就近）
    by_tier = {}
    for it, t in cand:
        by_tier.setdefault(t, []).append(it)
    target = tier_cap
    while target > 0 and target not in by_tier:
        target -= 1
    if target not in by_tier:
        return None
    return random.choice(by_tier[target])


def on_auto_first_clear(user, layer):
    """自动推进首次经过命名 Boss 层：未首通则给 Boss 材料并记首通（只给一次）。

    由 dungeon.settle_dungeon 调用；返回播报文本行（可为空）。
    """
    boss = boss_for_layer(layer)
    if boss is None:
        return []
    row = _get_state(user, boss)
    if row.first_clear_date:      # 已首通（挑战或推进）→ 不再给
        return []
    row.first_clear_date = _today()
    material.grant_materials(user.user_id, {boss["material"]: 1})
    db.session.commit()
    mm = material.material_meta(boss["material"])
    mname = mm["name"] if mm else boss["material"]
    return [f"🎁 通关 {boss['name']}（守关 Boss）首通！获得 Boss 材料：{mname} ×1"]


def challenge_boss(user, boss):
    """挑战指定 Boss：判定 → 结算。返回 (文本, 是否挑战成功)。

    每日额度（2026-09-08 更新）：普通守关(100 倍数) 组内共 3 次（跨 Boss 共享）、
    大 Boss(1000/2000/3000/3600) 每日各 1 次；胜/败都扣次数；掉落按 §1.7 规则。
    """
    today = _today()
    if dungeon.historical_best_layer(user) < int(boss.get("layer", 100)):
        return f"🔒 挑战「{boss['name']}」需地下城历史最高层 ≥ {boss['layer']} 层（当前 {dungeon.historical_best_layer(user)}）。", False
    g = boss_group(boss)
    limit = GROUP_DAILY_LIMIT[g]
    used = group_used_today(user, boss)
    subject = boss["name"] if g == "big" else GROUP_LABEL[g]
    if used >= limit:
        return f"今日「{subject}」挑战次数已用完（{used}/{limit}），明天再来吧！", False
    # 该 Boss 当日计数（胜败均扣；大 Boss 独立计数 / 普通守关计入组内共享额度）
    row = _get_state(user, boss)
    if row.fight_date != today:
        row.fight_date = today
        row.fight_count = 0
    row.fight_count += 1

    p = _success_rate(user, boss)
    win = random.random() < p
    # 娱乐技能交锋（v2.11.62）：仅文字表述，无任何效果；前后段引用技能名保持连贯
    from . import skills as _skills
    _my, _my_eff = _skills.title_skill(getattr(user, "profession", "") or "", getattr(user, "tier", 0) or 0)
    _his, _his_eff = _skills.boss_skill(boss.get("tag", ""))
    _clash = (f"💥 你使出【{_my}】，{_my_eff}！\n"
              f"👹 {boss['name']} 发动【{_his}】，{_his_eff}！")
    if not win:
        db.session.commit()
        return (f"⚔️ 挑战 {boss['name']}…胜率 {p * 100:.0f}%\n"
                f"{_clash}\n"
                f"💀 对手的【{_his}】更胜一筹，你的【{_my}】被化解，惜败！\n"
                f"今日「{subject}」剩余挑战次数 {max(0, limit - used - 1)}/{limit}。"), False

    # —— 胜利结算 ——
    lines = [f"⚔️ 挑战 {boss['name']}…胜率 {p * 100:.0f}%", _clash,
             f"🎉 你的【{_my}】压倒对手，击败了 {boss['name']}！"]
    first_clear = not row.first_clear_date
    if first_clear:
        row.first_clear_date = today
    wins = (row.total_wins or 0) + 1  # 本次成功后累计次数（用于衰减；新建行未 flush 时 total_wins 可能为 None）
    row.total_wins = wins

    # 铜币（永久递减）
    bonus = _coin_bonus(int(boss.get("layer", 100)), wins - 1 if not first_clear else 0)
    user.copper += bonus
    lines.append(f"💰 +{bonus} 铜币（本 Boss 累计成功 {wins} 次）")

    # 矿石（素材掉落 v2.11.63）：Boss 类型掷数量、每颗独立判档判种，按种类合并计数显示
    _bt = "major" if g == "big" else "minor"
    _ore_ids = dungeon._roll_boss_ore(int(boss.get("layer", 100)), _bt)
    if _ore_ids:
        from .ore import grant_ores, ore_meta
        _cnt = {}
        for _oid in _ore_ids:
            _cnt[_oid] = _cnt.get(_oid, 0) + 1
        grant_ores(user.user_id, _cnt)
        _parts = []
        for _oid, _c in sorted(_cnt.items(), key=lambda kv: -kv[1]):
            _om = ore_meta(_oid)
            _parts.append(f"{_om['name'] if _om else _oid} ×{_c}")
        lines.append(f"⛏️ 矿石：{'、'.join(_parts)}")

    # Boss 材料：首通必出；重复成功固定 60%（不随次数递减）
    if first_clear or random.random() < REPEAT_DROP_RATE:
        material.grant_materials(user.user_id, {boss["material"]: 1})
        mm = material.material_meta(boss["material"])
        mname = mm["name"] if mm else boss["material"]
        lines.append(("🎁 首通奖励 " if first_clear else "🎁 ") + f"Boss 材料：{mname} ×1")

    # 稀有装备（永久递减；低级 Boss 只掉低级稀有）
    rare = _roll_rare(user, boss, wins - 1 if not first_clear else 0)
    if rare:
        from .classes import TYPE_NAMES as _tn
        tn = _tn.get(rare.get("type", "other"), rare.get("type", ""))
        from .models import UserItem
        db.session.add(UserItem(user_id=user.user_id, item_id=rare["id"], is_new=1))
        lines.append(f"✦ {rare['name']}({tn}·稀有) new！")

    # 四大 Boss 专属特殊道具（v2.12.4）：1000/2000/3000/3600 各自专属、收藏级，极低掉率（SOUVENIR_DROP_RATE）
    _sou = SOUVENIR_LAYERS.get(int(boss.get("layer", 0) or 0))
    if _sou and random.random() < SOUVENIR_DROP_RATE:
        material.grant_materials(user.user_id, {_sou: 1})
        _rmeta = material.material_meta(_sou)
        lines.append(f"🌟 专属特殊道具：{_rmeta['name'] if _rmeta else _sou} ×1")

    # 究极大 Boss(1000/2000/3000/3600) 额外必掉「万宝源晶」（万宝符原料；3600 与万瓜圣契并列额外）
    if int(boss.get("layer", 0) or 0) in BIG_BOSS_LAYERS:
        material.grant_materials(user.user_id, {MYRIAD_GEM: 1})
        mmeta = material.material_meta(MYRIAD_GEM)
        lines.append(f"💎 大 Boss 专属材料：{mmeta['name'] if mmeta else MYRIAD_GEM} ×1")

    # 命名 Boss 专属装备（4 大 Boss 各 1 件：独特命名、全服限量、低掉率 5%、属性略高于同级锻造）
    from .boss_gear import roll_gear
    hit = roll_gear(user, int(boss.get("layer", 0) or 0))
    if hit:
        g = hit["gear"]
        lines.append(f"👑 全服限定掉落：{g['name']} new！"
                     f"（已产出 {hit['produced']}/{g['limit']} 件）")

    lines.append(f"今日「{subject}」剩余挑战次数 {max(0, limit - used - 1)}/{limit}。")
    db.session.commit()
    return "\n".join(lines), True


def boss_list_text(user):
    """/boss 列表：按分组（普通守关 / 大 Boss）展示命名 Boss（未解锁标锁）。"""
    today = _today()
    best = dungeon.historical_best_layer(user)
    rows = {}
    for b in all_bosses():
        st = db.session.execute(
            db.select(UserBoss).where(UserBoss.user_id == user.user_id,
                                      UserBoss.boss_id == b["id"])
        ).scalars().first()
        rows[b["id"]] = st
    groups = {}
    for b in all_bosses():
        groups.setdefault(boss_group(b), []).append(b)
    out = ["🎯 Boss 挑战（守关 Boss 每日共 3 次 · 大 Boss 1000/2000/3000/3600 每日各 1 次）",
           "   首通必出 Boss 材料 · 铜币/稀有永久递减 · 胜败均扣次数"]
    for g in ("normal", "big"):
        members = groups.get(g, [])
        if not members:
            continue
        limit = GROUP_DAILY_LIMIT[g]
        if g == "normal":
            used = group_used_today(user, members[0])
            remain = max(0, limit - used)
            out.append(f"—— 守关 Boss（今日剩 {remain}/{limit}）——")
        else:
            out.append("—— 大 Boss（1000/2000/3000/3600 · 每日各 1 次）——")
        for b in members:
            st = rows[b["id"]]
            unlocked = best >= int(b.get("layer", 100))
            mm = material.material_meta(b["material"])
            mname = mm["name"] if mm else b["material"]
            first = "已首通" if (st and st.first_clear_date) else "未首通"
            suffix = ""
            if unlocked and g == "big":
                remain_b = max(0, limit - group_used_today(user, b))
                suffix = f" 剩 {remain_b}/{limit}"
            # 专属掉落标注（boss_gear.json）：装备名（部位·职业）
            from .boss_gear import gear_for_layer
            gear = gear_for_layer(int(b.get("layer", 0) or 0))
            gear_txt = ""
            if gear:
                _gt = {"weapon": "主手", "staff": "法杖", "shield": "盾", "focus": "法器",
                       "armor": "战甲", "robe": "法袍", "accessory": "饰品"}
                _gl = {"physical": "战", "magic": "法", "any": "通用"}
                parts = []
                for gd in gear:
                    parts.append(f"{gd['name']}({_gl.get(gd['line'], '')}·{_gt.get(gd['type'], gd['type'])})")
                gear_txt = f"｜👑 {'/'.join(parts)}"
            if not unlocked:
                out.append(f"🔒 {b['layer']}层 {b['name']}（{b.get('tag','')}）需历史最高层 ≥ {b['layer']}{gear_txt}")
            else:
                out.append(f"· {b['layer']}层 {b['name']}（{b.get('tag','')}）[{first}] · 材料：{mname}{suffix}{gear_txt}")
    out.append("—— 用法：/挑战 <Boss名|层数|称号>（如 /挑战 裂风狼王 / 挑战 1000层）；/挑战 列表 查看 ——")
    return "\n".join(out)


# —— 专属装备查询（/挑战 装备 [名称]，v2.12.12）——
_GEAR_TYPE_CN = {"weapon": "主手", "staff": "法杖", "shield": "盾", "focus": "法器",
                 "armor": "战甲", "robe": "法袍", "accessory": "饰品"}
_GEAR_LINE_CN = {"physical": "战", "magic": "法", "any": "通用"}


def _gear_attrs_txt(g):
    parts = []
    for k, cn in (("attack", "攻"), ("mp", "魔"), ("agility", "敏"),
                  ("intelligence", "智"), ("hp", "命"), ("defense", "防")):
        v = int(g.get(k, 0) or 0)
        if v:
            parts.append(f"{cn}{v}")
    return " ".join(parts)


def gear_list_text(user=None, gear_name=None):
    """/挑战 装备 [名称]：查询命名 Boss 专属装备的属性与全服余量。

    无名称 → 全部 35 件按 4 大 Boss / 其他命名 Boss 分组列出（属性 + 评分 + 余量）；
    带名称 → 单件详情（含所属 Boss、掉率）。
    """
    from .boss_gear import load_gear
    from .models import UserItem
    gears = load_gear()
    counts = dict(db.session.execute(
        db.select(UserItem.item_id, db.func.count()).group_by(UserItem.item_id)
    ).all())
    layer_name = {int(b.get("layer", 0) or 0): b["name"] for b in all_bosses()}

    def remain_txt(g):
        produced = counts.get(g["id"], 0)
        return f"{max(0, int(g.get('limit', 5)) - produced)}/{g.get('limit')}"

    if gear_name:
        key = gear_name.strip()
        g = next((x for x in gears
                  if x.get("name") == key or x.get("id", "").lower() == key.lower()), None)
        if not g:
            return f"没有找到专属装备「{gear_name}」，发送 /挑战 装备 查看全部"
        layer = int(g.get("boss_layer", 0) or 0)
        score = round(dungeon.item_score(g), 1)
        rate = float(g.get("rate", 0.03)) * 100
        return "\n".join([
            f"👑 {g['name']}（{layer_name.get(layer, '?')} 专属 · "
            f"{_GEAR_LINE_CN.get(g.get('line', ''), '')}·{_GEAR_TYPE_CN.get(g.get('type', ''), g.get('type', ''))}）",
            f"  {_gear_attrs_txt(g)}",
            f"  评分 {score} · 全服余量 {remain_txt(g)} · 掉率 {rate:.1f}% · 掉落层 {g.get('boss_layer')}",
        ])

    big = set(BIG_BOSS_LAYERS)
    by_layer = {}
    for g in gears:
        by_layer.setdefault(int(g.get("boss_layer", 0) or 0), []).append(g)
    out = ["👑 命名 Boss 专属装备（全服限量 · 掉率极低）"]
    for layer in sorted(by_layer):
        boss_name = layer_name.get(layer, f"第 {layer} 层")
        rate = 0.1 if layer in big else 3
        out.append(f"—— {boss_name}（{layer} 层 · 掉率 {rate:.1f}%）——")
        for g in by_layer[layer]:
            out.append(f"· {g['name']}（{_GEAR_LINE_CN.get(g.get('line', ''), '')}·"
                       f"{_GEAR_TYPE_CN.get(g.get('type', ''), g.get('type', ''))}）"
                       f"{_gear_attrs_txt(g)} 评分{round(dungeon.item_score(g), 1)} 余量{remain_txt(g)}")
    out.append("—— /挑战 装备 <名称> 查单件详情 ——")
    return "\n".join(out)
