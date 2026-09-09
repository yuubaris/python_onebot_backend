# -*- coding: utf-8 -*-
"""铁匠铺锻造模块（按配方 Lv 分级解锁：Lv1@400 层 → Lv4@3200 层）。

锻造是商店（武器库）毕业线之后的进阶装备来源：Lv1 强度 ≈ 商店 T4.5，Lv2~Lv4 依次更高，
供 T5/T6/T7 玩家追装。配方消耗「铜币 + 矿石」，数据以 JSON（forge.json）维护；
数据库（user_item 表）只记录锻造产物的装备 id，完整属性统一从 JSON 读取。
"""
import json
import os

FORGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forge.json")
# 铁匠铺分级解锁层（对齐阶级层门槛 T3~T6）：历史最高层 ≥ 对应层 即解锁该 Lv 配方。
# Lv1@400(T3) / Lv2@800(T4) / Lv3@1600(T5) / Lv4@3200(T6)
FORGE_LV_LAYER = {1: 400, 2: 800, 3: 1600, 4: 3200}
FORGE_MIN_LAYER = FORGE_LV_LAYER[1]  # 兼容旧引用：最低解锁层（Lv1）

_cache = {"mtime": None, "recipes": [], "by_id": {}, "by_name": {}}


def unlocked_levels(layer):
    """给定历史最高层，返回已解锁的锻造 Lv 集合。"""
    return {lv for lv, need in FORGE_LV_LAYER.items() if (layer or 0) >= need}


def level_unlocked(layer, lv):
    """指定锻造 Lv 是否已按历史最高层解锁。"""
    return (layer or 0) >= FORGE_LV_LAYER.get(lv, 10 ** 9)


