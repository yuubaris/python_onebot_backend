# -*- coding: utf-8 -*-
"""方案A落地·商店法师数值下调（v3 最终版，带跨tier单调约束）

dungeon.py 已改为「保留生存乘区 + 输出权重对称」（攻=魔=2、敏=智=1.5）——即本脚本口径。

落地方式：
  A. staff 逐「价格档」对齐 weapon：法师主手智/魔数值虚高，逐档缩到与同价剑同收益分
     （消除同价主手强偏差）；
  B. robe/focus 顺序贪心（T0→T5）带跨tier单调约束：每 tier 顶配分不得低于上一 tier
     （避免 T5 缩得比 T4 狠造成倒挂，_stats_for_line 会误穿低阶袍），
     目标让法师该 tier 3+1 真实推进尽量贴近战士（允许法师略低 0~-5%，不倒挂优先）。

用法：
  python docs/regen_dual_balance.py            # dry-run
  python docs/regen_dual_balance.py --apply    # 备份后写回 equipment.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
EQ_FILE = os.path.join(BASE, "equipment.json")

WAR_TYPES = ("weapon", "armor", "shield")
MAG_TYPES = ("staff", "robe", "focus")
BASE_STATS = {"attack": 5, "defense": 2, "hp": 20, "mp": 5,
              "agility": 5, "intelligence": 5}
TIER_BONUS = {0: 1.0, 1: 1.05, 2: 1.1, 3: 1.15, 4: 1.2, 5: 1.25}


def item_score(it):
    """统一口径（v2.11.71）：直接复用 dungeon.item_score（与推进公式同一套）。"""
    from dungeon import item_score as _s
    return _s(it)


def setk(it, k):
    x = dict(it)
    for a in ("attack", "agility", "intelligence", "mp", "defense", "hp"):
        x[a] = int(round((x.get(a, 0) or 0) * k))
    return x


def top(pool, ty, t):
    c = [it for it in pool if it.get("type") == ty and it.get("tier") == t]
    return max(c, key=lambda x: x.get("price", 0)) if c else None


def speed_of(parts, t):
    """真实推进（手动叠加 base+装备 ×称号 → dungeon_speed），只算给定顶配件。"""
    from dungeon import dungeon_speed
    st = dict(BASE_STATS)
    for it in parts:
        if it:
            for k in st:
                st[k] += it.get(k, 0) or 0
    b = TIER_BONUS.get(t, 1.0)
    if b != 1.0:
        for k in st:
            st[k] *= b
    return dungeon_speed(st)


def main():
    apply = "--apply" in sys.argv
    with open(EQ_FILE, encoding="utf-8") as f:
        data = json.load(f)
    eq = data["equipment"]

    # A. staff 逐价格档对齐 weapon
    out = [dict(it) for it in eq]
    by = {}
    for it in out:
        by.setdefault(int(it.get("price", 0)), []).append(it)
    for p in sorted(by):
        arr = by[p]
        w = [x for x in arr if x.get("type") == "weapon"]
        for st in [x for x in arr if x.get("type") == "staff"]:
            if not w:
                continue
            target = max(item_score(x) for x in w)
            if item_score(st) <= target * 1.01:
                continue
            best = None
            for k in [x / 100 for x in range(100, 30, -1)]:
                if item_score(setk(st, k)) <= target * 1.05:
                    best = k
                    break
            for idx, it in enumerate(out):
                if it is st:
                    out[idx] = setk(st, best) if best else dict(st)

    # B. 顺序贪心 robe/focus（带单调下限 + 档间最低增长，供稀有钳制留空间）
    print("== 商店法师下调（A: staff对齐 + B: robe/focus 顺序贪心）==")
    prev_robe = prev_focus = None  # 上一 tier 顶配分
    result = {}
    for t in range(6):
        w = [top(out, x, t) for x in WAR_TYPES]
        m = [top(out, x, t) for x in MAG_TYPES]
        acc = top(out, "accessory", t)
        if not all(w) or not all(m):
            print(f"  T{t}: 缺件")
            continue
        wb = [x for x in w] + ([acc] if acc else [])
        rw = speed_of(wb, t)
        rob0, foc0 = m[1], m[2]
        staff0 = m[0]
        # 单调下限：本 tier 顶配分 ≥ 上一 tier 顶配分 × 1.03（档间最小增长，供稀有钳制插入）
        lo_kr = 0.30
        lo_kf = 0.30
        if prev_robe is not None:
            for kr in [x / 100 for x in range(30, 101)]:
                if item_score(setk(rob0, kr)) >= prev_robe * 1.03:
                    lo_kr = kr
                    break
        if prev_focus is not None:
            for kf in [x / 100 for x in range(30, 101)]:
                if item_score(setk(foc0, kf)) >= prev_focus * 1.03:
                    lo_kf = kf
                    break

        # 找 (kr,kf) 使法师3+1 最接近战士且 ≤战士×1.03、≥战士×0.95；找不到则取单调下限
        best = None
        for kr in [x / 100 for x in range(30, 101)]:
            if kr < lo_kr - 0.005:
                continue
            for kf in [x / 100 for x in range(30, 101)]:
                if kf < lo_kf - 0.005:
                    continue
                mb = [staff0, setk(rob0, kr), setk(foc0, kf)] + ([acc] if acc else [])
                rm = speed_of(mb, t)
                if rm <= rw * 1.03 and rm >= rw * 0.95:
                    if best is None:
                        best = (rm, kr, kf)
                    else:
                        if abs(rm - rw) < abs(best[0] - rw) - 1e-6:
                            best = (rm, kr, kf)
        if best is None:
            # 无解：取单调下限（宁法师略低，也不压缩档间成长/倒挂）
            kr = max(lo_kr, 0.30)
            kf = max(lo_kf, 0.30)
            mb = [staff0, setk(rob0, kr), setk(foc0, kf)] + ([acc] if acc else [])
            rm = speed_of(mb, t)
            best = (rm, kr, kf)
        rm, kr, kf = best
        prev_robe = item_score(setk(rob0, kr))
        prev_focus = item_score(setk(foc0, kf))
        result[t] = (kr, kf)
        print(f"  T{t}: robe×{kr:.2f} focus×{kf:.2f} 战{rw:.0f} 法{rm:.0f} "
              f"差{(rm-rw)/rw*100:+.1f}%")

    # 应用
    new_eq = []
    for it in out:
        if it.get("type") in ("robe", "focus"):
            kr, kf = result.get(it.get("tier", 0), (1.0, 1.0))
            k = kr if it.get("type") == "robe" else kf
            new_eq.append(setk(it, k) if k < 1 else dict(it))
        else:
            new_eq.append(dict(it))

    if not apply:
        print("\n[dry-run] 未写盘。确认后: python docs/regen_dual_balance.py --apply")
        return 0

    bak = EQ_FILE + ".bak-dual3"
    shutil.copy(EQ_FILE, bak)
    data["equipment"] = new_eq
    with open(EQ_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {EQ_FILE}（备份 {bak}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
