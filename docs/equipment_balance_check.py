# -*- coding: utf-8 -*-
"""装备平衡审计 / 校验脚本（开发期用，不入库）。

功能：
1. 稀有装备强度钳制：同 tier 普通顶配 < 稀有 ≤ tier+1 普通顶配（Boss 掉落专属）。
2. 职业线内收益排序合理性（同档多选偏差 <3% 属于目标，此处打印告警供人工平衡）。

用法：python docs/equipment_balance_check.py
（把脚本放项目根运行时请用： python equipment_balance_check.py 或将本目录加入 sys.path）
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def load_json(name):
    with open(os.path.join(BASE_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def item_score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def tier_of(it):
    t = it.get("tier")
    return int(t) if t is not None else 0


def main():
    eq = load_json("equipment.json")["equipment"]
    forge = load_json("forge.json").get("recipes", [])
    try:
        rare = load_json("rare_drops.json").get("items", [])
    except Exception:
        rare = []

    # 钳制基准只取「商店普通」equipment（铁匠铺为毕业线，不参与“稀有≤下一档普通”比较）
    normal_top = {}
    for it in eq:
        t = tier_of(it)
        sc = item_score(it)
        key = (t, it.get("type"))
        if key not in normal_top or sc > normal_top[key][1]:
            normal_top[key] = (it, sc)

    print("== 稀有装备强度钳制检查（同tier普通 < 稀有 ≤ 下一档普通）==")
    problems = 0
    max_tier = 10
    for r in rare:
        t = tier_of(r)
        sc = item_score(r)
        ty = r.get("type")
        low = normal_top.get((t, ty), (None, 0))[1]
        # cap = 下一个「更高 tier 中同部位普通」顶配（不含同tier，避免自相矛盾）
        cap = None
        for tt in range(t + 1, max_tier + 1):
            if (tt, ty) in normal_top:
                cap = normal_top[(tt, ty)][1]
                break
        if cap is None:
            cap = low * 1.25 if low else None
        flag = ""
        if low and sc <= low:
            flag = "  ❌ 弱于同tier普通(≤%.1f)" % low
        if cap and sc > cap:
            flag += "  ❌ 超下一档(>%.1f)" % cap
        if flag:
            problems += 1
        print(f"  {r['name']:<8} T{t} 分{sc:8.1f} | 同tier基准{low:8.1f} | 下档上限{cap if cap is not None else '∞':>8} {flag}")
    print(f"== 稀有装备问题数: {problems} ==")

    print()
    print("== 同价格档跨职业偏差（同价 weapon vs staff，偏差>3% 提示，>15% 强烈告警）==")
    # 按「价格档」分组（而非 tier：tier 覆盖多档价格，跨价比无意义）
    by_price = {}
    for it in eq + forge:
        p = it.get("price")
        if it.get("type") in ("weapon", "staff") and p is not None:
            by_price.setdefault(int(p), []).append(it)
    warn = 0
    for p in sorted(by_price):
        arr = by_price[p]
        if len(arr) < 2:
            continue
        scores = [item_score(x) for x in arr]
        base = max(scores)
        for x, s in zip(arr, scores):
            dev = (base - s) / base * 100 if base else 0
            if dev > 15:
                warn += 1
                print(f"  ¥{p} {x['name']:<8} 分{s:8.1f} 与同价最强差 {dev:.1f}%  ⚠️")
            elif dev > 3:
                print(f"  ¥{p} {x['name']:<8} 分{s:8.1f} 与同价最强差 {dev:.1f}%")
    print(f"== 同价格档武器/法杖强偏差数: {warn} ==")
    print("提示：>3% 属同档多选等价目标范围外，实施时在数值表中微调；>15% 建议改数值。")


if __name__ == "__main__":
    main()
