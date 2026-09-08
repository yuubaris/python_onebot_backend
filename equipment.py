# -*- coding: utf-8 -*-
"""装备数据加载模块。

装备数据以 JSON 文件（equipment.json）保存在项目目录中，方便直接维护；
数据库（user_item 表）只记录装备的 id，装备完整属性统一从 JSON 读取。
"""
import json
import os

EQUIPMENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equipment.json")

_cache = {"mtime": None, "items": [], "by_id": {}, "by_name": {}}


def load_equipment(force=False):
    """读取装备列表（带 mtime 缓存，修改 JSON 后自动生效）。"""
    mtime = os.path.getmtime(EQUIPMENT_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["items"]
    with open(EQUIPMENT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("equipment", [])
    _cache["mtime"] = mtime
    _cache["items"] = items
    _cache["by_id"] = {it["id"]: it for it in items}
    _cache["by_name"] = {it["name"]: it for it in items}
    return items


def find_item(name_or_id):
    """按装备名称或 id 精确查找，找不到返回 None。"""
    load_equipment()
    key = (name_or_id or "").strip()
    if not key:
        return None
    if key in _cache["by_name"]:
        return _cache["by_name"][key]
    if key in _cache["by_id"]:
        return _cache["by_id"][key]
    low = key.lower()
    for iid, it in _cache["by_id"].items():
        if iid.lower() == low:
            return it
    for name, it in _cache["by_name"].items():
        if name.lower() == low:
            return it
    return None


def suggest_items(keyword, limit=5):
    """按名称/id 包含关系给出建议（用于“未找到”时的提示）。"""
    load_equipment()
    kw = (keyword or "").strip()
    if not kw:
        return []
    hits = [it for it in _cache["items"] if kw in it["name"] or kw in it["id"]]
    return hits[:limit]
