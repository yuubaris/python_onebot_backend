# -*- coding: utf-8 -*-
"""炼金模块（/炼金 列表 · /炼金 <配方名>）。

配方在 alchemy_recipes.json：cost 可含 materials（草药/特殊/boss 材料）、
ores（矿石）、copper（铜币）；output 产出入 consumables.json 注册的药水/道具。

消耗校验：材料走 material.consume_materials、矿石走 ore.consume_ores、铜币即时扣减；
缺料逐项提示（仿 cmd_forge）。含 boss 材料的配方需「历史最高层 ≥ 该 Boss 层」防跳段。
"""
import json
import os
import re

from models import db, UserConsumable
import material
import ore
import consumable

RECIPES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alchemy_recipes.json")

_cache = {"mtime": None, "recipes": [], "by_id": {}, "by_name": {}}


def load_recipes(force=False):
    mtime = os.path.getmtime(RECIPES_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["recipes"]
    with open(RECIPES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    recipes = data.get("recipes", [])
    _cache["mtime"] = mtime
    _cache["recipes"] = recipes
    _cache["by_id"] = {r["id"]: r for r in recipes}
    _cache["by_name"] = {r["name"]: r for r in recipes}
    return recipes


def find_recipe(name_or_id):
    """按配方名/id 查找；兼容省略「炼金·」前缀。"""
    load_recipes()
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
    # 兼容省略“炼金·”前缀
    for name, r in _cache["by_name"].items():
        short = name
        for pre in ("炼金·", "炼金"):
            if name.startswith(pre):
                short = name[len(pre):]
                break
        if key in name or name in key or key == short or low == short.lower():
            return r
    # 归一化匹配：兼容「聚财符 1 / 聚财符1 / 聚财符Ⅰ / 聚财符 I / 聚财符一」等写法
    nk = consumable.normalize_name(key)
    # 用户侧可能带「炼金·/炼金 」前缀（去空格后为“炼金聚财符1”或“炼金·聚财符1”），先剥离再比
    nk_short = nk
    for pre in ("炼金·", "炼金"):
        if nk.startswith(pre):
            nk_short = nk[len(pre):]
            break
    for name, r in _cache["by_name"].items():
        if consumable.normalize_name(name) == nk:
            return r
    for name, r in _cache["by_name"].items():
        short = name
        for pre in ("炼金·", "炼金"):
            if name.startswith(pre):
                short = name[len(pre):]
                break
        if consumable.normalize_name(short) == nk or consumable.normalize_name(short) == nk_short:
            return r
    return None


def suggest_recipes(keyword, limit=5):
    load_recipes()
    kw = (keyword or "").strip()
    if not kw:
        return []
    kw2 = re.sub(r"\s+", "", kw)
    hits = [r for r in _cache["recipes"]
            if kw in r["name"] or kw in r["id"] or kw2 in re.sub(r"\s+", "", r["name"])]
    return hits[:limit]


def all_recipes():
    """全部配方（按 kind、level 排序）。"""
    recs = load_recipes()
    order = {"potion": 0, "tool": 1}
    return sorted(recs, key=lambda r: (order.get(r.get("kind", "tool"), 9), r.get("level", 9), r["name"]))


def format_cost(recipe):
    """把配方消耗转成可读文本（材料 + 矿石 + 铜币）。"""
    cost = recipe.get("cost") or {}
    parts = []
    for mid in sorted(cost.get("materials", {})):
        meta = material.material_meta(mid)
        parts.append(f"{(meta or {}).get('name', mid)}×{cost['materials'][mid]}")
    for oid in sorted(cost.get("ores", {})):
        meta = ore.ore_meta(oid)
        parts.append(f"{(meta or {}).get('name', oid)}×{cost['ores'][oid]}")
    if cost.get("copper"):
        from currency import format_currency
        parts.append(format_currency(cost["copper"]))
    return " ".join(parts) if parts else "无消耗"


def recipe_min_layer(recipe):
    """配方所需最低历史层：按产物等级近似（Lv1@100，之后每级 +400；🔧可调）。"""
    lv = int((recipe.get("level") or 1))
    return 100 + max(0, lv - 1) * 400


def forge_recipe(user, recipe):
    """执行炼金：校验 + 扣材料/矿石/铜币 + 产出入包。返回提示文本。"""
    from dungeon import historical_best_layer
    cost = recipe.get("cost") or {}
    need_mats = cost.get("materials", {})
    need_ores = cost.get("ores", {})
    need_copper = int(cost.get("copper", 0) or 0)
    out_id = (recipe.get("output") or {}).get("item_id", "")
    out_meta = consumable.consumable_meta(out_id)
    if out_meta is None:
        return f"配方产物缺失：{out_id}"

    # 解锁层校验（按产物等级）
    min_layer = recipe_min_layer(recipe)
    if historical_best_layer(user) < min_layer:
        return (f"🔒 炼金「{recipe['name']}」需地下城历史最高层 ≥ {min_layer} 层"
                f"（当前 {historical_best_layer(user)}）。")

    # 铜币校验
    if user.copper < need_copper:
        from currency import format_currency
        return (f"铜币不足，不能炼金！「{recipe['name']}」需 {format_currency(need_copper)}，"
                f"你只有 {format_currency(user.copper)}。")

    # 材料不足 → 缺啥提示啥
    missing = material.consume_materials(user.user_id, need_mats)
    if missing:
        lines = ["材料不足，不能炼金！"]
        for mid, (need, have) in missing.items():
            meta = material.material_meta(mid)
            lines.append(f"缺少 {(meta or {}).get('name', mid)}（需 {need}，现有 {have}）")
        return "\n".join(lines)
    # 矿石不足
    miss_ores = ore.consume_ores(user.user_id, need_ores)
    if miss_ores:
        material.grant_materials(user.user_id, need_mats)  # 回滚已扣材料
        lines = ["矿石不足，不能炼金！"]
        for oid, (need, have) in miss_ores.items():
            meta = ore.ore_meta(oid)
            lines.append(f"缺少 {(meta or {}).get('name', oid)}（需 {need}，现有 {have}）")
        return "\n".join(lines)

    user.copper -= need_copper
    consumable.grant_consumables(user.user_id, {out_id: int((recipe.get("output") or {}).get("count", 1))})
    db.session.commit()
    out_cnt = int((recipe.get("output") or {}).get("count", 1))
    kind = "药水" if out_meta.get("kind") == "potion" else "道具"
    return (f"⚗️ 炼金成功！获得「{out_meta['name']}」×{out_cnt}（{kind}）。\n"
            f"消耗：{format_cost(recipe)}。\n"
            f"发送 /使用 {out_meta['name']} 生效。")


def recipe_list_text(user):
    """/炼金 列表：按 药水/道具 分组展示配方（含消耗与产物说明）。"""
    from dungeon import historical_best_layer
    best = historical_best_layer(user)
    lines = ["⚗️ 炼金配方（/炼金 <配方名> 制作）"]
    for kind, kname in (("potion", "🧪 药水（属性强化，临时提升）"), ("tool", "🎫 道具（掉落增益）")):
        recs = [r for r in all_recipes() if r.get("kind") == kind]
        if not recs:
            continue
        lines.append(f"—— {kname} ——")
        for r in recs:
            min_layer = recipe_min_layer(r)
            out = consumable.consumable_meta((r.get("output") or {}).get("item_id", ""))
            out_name = out["name"] if out else "?"
            lock = "" if best >= min_layer else f"（🔒 需 {min_layer} 层）"
            lines.append(f"· {r['name']} → {out_name} {lock}")
            lines.append(f"    消耗：{format_cost(r)}")
            if r.get("desc"):
                lines.append(f"    效果：{r['desc']}")
    lines.append("—— 发送 /炼金 <配方名> 制作；/使用 <物品名> 生效；/背包 查看持有 ——")
    return "\n".join(lines)
