# -*- coding: utf-8 -*-
"""职业系统：职业注册表（职业 → line / 显示名 / 可用部位）。

设计（见 docs/equipment-system-redo-plan.md §3.1）：
- 职业决定玩家能穿哪一条「line」（物理/魔法）的装备，不能混搭。
- 未来新增职业只需在此 CLASSES 注册表登记即可。
"""
import os

# line 常量
LINE_PHYSICAL = "physical"   # 物理线（战士）
LINE_MAGIC = "magic"         # 魔法线（法师/魔法师）
LINE_ANY = "any"             # 通用（饰品/纯生存轻甲，两职业皆可）

# 职业注册表：职业标识 → {名称, 别名列表, line, 可用 type 集合, 描述}
# 可用 type：决定该职业可购买/装备/穿戴哪些部位（防混搭 + 购买限制）。
CLASSES = {
    "warrior": {
        "name": "战士",
        "aliases": ["战士", "warrior", "zhan", "zhan shi"],
        "line": LINE_PHYSICAL,
        "types": {"weapon", "armor", "shield"},
        "desc": "物理近战（攻击/敏捷）",
    },
    "mage": {
        "name": "魔法师",
        "aliases": ["魔法师", "法师", "mage", "fashi", "fa shi"],
        "line": LINE_MAGIC,
        "types": {"staff", "robe", "focus"},
        "desc": "魔法施法（智力/魔力）",
    },
}

# 部位 → 归属 line（用于旧数据/兜底推断）
TYPE_LINE_FALLBACK = {
    "weapon": LINE_PHYSICAL,
    "armor": LINE_ANY,        # 纯生存轻甲 → any（通用）
    "shield": LINE_PHYSICAL,
    "staff": LINE_MAGIC,
    "robe": LINE_MAGIC,
    "focus": LINE_MAGIC,
    "accessory": LINE_ANY,
}

# type 显示名（与 commands.TYPE_NAMES 保持一致；避免循环 import，这里放兜底映射）
TYPE_NAMES = {
    "weapon": "武器", "shield": "盾牌", "armor": "防具",
    "robe": "法袍", "accessory": "饰品", "staff": "法杖", "focus": "法器",
    "other": "其他",
}


def _norm(key):
    """归一化用户输入，用于匹配职业名/别名。"""
    return (key or "").strip().lower().replace(" ", "")


def find_class(name_or_alias):
    """按职业名/别名精确找职业标识（如 战士/warrior/法师/魔法师），找不到返回 None。"""
    key = _norm(name_or_alias)
    if not key:
        return None
    for cid, meta in CLASSES.items():
        if _norm(meta["name"]) == key or cid.lower() == key:
            return cid
        for a in meta.get("aliases", []):
            if _norm(a) == key:
                return cid
    return None


def class_meta(class_id):
    """按职业标识返回元信息；未知返回 None。"""
    return CLASSES.get(class_id)


def class_name(class_id):
    """职业显示名；未转职返回空串。"""
    meta = CLASSES.get(class_id)
    return meta["name"] if meta else ""


def class_line(class_id):
    """职业对应 line；未知返回 None。"""
    meta = CLASSES.get(class_id)
    return meta["line"] if meta else None


def class_types(class_id):
    """职业可用的 type 集合（不包含 accessory/通用件，调用处自行并入 any）。"""
    meta = CLASSES.get(class_id)
    return set(meta["types"]) if meta else set()


def all_classes():
    """全部职业列表：[{id,name,line,desc}]。"""
    return [{"id": cid, "name": m["name"], "line": m["line"], "desc": m["desc"]}
            for cid, m in CLASSES.items()]


def item_line(item):
    """装备的 line：优先取字段 line；无则按 type 兜底推断（旧数据兼容）。"""
    it = item if isinstance(item, dict) else {}
    line = it.get("line")
    if line in (LINE_PHYSICAL, LINE_MAGIC, LINE_ANY):
        return line
    return TYPE_LINE_FALLBACK.get(it.get("type", "other"), LINE_ANY)


def item_usable_for(item, class_id):
    """装备是否对某职业可用：line=any(通用) 或 line=该职业的 line。"""
    line = item_line(item)
    if line == LINE_ANY:
        return True
    cl = class_line(class_id)
    return bool(cl) and line == cl
