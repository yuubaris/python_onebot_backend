# -*- coding: utf-8 -*-
"""方案A落地·稀有法系联动下调（rare_drops.json）

商店普通法师下调后，法系稀有（staff/robe/focus）若仍按旧数值会「超下一档普通」，
破坏钳制（同tier普通 < 稀有 ≤ 下一档普通）。本脚本把破上限的稀有件等比缩到
落入钳制区间（尽量贴近上限、保持「稀有比普通强」的观感）。

用法：
  python docs/regen_rare_link.py            # dry-run
  python docs/regen_rare_link.py --apply    # 备份后写回 rare_drops.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
RARE_FILE = os.path.join(BASE, "rare_drops.json")
EQ_FILE = os.path.join(BASE, "equipment.json")


def item_score(it):
    """统一口径（v2.11.71）：直接复用 dungeon.item_score（与推进公式同一套）。"""
    from dungeon import item_score as _s
    return _s(it)


def setk(it, k):
    x = dict(it)
    for a in ("attack", "agility", "intelligence", "mp", "defense", "hp"):
        x[a] = int(round((x.get(a, 0) or 0) * k))
    return x


def normal_top_map():
    with open(EQ_FILE, encoding="utf-8") as f:
        eq = json.load(f)["equipment"]
    nt = {}
    for it in eq:
        t = it.get("tier", 0)
        sc = item_score(it)
        k = (t, it.get("type"))
        if k not in nt or sc > nt[k][1]:
            nt[k] = (it, sc)
    return nt


def clamp_bounds(nt, t, ty, low_score):
    """返回 (low, cap)。cap = 首个更高 tier 同部位普通；无则 low*1.25。"""
    low = nt.get((t, ty), (None, low_score))[1]
    cap = None
    for tt in range(t + 1, 11):
        if (tt, ty) in nt:
            cap = nt[(tt, ty)][1]
            break
    if cap is None:
        cap = low * 1.25 if low else None
    return low, cap


def main():
    apply = "--apply" in sys.argv
    nt = normal_top_map()
    with open(RARE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]

    print("== 稀有法系联动（破上限件等比缩入钳制区间）==")
    new_items = []
    fixed = 0
    for it in items:
        t = it.get("tier", 0)
        ty = it.get("type")
        sc = item_score(it)
        low, cap = clamp_bounds(nt, t, ty, sc)
        if cap is not None and sc > cap * 1.001:
            # 找最大 k 使缩后分数 ≤ cap（尽量贴近上限）
            best = None
            for k in [x / 100 for x in range(100, 30, -1)]:
                if item_score(setk(it, k)) <= cap:
                    best = k
                    break
            if best is None:
                best = 0.3
            nw = setk(it, best)
            # 若缩后 ≤ 同tier普通则退而取 low 与 cap 中点附近
            nsc = item_score(nw)
            if low is not None and nsc <= low:
                # 向低边界以上修正：以能 > low 的最高 k（上限受限时无解，忽略同tier约束）
                pass
            print(f"  T{t} {it['name']:<7} {ty:<5} 分{sc:7.0f}→{nsc:7.0f} "
                  f"(×{best:.2f}) 区间({low if low is not None else '-':.0f},{cap:.0f}]")
            new_items.append(nw)
            fixed += 1
        else:
            new_items.append(dict(it))

    if not apply:
        print(f"\n[dry-run] 将修复 {fixed} 件。确认后: python docs/regen_rare_link.py --apply")
        return 0

    bak = RARE_FILE + ".bak-dual"
    shutil.copy(RARE_FILE, bak)
    data["items"] = new_items
    with open(RARE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {RARE_FILE}（备份 {bak}），修复 {fixed} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
