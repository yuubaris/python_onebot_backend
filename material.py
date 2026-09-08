# -*- coding: utf-8 -*-
"""地下城材料模块（草药 / 特殊物品；boss 材料由 boss.py 挑战产出）。

材料数据在 materials.json（id/name/category{herb,special,boss}/rarity），
数据库 user_material 表只记录持有 id 与数量（镜像 ore.py）。

掉落（与矿石平行、互不抢占）：
- 草药 herb + 特殊物品 special：普通层周期结算（入口层 ≥150，15 分钟一周期）——
  两者**同池同概率**（普通 35% / 稀有 8% / 传说 2%×√(层/150)≤20% / 神话 0.1%×√(层/150)≤1%），
  命中档位后再在该档的草药+特殊材料中随机，保证同级配方成本期望一致；
  **神话档掉落「万宝源晶」**（神话级通用材料，与大 Boss 挑战必掉共享来源，非 3600 专属材料）；
- 特殊物品另有 Boss 关结算额外 roll（精英 25% / 小Boss 45% / 大Boss 80%，大 Boss 高稀有占比）；
- boss 材料 boss：由 /boss 挑战 + 自动推进首通产出（见 boss.py）。
"""
import json
import os

MATERIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "materials.json")

HERB_MIN_LAYER = 150          # 周期材料资格入口层
HERB_CYCLE_SECONDS = 15 * 60  # 与矿石同周期
HERB_COMMON_PCT = 0.35        # 普通 35%
HERB_RARE_PCT = 0.08          # 稀有 8%
HERB_LEGEND_BASE = 0.02       # 传说基础 2%
HERB_LEGEND_CAP = 0.20        # 传说上限 20%
HERB_MYTH_BASE = 0.001        # 神话基础 0.1%
HERB_MYTH_CAP = 0.01          # 神话上限 1%

# 神话档掉落：万宝源晶（神话级通用材料；大Boss挑战必掉 + 周期神话档稀有掉落）
MYTH_DROP_ID = "boss_myriad"

# Boss 关特殊物品掉落概率
SPECIAL_BOSS_PCT = {"elite": 0.25, "minor": 0.45, "major": 0.80}

_cache = {"mtime": None, "materials": [], "by_id": {}, "by_category": {}}


