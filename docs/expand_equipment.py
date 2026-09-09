# -*- coding: utf-8 -*-
"""武器库对称扩充生成器（开发期用，不入库）。

依据 docs/equipment-system-redo-plan.md §6.1：
- 每个价格档补齐 战士主手(weapon)/法师主手(staff)/防具(armor any)/法师生存(robe magic)/
  副手(shield physical / focus magic)/饰品(accessory any)，尽量各 tier 不断档。
- 同价格档 物理武器 与 魔法法杖 收益对齐（§4 反推法），新增 staff 反推 int/mp 使收益≈同档 weapon。
- 其它部位（armor/robe/shield/focus/accessory）按既有同 tier 该 type 的趋势生成，
  保持 type 内价格单调、不引入明显超模，且不参与主检（主检仅 weapon≈staff）。

用法：
  python docs/expand_equipment.py          # dry-run：只打印规划与校验，不写盘
  python docs/expand_equipment.py --apply   # 备份并写回 equipment.json
"""
import json
import math
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")

PRICES = [50, 75, 120, 200, 350, 600, 1000, 1700, 3000, 5200, 9000,
          15000, 26000, 45000]
# 价格档 → tier（与 tiers.PRICE_TIERS 一致）
def price_tier(p):
    for cap, t in [(120, 0), (600, 1), (3000, 2), (9000, 3),
                   (26000, 4), (45000, 5)]:
        if p <= cap:
            return t
    return 5


def score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def fit_phys(target, def_, hp, ratio_g=0.18, budget=None):
    """物理主手反推：给定 def/hp，求整数 (attack,agility) 使收益≈target。
    ratio_g = agility/attack 倾向。返回 dict 增量。"""
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    a0 = int(main_t / (2 + 1.5 * ratio_g))
    for attack in range(max(1, a0 - 6), a0 + 7):
        agility = max(0, int(round((main_t - 2 * attack) / 1.5)))
        sc = (attack * 2 + agility * 1.5) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, attack, agility)
    _, attack, agility = best
    return {"attack": attack, "agility": agility}


def fit_magic(target, def_, hp, ratio_m=0.55, budget=None):
    """魔法法杖反推：求整数 (intelligence,mp) 使收益≈target。ratio_m=mp/int。"""
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    i0 = int(main_t / (1.25 + 1.2 * ratio_m))
    for inte in range(max(1, i0 - 6), i0 + 7):
        mp = max(0, int(round((main_t - 1.25 * inte) / 1.2)))
        sc = (inte * 1.25 + mp * 1.2) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, inte, mp)
    _, inte, mp = best
    return {"intelligence": inte, "mp": mp}


# 每 tier 主手目标收益 T：以「同 tier 最高价武器」收益为锚（含已对齐的法杖）
# 这里用价格档本身的 weapon 现值；缺 weapon 的价格档用相邻插值（下方计算）
def target_score(items):
    """返回 {price: T}，T=该价格档 weapon 收益；无 weapon 档用相邻已有 weapon 插值。"""
    by_price = {}
    for it in items:
        if it.get("type") == "weapon":
            by_price.setdefault(it["price"], []).append(score(it))
    # 现有值（保留原档锚），缺失档用相邻线性插值
    pts = sorted((p, max(v)) for p, v in by_price.items())
    out = {}
    for p in PRICES:
        exact = [s for pp, s in pts if pp == p]
        if exact:
            out[p] = exact[0]
        else:
            below = [pp for pp, _ in pts if pp < p]
            above = [pp for pp, _ in pts if pp > p]
            if below and above:
                p0, p1 = below[-1], above[0]
                s0 = dict(pts)[p0]; s1 = dict(pts)[p1]
                out[p] = s0 + (s1 - s0) * (p - p0) / (p1 - p0)
            elif below:
                out[p] = dict(pts)[below[-1]]
            elif above:
                out[p] = dict(pts)[above[0]]
    return out


