# -*- coding: utf-8 -*-
"""方案A·法师下调生成器（按用户三条口径 2026-09-08）

用户口径：
1. 收益分「相近」即可（同级 3+1 法师≈战士，±3~5% 可接受，不追求逐档精确）。
2. 若同级装备数量已对称（T3/T4/T5）→ 调整装备即可，不新增件、不改商店结构；
   价格不参与推进/收益分，故法师必须「数值下调」，价格仅用于同强度同价观感（可选）。
3. 收益分权重对称：攻击=魔力、敏捷=智力（攻=魔=2.0、敏=智=1.5）；
   防/命给线性权重（草案 防=1.0、命=0.6，可调）。

⚠️ 运营级提示：要让「对称收益分」真正等于玩家推进，需同步把 dungeon.dungeon_speed
   从非线性乘区公式改为「对称线性可加」。这是影响所有在线玩家的变更——本脚本默认
   只改数值(dry-run/apply 到 equipment.json)，dungeon_speed 的改造另列 checklist，
   需确认后单独执行。未改公式前，法师按对称口径下调会导致旧公式真实推进偏低
   （法师 -7%~-18%），属预期中的「口径未对齐」阶段产物。

用法：
  python docs/regen_linear_score.py            # dry-run
  python docs/regen_linear_score.py --apply    # 备份后写回 equipment.json（法师数值下调）
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")

# 用户第3条对称权重（平衡口径）
W = {"attack": 2.0, "mp": 2.0, "agility": 1.5, "intelligence": 1.5,
     "defense": 1.0, "hp": 0.6}
MAG_TYPES = ("staff", "robe", "focus")
WAR_TYPES = ("weapon", "armor", "shield")


def lscore(it):
    return sum(it.get(k, 0) * W[k] for k in W)


def setk(it, k):
    x = dict(it)
    for a in W:
        x[a] = int((x.get(a, 0) or 0) * k)
    return x


def top(pool, ty, t):
    c = [it for it in pool if it.get("type") == ty and it.get("tier") == t]
    return max(c, key=lambda x: x.get("price", 0)) if c else None


def solve_k(eq, t):
    """法师 3+1 ≈ 战士 3+1（对称口径, ±3%）, 返回最大温和 k。"""
    acc = top(eq, "accessory", t)
    a_l = lscore(acc) if acc else 0
    w = [top(eq, x, t) for x in WAR_TYPES]
    m = [top(eq, x, t) for x in MAG_TYPES]
    if not all(w) or not all(m):
        return 1.0
    sw = sum(lscore(x) for x in w) + a_l
    best = None
    for k in [x / 100 for x in range(35, 101)]:
        sm = sum(lscore(setk(it, k)) for it in m) + a_l
        if sw * 0.97 <= sm <= sw * 1.03:
            best = k
            break
    return best if best else 0.35


def main():
    apply = "--apply" in sys.argv
    eq = json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]

    kmap = {}
    print("== 法师下调收敛系数（对称口径, 法师3+1≈战士3+1 ±3%）==")
    for t in range(6):
        k = solve_k(eq, t)
        kmap[t] = k
        acc = top(eq, "accessory", t); a_l = lscore(acc) if acc else 0
        w = [top(eq, x, t) for x in WAR_TYPES]
        m = [top(eq, x, t) for x in MAG_TYPES]
        if not all(w) or not all(m):
            print(f"  T{t}: 缺件"); continue
        sw = sum(lscore(x) for x in w) + a_l
        sm = sum(lscore(setk(it, k)) for it in m) + a_l
        print(f"  T{t}: k={k:.2f}  战{sw:.0f} → 法{sm:.0f} ({(sm - sw) / sw * 100:+.1f}%)")

    new_eq = []
    for it in eq:
        if it.get("type") in MAG_TYPES:
            new_eq.append(setk(it, kmap.get(it.get("tier", 0), 1.0)))
        else:
            new_eq.append(dict(it))

    print("\n== 法师 staff/robe/focus 商店件 old→new（对称收益分）==")
    for it in eq:
        if it.get("type") in MAG_TYPES:
            k = kmap.get(it.get("tier", 0), 1.0)
            n = setk(it, k)
            old = (it.get("intelligence", 0), it.get("mp", 0), it.get("defense", 0), it.get("hp", 0))
            new = (n.get("intelligence", 0), n.get("mp", 0), n.get("defense", 0), n.get("hp", 0))
            if old != new:
                print(f"  T{it.get('tier')} {it['name']:<6} ×{k} 智{old[0]}→{new[0]} "
                      f"魔{old[1]}→{new[1]} 防{old[2]}→{new[2]} 命{old[3]}→{new[3]} "
                      f"分{lscore(it):.0f}→{lscore(n):.0f}")

    if not apply:
        print("\n[dry-run] 未写盘。确认后运行: python docs/regen_linear_score.py --apply")
        return 0

    bak = EQ_FILE + ".bak-lin"
    shutil.copy(EQ_FILE, bak)
    with open(EQ_FILE, "w", encoding="utf-8") as f:
        json.dump({"equipment": new_eq}, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {EQ_FILE}（备份 {bak}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
