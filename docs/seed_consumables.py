# -*- coding: utf-8 -*-
"""炼金产物（consumables.json）与配方（alchemy_recipes.json）种子生成器。

药水 = 属性强化（6 系 × Lv1~5，按推进公式权重 攻=魔=2 / 敏=智=1.5 折算收益等价）；
道具 = 掉落增益（6 类，部分高阶才提供）。

v2.11 口径：药水持续 100 层；道具持续 4 小时；配方带 level（分级解锁 Lv1@100 → Lv5@1700）；
v2.11.2：药水消耗 = 「草药+特殊」、不耗矿石（不同配方不同草药，四种草药全利用）；
命名等级用阿拉伯数字（「聚财符1」），匹配层兼容 空格/罗马/中文数字 写法——见 consumable.normalize_name。

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
    ("tool_myriad",  "万宝符", "material",   "all",      {1: 1.6, 2: 1.8, 3: 2.0}),   # 究极: 万宝源晶(大Boss专属), 万象符上位
]

# ---------------- 药水配方消耗（Lv × 属性；「草药+特殊+Boss材料」多样化） ----------------
# 药水不消耗矿石（锻造装备与药水抢矿，战士综合缺矿；草药反而闲置）；
# 草药与特殊材料**同池同概率掉落**（见 material.py roll_material），成本由稀有度档唯一决定，
# 故配方按「材料点数」对齐——common=1 / rare=2 / legendary=3，同级六种点数一致（5/6/7/8/9），
# 保证同级期望成本相同；等效药水（攻=魔、敏=智、防=命）同级用同一种 Boss 材料，
# 来源等级一致且 ≤ 药水解锁层（Lv2=磐岩心核/Lv3=尸皇骨匣/Lv4=霜语结晶/Lv5=烬鳞龙鳞），
# 草药+特殊组合每级 6/6 不同，逐级稀有度递增
POTION_COST = {
    1: {  # 100铜 · 点数5（common：止血草/醒神花/兽魂结晶；入门无 Boss 材料）
        "atk": {"materials": {"herb_bloodgrass": 4, "special_beastsoul": 1},                 "copper": 100},
        "agi": {"materials": {"herb_wakeflower": 4, "special_beastsoul": 1},                 "copper": 100},
        "mp":  {"materials": {"herb_bloodgrass": 3, "herb_wakeflower": 1, "special_beastsoul": 1}, "copper": 100},
        "int": {"materials": {"herb_wakeflower": 3, "herb_bloodgrass": 1, "special_beastsoul": 1}, "copper": 100},
        "def": {"materials": {"herb_bloodgrass": 2, "herb_wakeflower": 2, "special_beastsoul": 1}, "copper": 100},
        "hp":  {"materials": {"herb_bloodgrass": 2, "special_beastsoul": 3},                 "copper": 100},
    },
    2: {  # 400铜 · 点数6（月光菇/元素之心登场）；统一 Boss 材料=磐岩心核(200/300层)
        "atk": {"materials": {"boss_magma": 1, "herb_bloodgrass": 3, "herb_moonmushroom": 1, "special_beastsoul": 1}, "copper": 400},
        "agi": {"materials": {"boss_magma": 1, "herb_wakeflower": 3, "herb_moonmushroom": 1, "special_beastsoul": 1}, "copper": 400},
        "mp":  {"materials": {"boss_magma": 1, "herb_bloodgrass": 1, "herb_moonmushroom": 2, "special_beastsoul": 1}, "copper": 400},
        "int": {"materials": {"boss_magma": 1, "herb_wakeflower": 1, "herb_moonmushroom": 2, "special_beastsoul": 1}, "copper": 400},
        "def": {"materials": {"boss_magma": 1, "herb_moonmushroom": 2, "special_beastsoul": 2}, "copper": 400},
        "hp":  {"materials": {"boss_magma": 1, "herb_bloodgrass": 3, "special_beastsoul": 1, "special_element": 1}, "copper": 400},
    },
    3: {  # 1200铜 · 点数7（元素之心为主）；统一 Boss 材料=尸皇骨匣(400~700层)
        "atk": {"materials": {"boss_bone": 1, "herb_moonmushroom": 2, "herb_wakeflower": 1, "special_element": 1}, "copper": 1200},
        "agi": {"materials": {"boss_bone": 1, "herb_moonmushroom": 2, "herb_bloodgrass": 1, "special_element": 1}, "copper": 1200},
        "mp":  {"materials": {"boss_bone": 1, "herb_moonmushroom": 2, "special_beastsoul": 1, "special_element": 1}, "copper": 1200},
        "int": {"materials": {"boss_bone": 1, "herb_moonmushroom": 3, "special_beastsoul": 1}, "copper": 1200},
        "def": {"materials": {"boss_bone": 1, "herb_bloodgrass": 3, "herb_moonmushroom": 1, "special_element": 1}, "copper": 1200},
        "hp":  {"materials": {"boss_bone": 1, "herb_wakeflower": 3, "herb_moonmushroom": 1, "special_element": 1}, "copper": 1200},
    },
    4: {  # 4000铜 · 点数8（龙血草/古神残片登场）；统一 Boss 材料=霜语结晶(800~1400层)
        "atk": {"materials": {"boss_frost": 1, "herb_dragonblood": 2, "special_element": 1}, "copper": 4000},
        "agi": {"materials": {"boss_frost": 1, "herb_dragonblood": 1, "herb_moonmushroom": 1, "special_relic": 1}, "copper": 4000},
        "mp":  {"materials": {"boss_frost": 1, "herb_dragonblood": 1, "herb_moonmushroom": 2, "herb_bloodgrass": 1}, "copper": 4000},
        "int": {"materials": {"boss_frost": 1, "herb_moonmushroom": 2, "special_beastsoul": 1, "special_relic": 1}, "copper": 4000},
        "def": {"materials": {"boss_frost": 1, "herb_dragonblood": 2, "special_beastsoul": 2}, "copper": 4000},
        "hp":  {"materials": {"boss_frost": 1, "herb_moonmushroom": 1, "herb_bloodgrass": 1, "special_element": 1, "special_relic": 1}, "copper": 4000},
    },
    5: {  # 12000铜 · 点数9（legendary 主打）；统一 Boss 材料=烬鳞龙鳞(1600~3000层)
        "atk": {"materials": {"boss_ember": 1, "herb_dragonblood": 2, "special_relic": 1}, "copper": 12000},
        "agi": {"materials": {"boss_ember": 1, "herb_dragonblood": 1, "special_relic": 2}, "copper": 12000},
        "mp":  {"materials": {"boss_ember": 1, "herb_moonmushroom": 3, "special_relic": 1}, "copper": 12000},
        "int": {"materials": {"boss_ember": 1, "herb_dragonblood": 1, "herb_moonmushroom": 2, "special_element": 1}, "copper": 12000},
        "def": {"materials": {"boss_ember": 1, "herb_moonmushroom": 2, "special_element": 1, "special_relic": 1}, "copper": 12000},
        "hp":  {"materials": {"boss_ember": 1, "herb_dragonblood": 1, "herb_moonmushroom": 1, "special_element": 2}, "copper": 12000},
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
    "myriad": {  # 万宝符：究极大 Boss 专属材料「万宝源晶」主料；Lv3 叠最终 Boss「万瓜圣辉」
        1: {"materials": {"boss_myriad": 1}, "copper": 4000},
        2: {"materials": {"boss_myriad": 2}, "copper": 15000},
        3: {"materials": {"boss_myriad": 3, "boss_wangua": 1}, "copper": 50000},
    },
}

TOOL_TARGET = {"coin": "金钱", "equip": "装备", "ore": "矿石", "herb": "草药",
               "special": "特殊物品", "all": "全部材料", "myriad": "全部材料"}


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