def main():
    apply = "--apply" in sys.argv
    items = json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]
    ids = {it["id"] for it in items}
    names = {it["name"] for it in items}

    T = target_score(items)
    new_items = []
    changes = []

    # 1) 补齐 weapon（物理主手）缺档：5200/15000/26000
    have_w = {it["price"] for it in items if it["type"] == "weapon"}
    for p in PRICES:
        if p in have_w:
            continue
        name = WEAPON_NAMES.get(p) or f"勇者之刃·{p}"
        iid = "blade_%d" % p
        if iid in ids:
            iid += "_b"
        base = fit_phys(T[p], def_=int(p ** 0.5), hp=int(p ** 0.6), ratio_g=0.16)
        item = {
            "id": iid, "name": name, "type": "weapon",
            "attack": base["attack"], "defense": int(p ** 0.5),
            "hp": int(p ** 0.6), "mp": 0, "agility": base["agility"],
            "intelligence": 0, "price": p, "line": "physical",
            "tier": price_tier(p),
        }
        ids.add(iid); names.add(name)
        new_items.append(item)
        changes.append(("weapon", p, name, round(score(item), 1), round(T[p], 1)))

    # 2) 补齐 staff（法师主手）缺档：使每价格档都有法杖，收益≈同档 weapon
    have_s = {it["price"] for it in items if it["type"] == "staff"}
    for p in PRICES:
        if p in have_s:
            continue
        name = STAFF_NAMES.get(p) or f"秘法之杖·{p}"
        iid = "staff_%d" % p
        if iid in ids:
            iid += "_b"
        base = fit_magic(T[p], def_=int(p ** 0.45), hp=int(p ** 0.6), ratio_m=0.5)
        item = {
            "id": iid, "name": name, "type": "staff",
            "attack": 0, "defense": int(p ** 0.45), "hp": int(p ** 0.6),
            "mp": base["mp"], "agility": 0, "intelligence": base["intelligence"],
            "price": p, "line": "magic", "tier": price_tier(p),
        }
        ids.add(iid); names.add(name)
        new_items.append(item)
        changes.append(("staff", p, name, round(score(item), 1), round(T[p], 1)))

    # 3) 补齐 armor（any 通用生存）每 tier ≥1
    for p, name in [(120, "轻铁甲"), (1000, "鳞纹战甲"), (9000, "玄铁战甲"),
                    (26000, "苍穹战铠")]:
        if any(it["type"] == "armor" and it["tier"] == price_tier(p) for it in items):
            continue
        if any(it["type"] == "armor" and it["price"] == p for it in items):
            continue
        t = price_tier(p)
        d = DEF_HP[p][0]; h = DEF_HP[p][1]
        item = {"id": "armor_%d" % p, "name": name, "type": "armor",
                "attack": 0, "defense": d, "hp": h, "mp": 0, "agility": 0,
                "intelligence": 0, "price": p, "line": "any", "tier": t}
        ids.add(item["id"]); names.add(item["name"])
        new_items.append(item)
        changes.append(("armor", p, name, round(score(item), 1), "-"))

    # 4) 补齐 robe（法师生存 magic）每 tier ≥1
    for p, name in [(600, "秘纹法袍"), (45000, "神谕圣袍")]:
        if any(it["type"] == "robe" and it["tier"] == price_tier(p) for it in items):
            continue
        if any(it["type"] == "robe" and it["price"] == p for it in items):
            continue
        # 法师袍输出偏 int/mp，数值参考同 tier staff 强度（生存袍按 0.75 预算）
        base = fit_magic(T[p] * 0.75, def_=ROBE_DH[p][0], hp=ROBE_DH[p][1], ratio_m=0.8)
        item = {"id": "robe_%d" % p, "name": name, "type": "robe",
                "attack": 0, "defense": ROBE_DH[p][0], "hp": ROBE_DH[p][1],
                "mp": base["mp"], "agility": 0, "intelligence": base["intelligence"],
                "price": p, "line": "magic", "tier": price_tier(p)}
        ids.add(item["id"]); names.add(item["name"])
        new_items.append(item)
        changes.append(("robe", p, name, round(score(item), 1), "-"))

    # 5) 补齐 shield（战士副手 physical）每 tier ≥1 缺档
    for p, name in [(9000, "镇岳重盾"), (45000, "苍穹壁垒")]:
        if any(it["type"] == "shield" and it["tier"] == price_tier(p) for it in items):
            continue
        if any(it["type"] == "shield" and it["price"] == p for it in items):
            continue
        # 盾：半输出半生存（参考同档武器 0.7 预算 + def/hp）
        base = fit_phys(T[p] * 0.7, def_=SHIELD_DH[p][0], hp=SHIELD_DH[p][1], ratio_g=0.12)
        item = {"id": "shield_%d" % p, "name": name, "type": "shield",
                "attack": base["attack"], "defense": SHIELD_DH[p][0],
                "hp": SHIELD_DH[p][1], "mp": 0, "agility": base["agility"],
                "intelligence": 0, "price": p, "line": "physical", "tier": price_tier(p)}
        ids.add(item["id"]); names.add(item["name"])
        new_items.append(item)
        changes.append(("shield", p, name, round(score(item), 1), "-"))

    # 6) 补齐 focus（法师副手 magic）每 tier ≥1（原全缺）
    focus_plan = [(50, "见习法器"), (200, "元素法器"), (1000, "秘术法器"),
                  (5200, "奥术法典"), (15000, "苍穹法典"), (45000, "神谕法典")]
    have_focus_tier = {it["tier"] for it in items if it["type"] == "focus"}
    for p, name in focus_plan:
        t = price_tier(p)
        if t in have_focus_tier:
            continue
        # 法器：半输出半生存（参考同档武器 0.55 预算）
        base = fit_magic(T[p] * 0.55, def_=FOCUS_DH[p][0], hp=FOCUS_DH[p][1], ratio_m=0.9)
        item = {"id": "focus_%d" % p, "name": name, "type": "focus",
                "attack": 0, "defense": FOCUS_DH[p][0], "hp": FOCUS_DH[p][1],
                "mp": base["mp"], "agility": 0, "intelligence": base["intelligence"],
                "price": p, "line": "magic", "tier": t}
        ids.add(item["id"]); names.add(item["name"])
        new_items.append(item)
        have_focus_tier.add(t)
        changes.append(("focus", p, name, round(score(item), 1), "-"))

    # 7) 补齐 accessory（any）低档 50/75/120
    for p, name in [(50, "初心护符"), (120, "冒险徽章")]:
        if any(it["type"] == "accessory" and it["price"] == p for it in items):
            continue
        base = fit_magic(T[p] * 0.7, def_=ACC_DH[p][0], hp=ACC_DH[p][1], ratio_m=0.7)
        item = {"id": "acc_%d" % p, "name": name, "type": "accessory",
                "attack": 0, "defense": ACC_DH[p][0], "hp": ACC_DH[p][1],
                "mp": base["mp"], "agility": int(base["intelligence"] * 0.3),
                "intelligence": int(base["intelligence"] * 0.7),
                "price": p, "line": "any", "tier": price_tier(p)}
        ids.add(item["id"]); names.add(item["name"])
        new_items.append(item)
        changes.append(("accessory", p, name, round(score(item), 1), "-"))

    total = items + new_items
    # 打印
    print(f"现有 {len(items)} 件 → 扩充后 {len(total)} 件（新增 {len(new_items)}）\n")
    for typ, p, nm, sc, tg in changes:
        print(f"  + {typ:9s} ¥{p:<7} {nm:<8} score={sc:7.1f}  目标T={tg}")
    if not apply:
        print("\n[dry-run] 未写盘。加 --apply 落盘（会先备份 .bak-expand）。")
        return 0

    # 校验：同价 weapon≈staff
    bad = 0
    by_p = {}
    for it in total:
        if it["type"] in ("weapon", "staff"):
            by_p.setdefault(it["price"], []).append(it)
    print("\n== 同价物理/魔法主检 ==")
    for p in sorted(by_p):
        arr = by_p[p]
        w = [score(x) for x in arr if x["type"] == "weapon"]
        s = [score(x) for x in arr if x["type"] == "staff"]
        if w and s:
            diff = abs(max(w) - max(s)) / max(w) * 100
            flag = "OK" if diff <= 3 else ("⚠️" if diff <= 15 else "❌")
            if flag != "OK":
                bad += 1
            print(f"  ¥{p}: weapon={max(w):.1f} staff={max(s):.1f} diff={diff:.1f}% {flag}")

    # 写盘
    shutil.copy(EQ_FILE, EQ_FILE + ".bak-expand")
    with open(EQ_FILE, "w", encoding="utf-8") as f:
        json.dump({"equipment": total}, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {EQ_FILE}（备份 .bak-expand）。主检异常数: {bad}")
    return 1 if bad else 0


# 部位 def/hp 参照（按价格档给出；商店件数值须明显低于锻造线 forge 64000=98/110，避免挤压毕业装）
DEF_HP = {120: (16, 20), 1000: (38, 50), 9000: (60, 75), 26000: (82, 100)}
ROBE_DH = {600: (14, 34), 45000: (70, 130)}
SHIELD_DH = {9000: (52, 60), 45000: (95, 105)}
FOCUS_DH = {50: (2, 4), 200: (6, 10), 1000: (16, 22), 5200: (38, 46),
            15000: (70, 85), 45000: (120, 130)}
ACC_DH = {50: (2, 4), 120: (6, 10)}

# 命名表（专属意象名，不与材料/称号撞）
WEAPON_NAMES = {5200: "破阵战刃", 15000: "裂穹大剑", 26000: "苍穹巨刃"}
STAFF_NAMES = {50: "见习法杖", 120: "精木法杖", 350: "风暴法杖", 1000: "秘银法杖",
               1700: "寒霜法杖", 3000: "星辉法杖", 5200: "狱炎法杖", 9000: "天雷法杖",
               15000: "灭世法杖", 26000: "苍穹权杖", 45000: "神谕圣杖"}


if __name__ == "__main__":
    sys.exit(main())