def load_forges(force=False):
    """读取铁匠铺配方列表（带 mtime 缓存，修改 JSON 后自动生效）。"""
    mtime = os.path.getmtime(FORGE_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["recipes"]
    with open(FORGE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    recipes = data.get("recipes", [])
    _cache["mtime"] = mtime
    _cache["recipes"] = recipes
    _cache["by_id"] = {r["id"]: r for r in recipes}
    _cache["by_name"] = {r["name"]: r for r in recipes}
    return recipes


def find_forge(name_or_id):
    """按配方名称或 id 精确查找，找不到返回 None；名称兼容带/不带“锻造”前缀（如 /锻造 断岳重剑）。"""
    load_forges()
    key = (name_or_id or "").strip()
    if not key:
        return None
    if key in _cache["by_name"]:
        return _cache["by_name"][key]
    if key in _cache["by_id"]:
        return _cache["by_id"][key]
    low = key.lower()
    for iid, r in _cache["by_id"].items():
        if iid.lower() == low:
            return r
    for name, r in _cache["by_name"].items():
        if name.lower() == low:
            return r
    # 兼容带/不带“锻造”前缀的输入（如 /锻造 锻造·断岳重剑 与 /锻造 断岳重剑）
    for name, r in _cache["by_name"].items():
        short = name
        for pre in ("锻造·", "锻造"):
            if name.startswith(pre):
                short = name[len(pre):]
                break
        if key in name or name in key or key == short or low == short.lower():
            return r
    return None


def suggest_forges(keyword, limit=5):
    """按名称/id 包含关系给出建议（用于“未找到”时的提示）。"""
    load_forges()
    kw = (keyword or "").strip()
    if not kw:
        return []
    hits = [r for r in _cache["recipes"] if kw in r["name"] or kw in r["id"]]
    return hits[:limit]


_ATTR_ORDER = [
    ("attack", "攻"),
    ("mp", "魔"),
    ("agility", "敏"),
    ("intelligence", "智"),
    ("hp", "命"),
    ("defense", "防"),
]


def _attr_desc(r):
    """配方装备属性文本（如：攻67 敏18 命35 防14），只列非零项。"""
    parts = []
    for key, label in _ATTR_ORDER:
        val = r.get(key)
        if val:
            parts.append(f"{label}{val}")
    return " ".join(parts)


def recommend_text(user):
    """/锻造 推荐：按背包矿石/铜币计算可锻造的配方及数量。

    - 只列当前职业可用的配方（physical/magic 匹配，any 通用）；
    - 可锻造数 = min(各矿石持有÷需求、铜币÷价格) 向下取整；
    - 按锻造 Lv 分组排列，组内已解锁在前、未解锁（🔒，材料已够只差层数）在后；
    - 每条展示装备属性与材料/铜币消耗；矿石不够的配方不列出。
    """
    load_forges()
    from .dungeon import historical_best_layer
    from . import classes as _cls
    ores = {o["id"]: o["count"] for o in _load_ores_owned(user.user_id)}
    prof = (user.profession or "") or ""
    my_line = _cls.class_line(prof)
    best = historical_best_layer(user)

    ok = []
    for r in load_forges():
        line = r.get("line") or _cls.item_line(r)
        if my_line and line != _cls.LINE_ANY and line != my_line:
            continue  # 非本职业配方不推荐
        cost = r.get("cost") or {}
        price = int(r.get("price", 0) or 0)
        can = None
        for oid, n in cost.items():
            c = ores.get(oid, 0) // n
            can = c if can is None else min(can, c)
        if price:
            c = user.copper // price
            can = c if can is None else min(can, c)
        if can is None or can < 1:
            continue
        lv = int(r.get("level", 1))
        locked = best < FORGE_LV_LAYER.get(lv, 10 ** 9)
        ok.append((lv, r, can, locked))
    if not ok:
        return ("当前矿石与铜币不足以锻造任何装备。\n"
                "发送 /铁匠铺 查看配方及需求。")
    # 按等级分组；组内已解锁在前、未解锁在后，同状态按价格升序
    ok.sort(key=lambda x: (x[0], x[3], x[1].get("price", 0)))
    lines = ["🔨 锻造推荐（按当前背包可制作）："]
    cur_lv = None
    for lv, r, can, locked in ok:
        if lv != cur_lv:
            cur_lv = lv
            if best >= FORGE_LV_LAYER.get(lv, 10 ** 9):
                head = f"—— Lv{lv}（已解锁）——"
            else:
                head = f"—— Lv{lv}（🔒 需到第 {FORGE_LV_LAYER.get(lv)} 层）——"
            lines.append(head)
        attrs = _attr_desc(r)
        line = (f"· {r['name']}（{_cls.TYPE_NAMES.get(r.get('type'), r.get('type', ''))}）×{can}"
                f" —— {attrs} —— {format_cost(r.get('cost', {}))} + {price_txt(r.get('price', 0))}")
        if locked:
            line += " 🔒"
        lines.append(line)
    lines.append("—— 用法：/锻造 <装备名> 制作；/铁匠铺 查看全部配方 ——")
    return "\n".join(lines)


def _load_ores_owned(user_id):
    """查询用户持有的矿石（延迟导入 ore，避免循环）。"""
    from .ore import owned_ores
    return owned_ores(user_id)


def price_txt(copper):
    from .currency import format_currency
    return format_currency(int(copper or 0))


def all_forges():
    """返回全部配方（按等级、价格排序）。"""
    recs = load_forges()
    return sorted(recs, key=lambda r: (r.get("level", 9), r.get("price", 0)))


def format_cost(cost):
    """把配方矿石消耗 {ore_id: count} 转成可读文本（如：铜矿石×70 铁矿石×70）。"""
    if not cost:
        return ""
    from .ore import load_ores, ore_meta
    load_ores()
    parts = []
    for oid in sorted(cost, key=lambda x: _ore_sort(x)):
        meta = ore_meta(oid)
        name = meta["name"] if meta else oid
        parts.append(f"{name}×{cost[oid]}")
    return " ".join(parts)


def _ore_sort(ore_id):
    """矿石展示排序：神话 → 传说 → 稀有 → 普通（与 /背包 前缀一致，神话最高）。"""
    from .ore import ore_meta
    meta = ore_meta(ore_id)
    rank = {"myth": -1, "legendary": 0, "rare": 1, "common": 2}
    return (rank.get((meta or {}).get("rarity", "common"), 9), ore_id)


def is_forged_item(item_id):
    """判断某装备 id 是否来自铁匠铺配方。"""
    load_forges()
    return item_id in _cache["by_id"]
