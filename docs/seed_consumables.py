# -*- coding: utf-8 -*-
"""炼金产物（consumables.json）与配方（alchemy_recipes.json）种子生成器。

药水 = 属性强化（6 系 × Lv1~5）；道具 = 掉落增益（6 类，部分高阶才提供）。
数值为初版示例（可调）；持续：药水默认「持续 N 层」，道具默认「持续 N 秒」。
用法： python docs/seed_consumables.py   → 在项目根目录生成/覆写两个 JSON。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROMAN = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ"}

# ---------------- 药水：6 系 × 5 级 ----------------
# (id, 名, 属性, [Lv1..Lv5 数值]) —— 全部点数型
POTIONS = [
    ("potion_atk", "狂攻药水", "attack",       [15, 40, 90, 160, 260]),
    ("potion_int", "凝神药水", "intelligence", [15, 40, 90, 160, 260]),
    ("potion_mp",  "魔涌药水", "mp",           [12, 30, 70, 120, 200]),
    ("potion_def", "坚壁药水", "defense",      [20, 50, 110, 190, 300]),
    ("potion_hp",  "血源药水", "hp",           [25, 60, 130, 220, 350]),
    ("potion_agi", "疾风药水", "agility",      [10, 25, 60, 110, 180]),
]

# ---------------- 道具：掉落增益（Lv1~3，部分高阶才有） ----------------
# (id, 名, bonus_type, scope, {level: mult})
TOOLS = [
    ("tool_coin",    "聚财符", "coin",       None,       {1: 1.2, 2: 1.4, 3: 1.6}),
    ("tool_equip",   "寻宝符", "equipment",  None,       {2: 1.3, 3: 1.5}),          # 稀有 Lv1 不提供
    ("tool_ore",     "采掘符", "material",   "ore",      {1: 1.3, 2: 1.5, 3: 1.8}),
    ("tool_herb",    "采药符", "material",   "herb",     {1: 1.3, 2: 1.5, 3: 1.8}),
    ("tool_special", "祈灵符", "material",   "special",  {2: 1.4, 3: 1.7}),         # 稀有 Lv1 不提供
    ("tool_all",     "万象符", "material",   "all",      {3: 1.5}),                  # 稀有 仅 Lv3
]

# 药水配方成本阶梯（Lv1..5）
POTION_COST = [
    {"materials": {"herb_bloodgrass": 3}, "copper": 100},
    {"materials": {"herb_wakeflower": 3}, "copper": 400},
    {"materials": {"herb_moonmushroom": 3}, "ores": {"iron_ore": 1}, "copper": 1200},
    {"materials": {"herb_dragonblood": 2, "special_element": 1}, "copper": 4000},
    {"materials": {"special_relic": 1, "boss_ember": 1}, "copper": 12000},
]

# 道具配方成本（按 Lv）
TOOL_COST = {
    1: {"materials": {"special_beastsoul": 2, "herb_moonmushroom": 1}, "copper": 800},
    2: {"materials": {"special_element": 2, "herb_dragonblood": 1}, "ores": {"silver_ore": 1}, "copper": 2500},
    3: {"materials": {"special_relic": 2, "boss_frost": 1}, "copper": 8000},
}


def main():
    consumables = []
    recipes = []

    # 药水
    for pid, pname, stat, vals in POTIONS:
        for lv, val in enumerate(vals, start=1):
            cid = f"{pid}_{lv}"
            name = f"{pname}{ROMAN[lv]}"
            consumables.append({
                "id": cid, "name": name, "kind": "potion", "level": lv,
                "effect": {"type": "buff_stat", "stats": {stat: val},
                           "duration": {"type": "layers", "value": 100 * lv}},
            })
            cost = dict(POTION_COST[lv - 1])
            cost.setdefault("materials", {})
            cost.setdefault("ores", {})
            recipes.append({
                "id": f"alc_{cid}", "name": f"炼金·{name}", "kind": "potion",
                "desc": f"临时提升 {stat} +{val}（持续 {100 * lv} 层）",
                "cost": cost, "output": {"item_id": cid, "count": 1},
            })

    # 道具
    for tid, tname, btype, scope, lvmult in TOOLS:
        for lv in sorted(lvmult):
            cid = f"{tid}_{lv}"
            name = f"{tname}{ROMAN[lv]}"
            scope_txt = {"ore": "矿石", "herb": "草药", "special": "特殊物品",
                         "all": "全部材料"}.get(scope, "")
            if btype == "coin":
                desc = f"金钱掉落 ×{lvmult[lv]}（持续 30 分钟）"
            elif btype == "equipment":
                desc = f"装备掉落率 ×{lvmult[lv]}（持续 30 分钟）"
            else:
                desc = f"{scope_txt}掉落率 ×{lvmult[lv]}（持续 30 分钟）"
            consumables.append({
                "id": cid, "name": name, "kind": "tool", "level": lv,
                "effect": {"type": "drop_bonus", "bonus_type": btype,
                           **({"scope": scope} if scope else {}),
                           "mult": lvmult[lv],
                           "duration": {"type": "time", "value": 1800}},
            })
            cost = dict(TOOL_COST[lv])
            cost.setdefault("materials", {})
            cost.setdefault("ores", {})
            recipes.append({
                "id": f"alc_{cid}", "name": f"炼金·{name}", "kind": "tool",
                "desc": desc, "cost": cost, "output": {"item_id": cid, "count": 1},
            })

    with open(os.path.join(BASE, "consumables.json"), "w", encoding="utf-8") as f:
        json.dump({"consumables": consumables}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BASE, "alchemy_recipes.json"), "w", encoding="utf-8") as f:
        json.dump({"recipes": recipes}, f, ensure_ascii=False, indent=2)
    print(f"生成完成: consumables {len(consumables)} 条, 配方 {len(recipes)} 条")


if __name__ == "__main__":
    main()
