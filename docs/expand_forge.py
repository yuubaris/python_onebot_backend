# -*- coding: utf-8 -*-
"""铁匠铺配方对称扩充生成器（D14：10 → ~28~32，开发期用，不入库）。

依据 docs/equipment-system-redo-plan.md §6.2 组合表：每 Lv 补全
  战士武器(weapon) / 法师法杖(staff) / 战士防具(armor) / 法师生存(robe) /
  盾(shield) / 法器(focus) / 饰品(accessory)
两职业 + 部位对称；数值逐级递增且每级 weapon≈staff 同价对齐（§4 主检）。

命名独立意象；与武器库/稀有池/既有 forge 重名时自动改名冲突检测。
forge_gold_staff(创世圣袍 robe) 为顶级法袍，按组合表归位 Lv4。

用法：
  python docs/expand_forge.py          # dry-run
  python docs/expand_forge.py --apply   # 备份并写回 forge.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FG_FILE = os.path.join(BASE, "forge.json")
EQ_FILE = os.path.join(BASE, "equipment.json")
RARE_FILE = os.path.join(BASE, "rare_drops.json")

# 每级 price / cost（与现有同 Lv 配方一致）
LEVEL = {
    1: {"price": 64000, "cost": {"copper_ore": 70, "iron_ore": 70}},
    2: {"price": 125000, "cost": {"copper_ore": 160, "iron_ore": 160,
                                   "silver_ore": 38, "mithril_ore": 38}},
    3: {"price": 400000, "cost": {"copper_ore": 280, "iron_ore": 280,
                                   "silver_ore": 70, "mithril_ore": 70,
                                   "gold_ore": 30, "dragon_crystal": 18}},
    4: {"price": 1200000, "cost": {"copper_ore": 360, "iron_ore": 350,
                                    "silver_ore": 100, "mithril_ore": 90,
                                    "gold_ore": 45, "dragon_crystal": 30}},
}

# 每级武器收益分锚（该级 weapon 现分，staff 对齐到它）
T_WEAPON = {1: 221.0, 2: 282.0, 3: 351.0, 4: 450.0}

# 部位定位：目标 score = weapon × 系数（robe/armor 生存向，见 §4.2.2）
RATIO = {
    "robe": 0.85,      # 法师生存+部分魔法输出
    "shield": 0.60,    # 战士副手（半生存半输出）
    "focus": 0.60,     # 法师副手
    "accessory": 0.65, # 通用饰品
    "staff": 1.0,
}
LINE_BY_TYPE = {
    "weapon": "physical", "shield": "physical", "armor": "any",
    "staff": "magic", "robe": "magic", "focus": "magic", "accessory": "any",
}

# 新增配方规格: (level, type, name, def/hp 模板)
# name 与 §6.2 组合表一致；与稀有/商店撞车的已改名（见冲突检测打印）
PLAN = [
    # Lv1
    (1, "robe",      "灵纹轻袍",  (30, 72)),
    (1, "shield",    "壁垒圆盾",  (60, 62)),
    (1, "focus",     "秘仪法器",  (52, 52)),
    (1, "accessory", "旅者徽记",  (18, 38)),
    # Lv2
    (2, "robe",      "星辉法袍",  (42, 100)),
    (2, "shield",    "辉光方盾",  (84, 90)),
    (2, "focus",     "曜晶法器",  (76, 76)),     # 原名秘术法器→与稀有冲突改名
    (2, "accessory", "贤者指环",  (26, 54)),
    # Lv3 (rob 圣辉法袍替换被移走至Lv4的创世圣袍)
    (3, "robe",      "圣辉法袍",  (48, 110)),
    (3, "shield",    "神鳞重盾",  (112, 128)),
    (3, "focus",     "神火宝珠",  (104, 104)),    # 原名奥术宝珠→与稀有冲突改名
    (3, "accessory", "神纹圣印",  (36, 74)),
    # Lv4
    (4, "robe",      "神铸圣袍",  (60, 150)),    # Lv4 法师生存（创世圣袍移入本 Lv）
    (4, "armor",     "神铸神铠",  (168, 186)),   # Lv4 战士顶级防具（补缺）
    (4, "shield",    "神铸圣盾",  (150, 170)),
    (4, "focus",     "灭世宝珠",  (140, 140)),
    (4, "accessory", "永恒王冠",  (50, 104)),
]

# 需要归位 Lv4 的顶级法袍（现有 forge_gold_staff）
GOLD_ROBE_TO_LV4 = "forge_gold_staff"


def score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def fit_phys(target, def_, hp, ratio_g=0.18):
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    a0 = int(main_t / (2 + 1.5 * ratio_g))
    for attack in range(max(1, a0 - 8), a0 + 9):
        agility = max(0, int(round((main_t - 2 * attack) / 1.5)))
        sc = (attack * 2 + agility * 1.5) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, attack, agility)
    return best[1], best[2]


def fit_magic(target, def_, hp, ratio_m=0.5):
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    i0 = int(main_t / (1.25 + 1.2 * ratio_m))
    for inte in range(max(1, i0 - 8), i0 + 9):
        mp = max(0, int(round((main_t - 1.25 * inte) / 1.2)))
        sc = (inte * 1.25 + mp * 1.2) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, inte, mp)
    return best[1], best[2]


def build_item(iid, name, typ, level, price, cost, a, g, i, m, d, h, line):
    return {
        "id": iid, "name": name, "type": typ, "level": level,
        "attack": a, "defense": d, "hp": h, "mp": m,
        "agility": g, "intelligence": i,
        "price": price, "cost": cost, "line": line, "tier": 4,
    }


def main():
    apply = "--apply" in sys.argv
    fg = json.load(open(FG_FILE, encoding="utf-8"))
    recipes = fg["recipes"]
    eq_names = {it["name"] for it in json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]}
    rare_names = {r["name"] for r in json.load(open(RARE_FILE, encoding="utf-8"))["items"]}
    used_ids = {r["id"] for r in recipes}
    used_names = {r["name"] for r in recipes} | eq_names | rare_names

    # 1) 归位 + 数值修正：创世圣袍(forge_gold_staff) Lv3 → Lv4（顶级法袍）
    #    历史遗留 990 分超模（≈2.2×同Lv毕业武器），按 §4.2.2 法袍定位 0.85 重算，
    #    使 Lv1~Lv4 法袍逐级 188/240/298/383 且均 ≤ 同 Lv 武器（450）。
    for r in recipes:
        if r["id"] == GOLD_ROBE_TO_LV4:
            r["level"] = 4
            r["price"] = LEVEL[4]["price"]
            r["cost"] = dict(LEVEL[4]["cost"])
            d = r.get("defense", 55); h = r.get("hp", 120)
            target = T_WEAPON[4] * 0.85
            i, m = fit_magic(target, d, h, ratio_m=4.5)
            r["attack"] = 0; r["intelligence"] = i; r["mp"] = m
            r["agility"] = 0
            break

    # 2) 补充缺失部位主武器：Lv3 staff(灭世法杖)、Lv4 staff(创世法杖) 若缺
    has = {(r.get("level"), r.get("type")) for r in recipes}
    staff_plan = [
        (3, "灭世法杖", "forge_destroy_staff", (20, 52)),
        (4, "创世法杖", "forge_creation_staff", (26, 62)),
    ]
    new_items = []
    for level, name, iid, (d, h) in staff_plan:
        if (level, "staff") in has:
            continue
        i, m = fit_magic(T_WEAPON[level], d, h, ratio_m=4.0)  # 法杖魔力主导(mp≈4×int)
        new_items.append(build_item(iid, name, "staff", level,
                                    LEVEL[level]["price"], LEVEL[level]["cost"],
                                    0, 0, i, m, d, h, "magic"))

    # 3) 补齐 法袍/盾/法器/饰品/战士防具
    for level, typ, name, (d, h) in PLAN:
        if (level, typ) in has:
            continue
        if name in used_names:
            print(f"  ⚠️ 名字冲突已跳过: {name}（Lv{level} {typ}）")
            continue
        if typ == "armor":
            # 纯生存甲：不参与 weapon/staff 主检，按保底分 def*2+hp*1.2 逐级递增
            a, g, i, m = 0, 0, 0, 0
        else:
            target = T_WEAPON[level] * RATIO[typ]
            if typ in ("weapon", "shield"):
                a, g = fit_phys(target, d, h, ratio_g=0.18 if typ == "weapon" else 0.16)
                i, m = 0, 0
            elif typ in ("staff", "robe", "focus"):
                # 法系件魔力主导：法袍 mp:int≈4.5、法杖 4.0、法器 3.0（延续锻造风格）
                rm = {"staff": 4.0, "robe": 4.5, "focus": 3.0}.get(typ, 0.9)
                i, m = fit_magic(target, d, h, ratio_m=rm)
                a, g = 0, 0
            else:  # accessory
                a, g = fit_phys(target * 0.45, d, h, ratio_g=0.3)
                i, m = fit_magic(target * 0.6, d, h, ratio_m=0.7)
        lv_meta = LEVEL[level]
        iid = f"forge_{typ}_{level}"
        if iid in used_ids:
            k = 2
            while f"{iid}_{k}" in used_ids:
                k += 1
            iid = f"{iid}_{k}"
        used_ids.add(iid); used_names.add(name)
        new_items.append(build_item(iid, name, typ, level, lv_meta["price"],
                                    lv_meta["cost"], a, g, i, m, d, h,
                                    LINE_BY_TYPE[typ]))

    all_items = recipes + new_items
    print(f"现有 {len(recipes)} 配方 → 扩充后 {len(all_items)}（新增 {len(new_items)}）\n")
    for it in sorted(all_items, key=lambda x: (x["level"], x["price"], x["type"])):
        print(f"  Lv{it['level']} ¥{it['price']:>7} {it['type']:9s} {it['id']:22s} "
              f"{it['name']:<7} atk{it.get('attack',0):<4} def{it.get('defense',0):<4} "
              f"hp{it.get('hp',0):<4} mp{it.get('mp',0):<4} agi{it.get('agility',0):<4} "
              f"int{it.get('intelligence',0):<4} score={score(it):.0f}")

    # 主检：同 price weapon vs staff 偏差
    print("\n== 同价主检（forge weapon vs staff）==")
    bad = 0
    by_p = {}
    for it in all_items:
        if it["type"] in ("weapon", "staff"):
            by_p.setdefault(it["price"], []).append(it)
    for p in sorted(by_p):
        w = [score(x) for x in by_p[p] if x["type"] == "weapon"]
        s = [score(x) for x in by_p[p] if x["type"] == "staff"]
        if w and s:
            diff = abs(max(w) - max(s)) / max(w) * 100
            if diff > 3:
                bad += 1
            print(f"  ¥{p}: weapon={max(w):.0f} staff={max(s):.0f} diff={diff:.1f}% "
                  + ("❌" if diff > 3 else "OK"))

    if not apply:
        print("\n[dry-run] 未写盘。加 --apply 落盘。")
        return 0
    shutil.copy(FG_FILE, FG_FILE + ".bak-d14")
    with open(FG_FILE, "w", encoding="utf-8") as f:
        json.dump({"recipes": all_items}, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {FG_FILE}（备份 .bak-d14），共 {len(all_items)} 配方。主检异常: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
