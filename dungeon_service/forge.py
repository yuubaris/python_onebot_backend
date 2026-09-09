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
