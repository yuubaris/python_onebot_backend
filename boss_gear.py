# -*- coding: utf-8 -*-
"""命名 Boss 专属装备模块（/boss 挑战胜利时概率掉落）。

规则（2026-09-09 v2.11.49）：
- 仅 4 个大 Boss（1000/2000/3000/3600）各 1 件专属装备，独特命名；
- 属性 = 同级锻造装备 ×1.1（略高）；tier 与同级锻造一致（1000→T5、其余→T6）；
- 全服限量：每件全服最多产出 limit 件（查 user_item 全局计数，达到上限不再掉落）；
- 掉率低：每件默认 5%（rate 字段），每次挑战胜利独立判定；
- 专属装备不可出售（与锻造装一致，收藏向）。
"""
import json
import os
import random

from models import db, UserItem

GEAR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boss_gear.json")

_cache = {"mtime": None, "items": [], "by_layer": {}}


def load_gear(force=False):
    """读取命名 Boss 专属装备表（带 mtime 缓存）。"""
    mtime = os.path.getmtime(GEAR_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["items"]
    with open(GEAR_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    _cache["mtime"] = mtime
    _cache["items"] = items
    _cache["by_layer"] = {int(g.get("boss_layer", 0)): g for g in items}
    return items


def gear_for_layer(layer):
    """按 Boss 层数返回专属装备定义（无则 None）。"""
    load_gear()
    return _cache["by_layer"].get(int(layer or 0))


def gear_ids():
    """全部专属装备 id 集合（供背包标注 / 出售拦截）。"""
    return {g["id"] for g in load_gear()}


def is_gear(item_id):
    """是否为命名 Boss 专属装备。"""
    return item_id in gear_ids()


def produced_count(item_id):
    """全服已产出数量（user_item 全局计数）。"""
    return db.session.execute(
        db.select(db.func.count()).select_from(UserItem).where(
            UserItem.item_id == item_id
        )
    ).scalar_one()


def roll_gear(user, layer):
    """挑战胜利时判定专属装备掉落：限量内 + 低概率 → 入包。

    返回装备 dict（含已产出/限量用于播报），未掉落返回 None。
    """
    g = gear_for_layer(layer)
    if not g:
        return None
    produced = produced_count(g["id"])
    if produced >= int(g.get("limit", 3)):
        return None
    if random.random() >= float(g.get("rate", 0.05)):
        return None
    db.session.add(UserItem(user_id=user.user_id, item_id=g["id"], is_new=1))
    return {"gear": g, "produced": produced + 1}
