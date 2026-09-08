# -*- coding: utf-8 -*-
"""稀有装备池生成器（开发期用，不入库）。

依据 docs/dungeon-boss-plan.md §4.3 铁律：
- 稀有装备：商店（/武器库 /铁匠铺）买不到，仅 Boss 关掉落；
- 强度钳制：同tier普通顶配 < 稀有(tier) ≤ tier+1普通顶配（同部位对比）；
- 每 tier 提供 战士主手(weapon) / 法师主手(staff) / 通用饰品(accessory any)，
  转职后可掉本职业主手+any，未转职可掉 any（保证各阶段 Boss 都有稀有望）。
- 独立专属命名，不与 武器库/铁匠铺/称号前缀 撞车；展示端加 ✦ 与 (稀有)。

用法：
  python docs/fill_rare_pool.py          # dry-run：只打印生成预览与钳制校验
  python docs/fill_rare_pool.py --apply   # 备份并写回 rare_drops.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")
FG_FILE = os.path.join(BASE, "forge.json")
RARE_FILE = os.path.join(BASE, "rare_drops.json")

MAX_TIER = 6


def item_score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def normal_top(eq):
    """每 (tier,type) 普通顶配收益。"""
    top = {}
    for it in eq:
        k = (int(it.get("tier", 0)), it.get("type"))
        s = item_score(it)
        if k not in top or s > top[k][1]:
            top[k] = (it["name"], s)
    return top


def clamp_target(top, t, typ, boost=1.12, boost_cap=None):
    """稀有目标收益 = 同tier顶配×boost，且 ≤ 下一个更高 tier 同 type 顶配。
    返回 (target, low, cap)。"""
    low = top.get((t, typ), (None, 0))[1]
    cap = None
    for tt in range(t + 1, MAX_TIER + 2):
        if (tt, typ) in top:
            cap = top[(tt, typ)][1]
            break
    if cap is None:
        cap = low * 1.25 if low else None
    target = low * (boost_cap if boost_cap else boost)
    if cap is not None:
        target = min(target, cap * 0.99)
    return target, low, cap


def fit_phys(target, def_, hp, ratio_g=0.16):
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
    _, a, g = best
    return a, g


def fit_magic(target, def_, hp, ratio_m=0.5):
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
    _, i, m = best
    return i, m


# 每 tier 稀有命名（独立意象，不与商店/锻造/称号前缀撞车）
# 格式: (type, line, 名称)
PLAN = {
    0: [("weapon", "physical", "尘封战刃"),
        ("staff", "magic", "尘封法杖"),
        ("accessory", "any", "尘封护符")],
    1: [("weapon", "physical", "踏风战刃"),
        ("staff", "magic", "踏风秘杖"),
        ("accessory", "any", "踏风护符")],
    2: [("weapon", "physical", "熔岩斩刃"),
        ("staff", "magic", "熔岩法杖"),
        ("accessory", "any", "熔岩护印")],
    3: [("weapon", "physical", "月蚀巨刃"),
        ("staff", "magic", "月蚀权杖"),
        ("accessory", "any", "月蚀吊坠")],
    4: [("weapon", "physical", "星穹圣刃"),
        ("staff", "magic", "星穹权杖"),
        ("accessory", "any", "星穹法印")],
    5: [("weapon", "physical", "神陨之刃"),
        ("staff", "magic", "神陨圣杖"),
        ("accessory", "any", "神陨圣印")],
}


def make_item(iid, name, typ, tier, line, price, a, g, i, m, d, h):
    return {
        "id": iid, "name": name, "type": typ,
        "attack": a, "defense": d, "hp": h, "mp": m,
        "agility": g, "intelligence": i,
        "price": price, "line": line, "tier": tier,
    }


def main():
    apply = "--apply" in sys.argv
    eq = json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]
    fg = json.load(open(FG_FILE, encoding="utf-8")).get("recipes", [])
    top = normal_top(eq)
    # 禁用名集合（商店 + 锻造 现在/改名后 + 称号前缀）
    used_names = {it["name"] for it in eq} | {r["name"] for r in fg}
    used_ids = {it["id"] for it in eq} | {r["id"] for r in fg}
    title_prefix = {"见习", "疾风", "烈焰", "银辉", "苍穹", "神谕", "灭世"}

    items = []
    problems = []
    for tier in sorted(PLAN):
        for typ, line, name in PLAN[tier]:
            target, low, cap = clamp_target(top, tier, typ)
            if not low:
                problems.append(f"T{tier} {typ} 无同tier普通基准，跳过")
                continue
            if name in used_names:
                problems.append(f"名字撞车: {name}")
                continue
            # 生成（防御/生命随 tier 小幅递增，作为稀有“生存向”点缀）
            d = 2 + tier * 4
            h = 6 + tier * 10
            if typ == "weapon":
                a, g = fit_phys(target, d, h)
                i, m = 0, 0
                extra = {"attack": a, "agility": g, "intelligence": 0, "mp": 0}
            elif typ == "staff":
                i, m = fit_magic(target, d, h)
                a, g = 0, 0
                extra = {"attack": 0, "agility": 0, "intelligence": i, "mp": m}
            else:  # accessory 混合
                i, m = fit_magic(target * 0.55, d, h)
                a, g = fit_phys(target * 0.45, d, h)
                extra = {"attack": a, "agility": g, "intelligence": i, "mp": m}
            iid = f"rare_{typ}_{tier}"
            if iid in used_ids:
                k = 2
                while f"{iid}_{k}" in used_ids:
                    k += 1
                iid = f"{iid}_{k}"
            used_ids.add(iid); used_names.add(name)
            it = make_item(iid, name, typ, tier, line, 0, extra["attack"],
                           extra["agility"], extra["intelligence"], extra["mp"],
                           d, h)
            items.append(it)
            sc = item_score(it)
            flag = ""
            if sc <= low:
                flag = " ❌弱于同tier"
            if cap and sc > cap:
                flag += f" ❌超下一档({cap:.0f})"
            if flag:
                problems.append(f"T{tier} {name} {flag}")
            print(f"  T{tier} {typ:10s} {name:<6} score={sc:7.1f} | 同tier {low:6.1f} → 下档 {cap if cap else '∞':>6}")

    if problems:
        print("\n⚠️ 问题：")
        for p in problems:
            print("  -", p)
        if not apply:
            print("\n[dry-run] 有异常，未写盘。")
            return 1

    if not apply:
        print(f"\n[dry-run] 将生成 {len(items)} 件稀有。加 --apply 落盘。")
        return 0

    shutil.copy(RARE_FILE, RARE_FILE + ".bak-fill")
    with open(RARE_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    print(f"\n已写盘 {RARE_FILE}（备份 .bak-fill），共 {len(items)} 件稀有。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
