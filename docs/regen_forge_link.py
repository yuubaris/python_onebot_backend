# -*- coding: utf-8 -*-
"""方案A落地·锻造法师线联动（forge.json，铁匠铺毕业线）

dungeon.py 保留乘区 + 输出权重对称口径。锻造每 Lv 各部位 1 件（同价成套），
法师线(staff/robe/focus)在新对称公式下相对战士(weapon/armor/shield)虚高：
实测 Lv1 +32% … Lv4 +5%（staff 虚高最多）。

落地：
  A. staff 逐 Lv 对齐 weapon（同 Lv 锻造主手收益分相近）；
  B. robe/focus 等比缩，使法师该 Lv 3+1 ≈ 战士该 Lv 3+1（±3~5%）。

用法：
  python docs/regen_forge_link.py            # dry-run
  python docs/regen_forge_link.py --apply    # 备份后写回 forge.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
FORGE_FILE = os.path.join(BASE, "forge.json")

WAR = ("weapon", "armor", "shield")
MAG = ("staff", "robe", "focus")


def item_score(it):
    a = it.get("attack", 0)
    mp = it.get("mp", 0)
    g = it.get("agility", 0)
    i = it.get("intelligence", 0)
    d = it.get("defense", 0)
    h = it.get("hp", 0)
    off = a * 2 + mp * 2 + g * 1.5 + i * 1.5
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def setk(it, k):
    x = dict(it)
    for a in ("attack", "agility", "intelligence", "mp", "defense", "hp"):
        x[a] = int(round((x.get(a, 0) or 0) * k))
    return x


def main():
    apply = "--apply" in sys.argv
    with open(FORGE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    fg = data.get("recipes", [])

    # 分组
    def fget(lv, ty):
        return next((r for r in fg if r.get("level") == lv and r.get("type") == ty), None)

    def speed(parts, t=5):
        # 只做相对比较（同一 tier 称号一致），叠加 base + 称号近似足够
        st = {"attack": 5, "defense": 2, "hp": 20, "mp": 5,
              "agility": 5, "intelligence": 5}
        for it in parts:
            if it:
                for k in st:
                    st[k] += it.get(k, 0) or 0
        b = {0: 1.0, 1: 1.05, 2: 1.1, 3: 1.15, 4: 1.2, 5: 1.25}[t]
        for k in st:
            st[k] *= b
        off = st["attack"] * 2 + st["mp"] * 2 + st["agility"] * 1.5 + st["intelligence"] * 1.5
        return off * (1 + st["defense"] / 400) * (1 + st["hp"] / 600)

    print("== 锻造法师联动 ==")
    out = []
    for lv in (1, 2, 3, 4):
        w = [fget(lv, x) for x in WAR]
        m = [fget(lv, x) for x in MAG]
        acc = fget(lv, "accessory")
        if not all(w) or not all(m):
            continue
        rw = speed([x for x in w] + ([acc] if acc else []))

        # A. staff 对齐同 Lv weapon
        staff = m[0]
        tw = max(item_score(x) for x in w if x.get("type") == "weapon")
        cur = item_score(staff)
        if cur > tw * 1.01:
            best = None
            for k in [x / 100 for x in range(100, 30, -1)]:
                if item_score(setk(staff, k)) <= tw * 1.05:
                    best = k
                    break
            staff2 = setk(staff, best) if best else dict(staff)
        else:
            staff2 = dict(staff)

        # B. robe/focus 收敛法师≈战士
        rob, foc = m[1], m[2]
        best = None
        for kr in [x / 100 for x in range(30, 101)]:
            for kf in [x / 100 for x in range(30, 101)]:
                mb = [staff2, setk(rob, kr), setk(foc, kf)] + ([acc] if acc else [])
                rm = speed(mb)
                if rm <= rw * 1.05 and rm >= rw * 0.95:
                    if best is None or abs(rm - rw) < abs(best[0] - rw) - 1e-6:
                        best = (rm, kr, kf)
        if best is None:
            best = (0, 0.5, 0.5)
            rm = speed([staff2, setk(rob, best[1]), setk(foc, best[2])] + ([acc] if acc else []))
            best = (rm, best[1], best[2])
        rm, kr, kf = best
        rob2 = setk(rob, kr)
        foc2 = setk(foc, kf)
        print(f"  Lv{lv}: staff {cur:.0f}→{item_score(staff2):.0f} "
              f"robe×{kr:.2f} focus×{kf:.2f} 战{rw:.0f} 法{rm:.0f} 差{(rm-rw)/rw*100:+.0f}%")
        # 替换
        for it in fg:
            if it["name"] == staff["name"]:
                it.update(staff2)
            elif it["name"] == rob["name"]:
                it.update(rob2)
            elif it["name"] == foc["name"]:
                it.update(foc2)

    if not apply:
        print("\n[dry-run] 未写盘。确认后: python docs/regen_forge_link.py --apply")
        return 0

    bak = FORGE_FILE + ".bak-dual"
    shutil.copy(FORGE_FILE, bak)
    with open(FORGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已写盘 {FORGE_FILE}（备份 {bak}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
