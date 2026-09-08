# -*- coding: utf-8 -*-
"""炼金产物（consumables.json）与配方（alchemy_recipes.json）种子生成器。

药水 = 属性强化（6 系 × Lv1~5，按推进公式权重 攻=魔=2 / 敏=智=1.5 折算收益等价）；
道具 = 掉落增益（6 类，部分高阶才提供）。

v2.11 口径：药水持续 100 层；道具持续 4 小时；消耗参考锻造多样化（同 Lv 六种药水各不相同）；
配方带 level（分级解锁 Lv1@100 → Lv5@1700）；命名等级用阿拉伯数字（「聚财符1」，
匹配层兼容 空格/罗马/中文数字 写法——见 consumable.normalize_name）。

用法： python docs/seed_consumables.py   → 在项目根目录生成/覆写两个 JSON。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POTION_DUR_LAYERS = 100   # 药水持续层数（统一）
TOOL_DUR_SEC = 14400      # 道具持续秒数（4 小时）

# ---------------- 药水：6 系 × 5 级 ----------------
# (id, 名, 属性, [Lv1..Lv5 数值]) —— 敏/智 = 攻/魔 × 4/3（权重 1.5 vs 2）
POTIONS = [
    ("potion_atk", "狂攻药水", "attack",       [15, 40, 90, 160, 260]),
    ("potion_mp",  "魔涌药水", "mp",           [15, 40, 90, 160, 260]),
    ("potion_int", "凝神药水", "intelligence", [20, 53, 120, 213, 347]),
    ("potion_agi", "疾风药水", "agility",      [20, 53, 120, 213, 347]),
    ("potion_def", "坚壁药水", "defense",      [20, 50, 110, 190, 300]),
    ("potion_hp",  "血源药水", "hp",           [25, 60, 130, 220, 350]),
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

# ---------------- 药水配方消耗（Lv × 属性；参考锻造「线+级」多样化） ----------------
# 物理线(攻/敏)=金属矿石+草药；魔法线(魔/智)=草药+特殊；生存线(防/命)=特殊+草药；逐级稀有度递增
POTION_COST = {
    1: {
        "atk": {"materials": {"herb_bloodgrass": 3},            "ores": {"copper_ore": 1}, "copper": 100},
        "agi": {"materials": {"herb_bloodgrass": 3},            "ores": {"tin_ore": 1},    "copper": 100},
        "mp":  {"materials": {"herb_bloodgrass": 3, "herb_wakeflower": 1},                "copper": 100},
        "int": {"materials": {"herb_wakeflower": 3, "herb_bloodgrass": 1},                "copper": 100},
        "def": {"materials": {"special_beastsoul": 2, "herb_bloodgrass": 1},             "copper": 100},
        "hp":  {"materials": {"herb_bloodgrass": 4, "special_beastsoul": 1},             "copper": 100},
    },
    2: {
        "atk": {"materials": {"herb_wakeflower": 3},            "ores": {"iron_ore": 1},   "copper": 400},
        "agi": {"materials": {"herb_wakeflower": 3},            "ores": {"lead_ore": 1},   "copper": 400},
        "mp":  {"materials": {"herb_wakeflower": 3, "special_beastsoul": 1},              "copper": 400},
        "int": {"materials": {"herb_moonmushroom": 2, "herb_wakeflower": 2},              "copper": 400},
        "def": {"materials": {"special_beastsoul": 3, "herb_wakeflower": 1},              "copper": 400},
        "hp":  {"materials": {"herb_wakeflower": 4, "herb_moonmushroom": 1},              "copper": 400},
    },
    3: {
        "atk": {"materials": {"herb_moonmushroom": 3},          "ores": {"silver_ore": 1},  "copper": 1200},
        "agi": {"materials": {"herb_moonmushroom": 3},          "ores": {"mithril_ore": 1}, "copper": 1200},
        "mp":  {"materials": {"herb_moonmushroom": 3, "special_element": 1},               "copper": 1200},
        "int": {"materials": {"herb_moonmushroom": 4, "special_beastsoul": 1},             "copper": 1200},
        "def": {"materials": {"special_element": 2, "herb_moonmushroom": 2},               "copper": 1200},
        "hp":  {"materials": {"herb_moonmushroom": 4, "special_element": 1},               "copper": 1200},
    },
    4: {
        "atk": {"materials": {"herb_dragonblood": 2},           "ores": {"adamantite_ore": 2}, "copper": 4000},
        "agi": {"materials": {"herb_dragonblood": 2},           "ores": {"stardust_sand": 2},  "copper": 4000},
        "mp":  {"materials": {"herb_dragonblood": 2, "special_element": 2},                    "copper": 4000},
        "int": {"materials": {"herb_dragonblood": 3, "herb_moonmushroom": 2},                  "copper": 4000},
        "def": {"materials": {"special_element": 3, "herb_dragonblood": 1},                    "copper": 4000},
        "hp":  {"materials": {"herb_dragonblood": 4, "special_element": 1},                    "copper": 4000},
    },
    5: {
        "atk": {"materials": {"special_relic": 1},              "ores": {"star_iron": 1},      "copper": 12000},
        "agi": {"materials": {"special_relic": 1},              "ores": {"oracle_stone": 1},   "copper": 12000},
        "mp":  {"materials": {"special_relic": 2, "herb_dragonblood": 2},                      "copper": 12000},
        "int": {"materials": {"special_relic": 2, "special_element": 1},                       "copper": 12000},
        "def": {"materials": {"special_relic": 2},              "ores": {"star_iron": 1},      "copper": 12000},
        "hp":  {"materials": {"herb_dragonblood": 4, "special_relic": 1},                      "copper": 12000},
    },
}

# ---------------- 道具配方成本（Lv → 消耗；按增益目标配材料） ----------------
TOOL_COST = {
    "coin": {
        1: {"materials": {"special_beastsoul": 2, "herb_moonmushroom": 1}, "copper": 800},
        2: {"materials": {"special_element": 2, "herb_dragonblood": 1}, "ores": {"silver_ore": 1}, "copper": 2500},
        3: {"materials": {"special_relic": 2, "boss_frost": 1}, "copper": 8000},
    },
    "equip": {
        2: {"materials": {"boss_wolf": 2, "special_element": 1}, "copper": 2500},
        3: {"materials": {"boss_frost": 2, "special_relic": 1}, "copper": 8000},
    },
    "ore": {
        1: {"materials": {"herb_bloodgrass": 1}, "ores": {"copper_ore": 4}, "copper": 800},
        2: {"materials": {"herb_moonmushroom": 1}, "ores": {"mithril_ore": 4}, "copper": 2500},
        3: {"materials": {"herb_dragonblood": 1}, "ores": {"dragon_crystal": 4}, "copper": 8000},
    },
    "herb": {
        1: {"materials": {"herb_bloodgrass": 6, "special_beastsoul": 1}, "copper": 800},
        2: {"materials": {"herb_moonmushroom": 5, "special_element": 1}, "copper": 2500},
        3: {"materials": {"herb_dragonblood": 4, "special_relic": 1}, "copper": 8000},
    },
    "special": {
        2: {"materials": {"special_element": 4, "herb_moonmushroom": 2}, "copper": 2500},
        3: {"materials": {"special_relic": 3, "herb_dragonblood": 2}, "copper": 8000},
    },
    "all": {
        3: {"materials": {"special_relic": 2, "boss_frost": 1, "herb_dragonblood": 2}, "copper": 8000},
    },
}

TOOL_TARGET = {"coin": "金钱", "equip": "装备", "ore": "矿石", "herb": "草药",
               "special": "特殊物品", "all": "全部材料"}


def main():
    consumables = []
    recipes = []

    # 药水
    for pid, pname, stat, vals in POTIONS:
        key = pid.split("_", 1)[1]
        for lv, val in enumerate(vals, start=1):
            cid = f"{pid}_{lv}"
            name = f"{pname}{lv}"
            consumables.append({
                "id": cid, "name": name, "kind": "potion", "level": lv,
                "effect": {"type": "buff_stat", "stats": {stat: val},
                           "duration": {"type": "layers", "value": POTION_DUR_LAYERS}},
            })
            recipes.append({
                "id": f"alc_{cid}", "name": f"炼金·{name}", "kind": "potion", "level": lv,
                "desc": f"临时提升 {stat} +{val}（持续 {POTION_DUR_LAYERS} 层）",
                "cost": dict(POTION_COST[lv][key]),
                "output": {"item_id": cid, "count": 1},
            })

    # 道具
    for tid, tname, btype, scope, lvmult in TOOLS:
        key = tid.split("_", 1)[1]
        target = TOOL_TARGET[key]
        for lv in sorted(lvmult):
            cid = f"{tid}_{lv}"
            name = f"{tname}{lv}"
            desc = f"{target}掉落 ×{lvmult[lv]}（持续 4 小时）"
            consumables.append({
                "id": cid, "name": name, "kind": "tool", "level": lv,
                "effect": {"type": "drop_bonus", "bonus_type": btype,
                           **({"scope": scope} if scope else {}),
                           "mult": lvmult[lv],
                           "duration": {"type": "time", "value": TOOL_DUR_SEC}},
            })
            recipes.append({
                "id": f"alc_{cid}", "name": f"炼金·{name}", "kind": "tool", "level": lv,
                "desc": desc, "cost": dict(TOOL_COST[key][lv]),
                "output": {"item_id": cid, "count": 1},
            })

    with open(os.path.join(BASE, "consumables.json"), "w", encoding="utf-8") as f:
        json.dump({"consumables": consumables}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BASE, "alchemy_recipes.json"), "w", encoding="utf-8") as f:
        json.dump({"recipes": recipes}, f, ensure_ascii=False, indent=2)
    print(f"生成完成: consumables {len(consumables)} 条, 配方 {len(recipes)} 条")


if __name__ == "__main__":
    main()
