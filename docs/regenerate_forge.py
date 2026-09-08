# -*- coding: utf-8 -*-
"""锻造配方数值重校生成器（开发期用，不入库）。

依据 docs/tier-forge-title-rework-request.md（用户 2026-09-08 确认）：
- 锻造强度 Lv1 = 商店 T4.5（每部位以 商店该部位 T4 顶配与 T5 顶配的收益分中点为准）；
- Lv2/Lv3/Lv4 按等比 ~1.30 平滑递增（单件收益分口径；四件套合成战力等比 ~1.52；整体覆盖 商店T4.5 ~ T7 级毕业带）；
- 修复原 Lv3↔Lv4 数值倒挂；价格随强度联动下调（避免“贵却弱”）。

用法：
  python docs/regenerate_forge.py            # dry-run：只打印预览与校验
  python docs/regenerate_forge.py --apply    # 备份并写回 forge.json
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")
FG_FILE = os.path.join(BASE, "forge.json")
sys.path.insert(0, os.path.join(BASE, "docs"))
from fill_rare_pool import fit_phys, fit_magic, fit_armor  # noqa: E402

RATIO = 1.30          # 每级强度等比系数（单件收益分口径，Lv1=商店T4.5 起步；四件套合成战力等比 ~1.52）
# 价格联动（每 Lv 统一价，随强度下调；Lv1 介于商店 T4/T5 之间，Lv4 约 20万+）
PRICE_BY_LV = {1: 32000, 2: 60000, 3: 120000, 4: 260000}
# 锻造装备 tier（语义/展示档；实际穿戴以铁匠铺 Lv 解锁层为准，见 dungeon.effective_stats）
TIER_BY_LV = {1: 5, 2: 5, 3: 6, 4: 6}


def item_score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def main():
    apply = "--apply" in sys.argv
    eq = json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]
    fg = json.load(open(FG_FILE, encoding="utf-8")).get("recipes", [])

    # 商店每 (tier,type) 顶配分
    top = {}
    for it in eq:
        k = (int(it.get("tier", 0)), it.get("type"))
        s = item_score(it)
        if k not in top or s > top[k][1]:
            top[k] = (it, s)

    # 每 type 的 T4.5 基准 = 商店 T4 顶配分 与 T5 顶配分 的中点
    t45 = {}
    for ty in ("weapon", "staff", "armor", "shield", "robe", "focus", "accessory"):
        if (4, ty) in top and (5, ty) in top:
            t45[ty] = (top[(4, ty)][1] + top[(5, ty)][1]) / 2.0
    if not t45:
        print("商店缺少 T4/T5 基准，无法重校")
        return

    def target(lv, ty):
        return t45[ty] * (RATIO ** (lv - 1))

    out = []
    for r in fg:
        lv = int(r.get("level", 1)); ty = r.get("type")
        if ty not in t45:
            continue
        tgt = target(lv, ty)
        d = r.get("defense", 0); h = r.get("hp", 0)
        nr = dict(r)
        if ty == "armor":
            nd, nh = fit_armor(tgt, d or 1, h or 1)
            nr.update(defense=nd, hp=nh)
        elif ty in ("staff", "focus", "robe"):
            rm = 0.5 if ty != "robe" else 0.35
            i, m = fit_magic(tgt, d, h, ratio_m=rm)
            nr.update(intelligence=i, mp=m, attack=0, agility=0)
        elif ty == "weapon":
            a, g = fit_phys(tgt, d, h, ratio_g=0.16)
            nr.update(attack=a, agility=g, intelligence=0, mp=0)
        elif ty == "shield":
            a, g = fit_phys(tgt, d, h, ratio_g=0.25)
            nr.update(attack=a, agility=g)
        else:  # accessory：攻/敏 与 智/魔 各半（通用件）
            a, g = fit_phys(tgt * 0.5, d, h, ratio_g=0.5)
            i, m = fit_magic(tgt * 0.5, d, h, ratio_m=0.5)
            nr.update(attack=a, agility=g, intelligence=i, mp=m)
        nr["price"] = PRICE_BY_LV[lv]
        nr["tier"] = TIER_BY_LV[lv]
        out.append(nr)

    # ---- 校验 ----
    by_ty = {}
    max_err = 0.0
    for r in out:
        lv = int(r.get("level", 1)); ty = r.get("type")
        want = target(lv, ty); got = item_score(r)
        err = abs(got - want) / want * 100
        max_err = max(max_err, err)
        by_ty.setdefault(ty, {})[lv] = got
    print(f"重校完成 {len(out)} 件 | 最大偏差 {max_err:.2f}%")
    mono_ok = True
    for ty in sorted(by_ty):
        seq = [round(by_ty[ty][lv], 1) for lv in (1, 2, 3, 4)]
        ok = all(seq[i] < seq[i + 1] for i in range(3))
        mono_ok &= ok
        print(f"  {ty:<10} {'→'.join(map(str, seq))} {'✓' if ok else '异常!'}")

    if not apply:
        print("\n[dry-run] 未写回；加 --apply 生效")
        return
    if not mono_ok or max_err > 3:
        print("\n校验未通过，中止写回")
        return
    backup = FG_FILE + ".bak"
    shutil.copyfile(FG_FILE, backup)
    with open(FG_FILE, "w", encoding="utf-8") as f:
        json.dump({"recipes": out}, f, ensure_ascii=False, indent=2)
    print(f"\n已写回 {FG_FILE}（备份 {backup}）")


if __name__ == "__main__":
    main()
