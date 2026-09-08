# -*- coding: utf-8 -*-
"""修复「肉装化」主手装备（开发期脚本，可入库保留；equipment.json 会备份再改写）。

问题：商店高价格档 weapon/staff 主手被塞成 高防/高命 + 低输出（如 神谕圣杖 命619），
与 稀有/锻造 对应主手（输出为主、防命正常）形态严重失衡、观感离谱。

修复策略：把「偏肉」主手重排为健康输出形态 —— 参考同 tier 稀有主手的 防/命 作为
「健康生存预算」，保持装备总分不变，反推 攻/敏(物理) 或 智/魔(魔法) 输出词条。
这样：总分/稀有钳制/同价 weapon≈staff 对齐 均不受影响，仅形态恢复输出为主。

用法：
  python docs/fix_tanky_equipment.py          # dry-run：打印规划，不写盘
  python docs/fix_tanky_equipment.py --apply  # 备份并写回 equipment.json
"""
import json
import math
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")
RARE_FILE = os.path.join(BASE, "rare_drops.json")


def load(n, key):
    with open(n, encoding="utf-8") as f:
        return json.load(f)[key]


def score(it):
    a = it.get("attack", 0); g = it.get("agility", 0)
    i = it.get("intelligence", 0); m = it.get("mp", 0)
    d = it.get("defense", 0); h = it.get("hp", 0)
    off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
    if off <= 0:
        return (d * 2 + h * 1.2) * 1.0
    return off * (1 + d / 400) * (1 + h / 600)


def fit_phys(target, def_, hp, ratio_g=0.16):
    """物理主手：给定健康 def/hp，反推 (attack, agility) 使收益≈target。"""
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    a0 = int(main_t / (2 + 1.5 * ratio_g))
    for attack in range(max(1, a0 - 12), a0 + 13):
        agility = max(0, int(round((main_t - 2 * attack) / 1.5)))
        sc = (attack * 2 + agility * 1.5) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, attack, agility)
    _, attack, agility = best
    return {"attack": attack, "agility": agility}


def fit_magic(target, def_, hp, ratio_m=0.5):
    """魔法主手：给定健康 def/hp，反推 (intelligence, mp) 使收益≈target。"""
    mul = (1 + def_ / 400) * (1 + hp / 600)
    main_t = target / mul
    best = None
    i0 = int(main_t / (1.25 + 1.2 * ratio_m))
    for inte in range(max(1, i0 - 12), i0 + 13):
        mp = max(0, int(round((main_t - 1.25 * inte) / 1.2)))
        sc = (inte * 1.25 + mp * 1.2) * mul
        err = abs(sc - target)
        if best is None or err < best[0]:
            best = (err, inte, mp)
    _, inte, mp = best
    return {"intelligence": inte, "mp": mp}


def healthy_survival(it, rare_items):
    """返回该主手应使用的「健康防/命」（参照同 tier 同 type 稀有件）；无参照用价格开方。"""
    t = it.get("tier")
    ty = it.get("type")
    refs = [r for r in rare_items if r.get("type") == ty and r.get("tier") == t]
    if refs:
        r = refs[0]
        return int(r.get("defense", 0)), int(r.get("hp", 0))
    p = it.get("price", 100)
    return int(p ** 0.5), int(p ** 0.4)


def main():
    apply = "--apply" in sys.argv
    items = load(EQ_FILE, "equipment")
    rare = load(RARE_FILE, "items")

    plans = []
    for it in items:
        ty = it.get("type")
        if ty not in ("weapon", "staff"):
            continue
        a = it.get("attack", 0); g = it.get("agility", 0)
        i = it.get("intelligence", 0); m = it.get("mp", 0)
        d = it.get("defense", 0); h = it.get("hp", 0)
        off = a * 2 + g * 1.5 + i * 1.25 + m * 1.2
        denom = off + d * 2 + h * 1.2
        ratio = off / denom if denom else 0
        # 肉装化判定：主手输出占比 <0.35（输出低、靠防/命撑分）
        if ratio >= 0.35:
            continue
        target = score(it)          # 保持当前总分
        d2, hp2 = healthy_survival(it, rare)
        if ty == "weapon":
            new = fit_phys(target, d2, hp2)
            update = {"attack": new["attack"], "agility": new["agility"],
                      "defense": d2, "hp": hp2, "mp": 0, "intelligence": 0}
        else:
            new = fit_magic(target, d2, hp2)
            update = {"intelligence": new["intelligence"], "mp": new["mp"],
                      "defense": d2, "hp": hp2, "attack": 0, "agility": 0}
        it2 = dict(it)
        it2.update(update)
        plans.append((it, it2))

    if not plans:
        print("没有需要修复的偏肉主手。")
        return
    print("== 待修复偏肉主手（dry-run 预览，--apply 写盘）==\n")
    for old, new in plans:
        def _fmt(x):
            return (f"攻{x.get('attack',0)} 防{x.get('defense',0)} 命{x.get('hp',0)} "
                    f"魔{x.get('mp',0)} 敏{x.get('agility',0)} 智{x.get('intelligence',0)} "
                    f"分{score(x):.0f}")
        print(f"¥{old['price']:<6} T{old.get('tier')} {old.get('type'):<6} {old['name']}")
        print(f"    原  {_fmt(old)}")
        print(f"    改  {_fmt(new)}")

    if not apply:
        print("\n(dry-run：未写盘。加 --apply 执行)")
        return

    # 备份 + 应用（把新值写回 items 原对象）
    backup = EQ_FILE + ".bak"
    shutil.copyfile(EQ_FILE, backup)
    for old, new in plans:
        old.clear()
        old.update(new)
    with open(EQ_FILE, "w", encoding="utf-8") as f:
        json.dump({"equipment": items}, f, ensure_ascii=False, indent=2)
    print(f"\n已写回 {EQ_FILE}（备份 {backup}），修复 {len(plans)} 件。")
    print("请随后运行 docs/equipment_balance_check.py 校验。")


if __name__ == "__main__":
    main()