def load_materials(force=False):
    """读取材料列表（带 mtime 缓存）。"""
    mtime = os.path.getmtime(MATERIALS_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["materials"]
    with open(MATERIALS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    materials = data.get("materials", [])
    _cache["mtime"] = mtime
    _cache["materials"] = materials
    _cache["by_id"] = {m["id"]: m for m in materials}
    by_cat = {}
    for m in materials:
        by_cat.setdefault(m.get("category", "herb"), []).append(m["id"])
    _cache["by_category"] = by_cat
    return materials


def herb_legend_probability(layer):
    """传说草药概率 = 2% × √(层/150)，上限 20%（150 层起即有基础 2%，与资格边界一致）。"""
    if layer < HERB_MIN_LAYER:
        return 0.0
    p = HERB_LEGEND_BASE * ((layer / HERB_MIN_LAYER) ** 0.5)
    return min(p, HERB_LEGEND_CAP)


def herb_myth_probability(layer):
    """神话草药概率 = 0.1% × √(层/150)，上限 1%（150 层起即有基础 0.1%，与资格边界一致）。"""
    if layer < HERB_MIN_LAYER:
        return 0.0
    p = HERB_MYTH_BASE * ((layer / HERB_MIN_LAYER) ** 0.5)
    return min(p, HERB_MYTH_CAP)


def roll_material(layer, rand=None):
    """普通层周期掷周期材料（草药+特殊同池同概率）：返回材料 id；未掉落返回 None（<150 层直接 None）。"""
    import random
    if layer < HERB_MIN_LAYER:
        return None
    load_materials()
    rand = rand or random.random
    pool = (_cache["by_category"].get("herb", []) +
            _cache["by_category"].get("special", []))
    if not pool:
        return None
    r = rand()
    if r < herb_myth_probability(layer):
        return MYTH_DROP_ID if _cache["by_id"].get(MYTH_DROP_ID) else (random.choice(pool) if pool else None)
    if r < herb_myth_probability(layer) + herb_legend_probability(layer):
        return _pick_rarity_material("legendary") or random.choice(pool)
    if r < herb_myth_probability(layer) + herb_legend_probability(layer) + HERB_RARE_PCT:
        return _pick_rarity_material("rare") or random.choice(pool)
    if r < herb_myth_probability(layer) + herb_legend_probability(layer) + HERB_RARE_PCT + HERB_COMMON_PCT:
        return _pick_rarity_material("common") or random.choice(pool)
    return None


# 兼容旧名（调用方均已改为 roll_material）
roll_herb = roll_material


def _pick_rarity_material(rarity):
    """从指定稀有度的周期材料池（草药+特殊）随机抽一个 id；无则返回 None。"""
    import random
    mats = [m["id"] for m in _cache["materials"]
            if m.get("category") in ("herb", "special") and m.get("rarity") == rarity]
    return random.choice(mats) if mats else None


def roll_special(btype, rand=None):
    """Boss 关结算掷特殊物品：命中返回 special 材料 id；未命中返回 None。"""
    import random
    load_materials()
    rand = rand or random.random
    p = SPECIAL_BOSS_PCT.get(btype, 0.25)
    if rand() >= p:
        return None
    pool = _cache["by_category"].get("special", [])
    if not pool:
        return None
    # 大 Boss 偏向高稀有（传说优先，其次稀有/普通），精英/小Boss 均匀或偏普通
    if btype == "major":
        weighted = []
        for mid in pool:
            meta = material_meta(mid)
            w = {"legendary": 5, "rare": 3, "common": 1}.get((meta or {}).get("rarity", "common"), 1)
            weighted.extend([mid] * w)
        return random.choice(weighted) if weighted else None
    return random.choice(pool)


def grant_materials(user_id, gained):
    """批量累加材料持有（gained: {material_id: count}）；提交由调用方负责。"""
    if not gained:
        return
    from models import db, UserMaterial
    for mid, cnt in gained.items():
        row = db.session.execute(
            db.select(UserMaterial).where(
                UserMaterial.user_id == user_id, UserMaterial.material_id == mid
            )
        ).scalars().first()
        if row is None:
            db.session.add(UserMaterial(user_id=user_id, material_id=mid, count=cnt))
        else:
            row.count += cnt


def consume_materials(user_id, needed):
    """按 needed({material_id: count}) 扣减；不足返回缺失 {id: (need, have)}，成功返回 None。"""
    if not needed:
        return None
    from models import db, UserMaterial
    ids = list(needed)
    rows = {r.material_id: r for r in db.session.execute(
        db.select(UserMaterial).where(UserMaterial.user_id == user_id,
                                      UserMaterial.material_id.in_(ids))
    ).scalars().all()}
    missing = {}
    for mid, need in needed.items():
        have = rows[mid].count if mid in rows else 0
        if have < need:
            missing[mid] = (need, have)
    if missing:
        return missing
    for mid, need in needed.items():
        row = rows[mid]
        row.count -= need
        if row.count <= 0:
            db.session.delete(row)
    return None


def material_meta(material_id):
    """按材料 id 返回元信息；未知返回 None。"""
    load_materials()
    return _cache["by_id"].get(material_id)


def owned_materials(user_id, category=None):
    """查询用户持有材料：[{id,name,category,rarity,count}]（可按类别过滤）。"""
    from models import db, UserMaterial
    load_materials()
    rows = db.session.execute(
        db.select(UserMaterial).where(UserMaterial.user_id == user_id, UserMaterial.count > 0)
    ).scalars().all()
    out = []
    for r in rows:
        meta = _cache["by_id"].get(r.material_id)
        if meta is None:
            continue
        if category and meta.get("category") != category:
            continue
        out.append({"id": r.material_id, "name": meta["name"],
                    "category": meta.get("category", "herb"),
                    "rarity": meta.get("rarity", "common"), "count": r.count})
    _rank = {"myth": -1, "legendary": 0, "rare": 1, "common": 2}
    out.sort(key=lambda m: (_rank.get(m["rarity"], 9), m["category"], m["id"]))
    return out
