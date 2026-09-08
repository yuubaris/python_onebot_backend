# -*- coding: utf-8 -*-
"""地下城稀有矿石模块（400 层以上，进入时获得资格）。

掉落规则（每 15 分钟一个结算周期，掷骰一次）：
- 普通档 30%（铜矿石/铁矿石 各 50%）
- 稀有档 8% （银矿石/秘银矿石 各 50%）
- 传说档 2% × (层数/400)^0.5，按当时层数，上限 30%（金矿石/龙晶石 各 50%）
- 其余为无掉落；普通/稀有概率固定不变，层数加成只作用于传说档。

矿石数据以 JSON 文件（ores.json）保存在项目目录中，方便维护；
数据库（user_ore 表）只记录用户持有矿石 id 与数量。
"""
import json
import os
import random

ORES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ores.json")

# 结算周期与门槛
ORE_CYCLE_SECONDS = 15 * 60          # 每 15 分钟一个结算周期
ORE_MIN_LAYER = 400                  # 进入时当前层数 > 400 才可获得矿石
ORE_LEGEND_BASE = 0.02               # 传说档基础概率 2%
ORE_LEGEND_CAP = 0.30                # 传说档概率上限 30%
ORE_RARE_PCT = 0.08                  # 稀有档固定 8%
ORE_COMMON_PCT = 0.30                # 普通档固定 30%

_cache = {"mtime": None, "ores": [], "by_id": {}, "by_rarity": {}}


def load_ores(force=False):
    """读取矿石列表（带 mtime 缓存，修改 JSON 后自动生效）。"""
    mtime = os.path.getmtime(ORES_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["ores"]
    with open(ORES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    ores = data.get("ores", [])
    _cache["mtime"] = mtime
    _cache["ores"] = ores
    _cache["by_id"] = {o["id"]: o for o in ores}
    by_rarity = {}
    for o in ores:
        by_rarity.setdefault(o.get("rarity", "common"), []).append(o["id"])
    _cache["by_rarity"] = by_rarity
    return ores


def legend_probability(layer):
    """传说档概率 = 2% × (层数/400)^0.5，上限 30%。"""
    if layer <= ORE_MIN_LAYER:
        return 0.0
    p = ORE_LEGEND_BASE * ((layer / ORE_MIN_LAYER) ** 0.5)
    return min(p, ORE_LEGEND_CAP)


def roll_ore(layer, rand=random.random):
    """对一个 15 分钟周期掷骰一次：返回掉落的矿石 id；未掉落返回 None。

    layer 为该周期结算时的当前层数（<400 层不参与掷骰，直接返回 None）。
    """
    if layer < ORE_MIN_LAYER:
        return None
    load_ores()
    p_legend = legend_probability(layer)
    r = rand()
    if r < p_legend:
        return random.choice(_cache["by_rarity"].get("legendary", [])) or None
    if r < p_legend + ORE_RARE_PCT:
        return random.choice(_cache["by_rarity"].get("rare", [])) or None
    if r < p_legend + ORE_RARE_PCT + ORE_COMMON_PCT:
        return random.choice(_cache["by_rarity"].get("common", [])) or None
    return None


def grant_ores(user_id, gained):
    """批量累加用户矿石持有量（gained: {ore_id: count}）。

    调用方需处于应用上下文内；本函数只做数据库操作，提交由调用方负责。
    """
    if not gained:
        return
    from models import db, UserOre
    for ore_id, cnt in gained.items():
        row = db.session.execute(
            db.select(UserOre).where(
                UserOre.user_id == user_id, UserOre.ore_id == ore_id
            )
        ).scalars().first()
        if row is None:
            db.session.add(UserOre(user_id=user_id, ore_id=ore_id, count=cnt))
        else:
            row.count += cnt


def ore_meta(ore_id):
    """按矿石 id 返回矿石元信息（含 name/rarity），未知返回 None。"""
    load_ores()
    return _cache["by_id"].get(ore_id)


def consume_ores(user_id, needed):
    """按 needed({ore_id: count}) 扣减用户矿石；不足时返回缺失清单 {ore_id: (need, have)}，成功返回 None。

    调用方需处于应用上下文内；本函数只做数据库操作，提交由调用方负责。
    """
    if not needed:
        return None
    from models import db, UserOre
    ids = list(needed)
    rows = {r.ore_id: r for r in db.session.execute(
        db.select(UserOre).where(UserOre.user_id == user_id, UserOre.ore_id.in_(ids))
    ).scalars().all()}
    missing = {}
    for oid, need in needed.items():
        have = rows[oid].count if oid in rows else 0
        if have < need:
            missing[oid] = (need, have)
    if missing:
        return missing
    for oid, need in needed.items():
        row = rows[oid]
        row.count -= need
        if row.count <= 0:
            db.session.delete(row)
    return None


def owned_ores(user_id):
    """查询用户持有的矿石列表：[{id, name, rarity, count}]，按稀有度降序。"""
    from models import db, UserOre
    load_ores()
    rows = db.session.execute(
        db.select(UserOre).where(UserOre.user_id == user_id, UserOre.count > 0)
    ).scalars().all()
    out = []
    for r in rows:
        meta = _cache["by_id"].get(r.ore_id)
        if meta is None:
            continue
        out.append({"id": r.ore_id, "name": meta["name"],
                    "rarity": meta.get("rarity", "common"), "count": r.count})
    _rank = {"legendary": 0, "rare": 1, "common": 2}
    out.sort(key=lambda o: (_rank.get(o["rarity"], 9), o["id"]))
    return out
