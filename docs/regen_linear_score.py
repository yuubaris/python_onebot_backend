# -*- coding: utf-8 -*-
"""方案A·线性收益分体系生成器（开发期用，默认 dry-run 不落盘）。

核心（见 docs/linear-score-rework-request.md）：
- 推进公式改为「线性可加」：speed = 攻×W_A + 敏×W_G + 智×W_I + 魔×W_M + 防×W_D + 命×W_H
- 单件收益分 = 同一线性公式 → 整套路推进 = 基础分 + Σ单件分，完全可加。
- 物理/魔法输出词条每点等值（攻=智=2.0、敏=魔=1.5）。
- 部位对称：weapon≡staff / armor≡robe / shield≡focus / accessory 通用，
  同一「收益档」每对对称部位锚定同一目标分 → 法师 3+1 == 战士 3+1。
- 每件按「部位输出/生存占比」分配目标分：
  输出预算 → 反推攻敏(物理)或智魔(魔法)；生存预算 → 反解防/命骨架。
  任何单件总分恒等于该部位该档目标（不超、不欠），保证可加校验成立。

用法：
  python docs/regen_linear_score.py           # dry-run：预览 + 校验，不写盘
  python docs/regen_linear_score.py --apply   # 备份三个 json 后写盘（需先人工确认）
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EQ_FILE = os.path.join(BASE, "equipment.json")
FG_FILE = os.path.join(BASE, "forge.json")
RARE_FILE = os.path.join(BASE, "rare_drops.json")

# ================= 核心参数（草案，见设计文档 §3） =================
# 输出词条对称权重（每点收益分）
W = {"attack": 2.0, "agility": 1.5, "intelligence": 2.0, "mp": 1.5,
     "defense": 1.0, "hp": 0.6}
K = 1.0
# 每部位「生存预算占比」（其余为输出预算）
SURV_SHARE = {
    "weapon": 0.08, "staff": 0.08,      # 主手输出核心
    "armor": 1.00,                       # 战士甲 = 纯生存
    "robe": 0.60,                        # 法袍 = 生存为主 + 部分魔法输出
    "shield": 0.55, "focus": 0.55,       # 副手半生存半输出
    "accessory": 0.35,                   # 饰品混合
}
# 部位「数值档系数」：同价格档内 该部位目标分 = 主手目标 × 系数
RATIO = {"weapon": 1.00, "staff": 1.00,
         "armor": 0.90, "robe": 0.90,
         "shield": 0.62, "focus": 0.62,
         "accessory": 0.55}
PRICES = [50, 75, 120, 200, 350, 600, 1000, 1700, 3000, 5200, 9000,
          15000, 26000, 45000]
PRICE_TIER = [(120, 0), (600, 1), (3000, 2), (9000, 3), (26000, 4), (45000, 5)]


def price_tier(p):
    for cap, t in PRICE_TIER:
        if p <= cap:
            return t
    return 5


def lscore(it):
    return (it.get("attack", 0) * W["attack"] + it.get("agility", 0) * W["agility"]
            + it.get("intelligence", 0) * W["intelligence"] + it.get("mp", 0) * W["mp"]
            + it.get("defense", 0) * W["defense"] + it.get("hp", 0) * W["hp"]) * K


def fit_surv(budget, p, kind):
    """生存预算 → 反解 (def,hp) 使 def*WD+hp*WH ≈ budget。kind 给比例风格。"""
    if budget <= 0.5:
        return 0, 0
    ratio_dh = {"plate": 0.9, "cloth": 0.6}.get(kind, 0.75)
    best = None
    d0 = int(budget / (W["defense"] + W["hp"] * ratio_dh))
    for d in range(max(0, d0 - 4), d0 + 5):
        h = max(0, int(round(d * ratio_dh)))
        err = abs((d * W["defense"] + h * W["hp"]) * K - budget)
        if best is None or err < best[0]:
            best = (err, d, h)
    if best is None:
        return 0, 0
    return best[1], best[2]


def fit_out(target_out, kind, ratio_sub=0.2):
    """输出预算 → 反推词条。kind: phys / magic / mix。"""
    if target_out <= 0.5:
        return {"attack": 0, "agility": 0, "intelligence": 0, "mp": 0}
    best = None
    if kind == "phys":
        t = target_out / K
        a0 = int(t / (W["attack"] + W["agility"] * ratio_sub))
        for atk in range(max(0, a0 - 10), a0 + 11):
            agi = max(0, int(round((t - W["attack"] * atk) / W["agility"])))
            err = abs((atk * W["attack"] + agi * W["agility"]) * K - target_out)
            if best is None or err < best[0]:
                best = (err, atk, agi, 0, 0)
        return {"attack": best[1], "agility": best[2], "intelligence": 0, "mp": 0}
    if kind == "magic":
        t = target_out / K
        i0 = int(t / (W["intelligence"] + W["mp"] * ratio_sub))
        for inte in range(max(0, i0 - 10), i0 + 11):
            mp = max(0, int(round((t - W["intelligence"] * inte) / W["mp"])))
            err = abs((inte * W["intelligence"] + mp * W["mp"]) * K - target_out)
            if best is None or err < best[0]:
                best = (err, inte, mp, 0, 0)
        return {"attack": 0, "agility": 0, "intelligence": best[1], "mp": best[2]}
    # mix（饰品）：攻敏 + 智魔 各半
    half = target_out / 2
    p = fit_out(half, "phys"); m = fit_out(half, "magic")
    return {"attack": p["attack"], "agility": p["agility"],
            "intelligence": m["intelligence"], "mp": m["mp"]}


def main_hand_targets(items):
    """{price: T_main}：取该价格档 weapon 的现值(线性分)为锚；缺档用相邻插值。"""
    by_p = {}
    for it in items:
        if it.get("type") == "weapon":
            by_p.setdefault(it["price"], []).append(lscore(it))
    pts = sorted((p, max(v)) for p, v in by_p.items())
    out = {}
    for p in PRICES:
        exact = [s for pp, s in pts if pp == p]
        if exact:
            out[p] = exact[0]
            continue
        below = [pp for pp, _ in pts if pp < p]
        above = [pp for pp, _ in pts if pp > p]
        if below and above:
            p0, p1 = below[-1], above[0]
            s0, s1 = dict(pts)[p0], dict(pts)[p1]
            out[p] = s0 + (s1 - s0) * (p - p0) / (p1 - p0)
        elif below:
            out[p] = dict(pts)[below[-1]]
        elif above:
            out[p] = dict(pts)[above[0]]
        else:
            out[p] = 0
    return out


def build_one(it, T):
    """单件重算：按部位占比把目标分切成 生存/输出，再分别凑分。"""
    p = it.get("price", 0); typ = it.get("type")
    target = T.get(p, 0) * RATIO.get(typ, 1.0)
    surv_budget = target * SURV_SHARE.get(typ, 0.3)
    out_budget = target - surv_budget
    kind = "phys" if typ in ("weapon", "shield") else ("magic" if typ in ("staff", "robe", "focus") else "mix")
    surv_kind = "plate" if typ in ("armor", "shield", "weapon") else "cloth"
    d, h = fit_surv(surv_budget, p, surv_kind)
    o = fit_out(out_budget, kind, ratio_sub=0.18 if typ in ("weapon", "staff") else 0.4)
    nr = dict(it)
    nr.update(attack=o["attack"], agility=o["agility"],
              intelligence=o["intelligence"], mp=o["mp"],
              defense=d, hp=h)
    return nr


def rebuild(items, T):
    """重建整套装备。"""
    out = []
    for it in items:
        out.append(build_one(it, T))
    return out


def eq3plus1(new_eq, t, prof):
    """同 tier 玩家「能买到的最高价」3+1（取 ≤ 该tier价格上限的每 type 最高价件）。"""
    cap = [c for c, tt in PRICE_TIER if tt == t][0]
    def top_pick(typ):
        cand = [x for x in new_eq if x.get("type") == typ and x.get("price", 10 ** 9) <= cap]
        return max(cand, key=lambda x: x["price"]) if cand else None
    tys = ["weapon", "armor", "shield"] if prof == "warrior" else ["staff", "robe", "focus"]
    parts = [top_pick(x) for x in tys] + [top_pick("accessory")]
    return [p for p in parts if p]


# ---------- 补齐对称部位：目标结构 = 每价格档都有 7 部位 ----------
SYM = {
    "weapon": ("phys", 1.00), "staff": ("magic", 1.00),
    "armor": ("surv", 0.90), "robe": ("magic", 0.90),
    "shield": ("phys", 0.62), "focus": ("magic", 0.62),
    "accessory": ("mix", 0.55),
}
# 缺失件命名（占位名，待用户定名；避免与既有撞名）
_FALLBACK_CN = {"armor": "战甲", "robe": "法袍", "shield": "坚盾", "focus": "法器",
                "accessory": "徽记", "weapon": "之刃", "staff": "权杖"}
_USED = set()


def _gen_name(typ, p):
    base = f"{_FALLBACK_CN.get(typ, typ)}·{p}"
    k = 1
    nm = base
    while nm in _USED:
        k += 1
        nm = f"{base}·{k}"
    _USED.add(nm)
    return nm


def plan_full_symmetry(eq):
    """返回 (补齐规划清单, 说明)。每价格档 7 部位：已有保留，缺失则生成占位件。"""
    by_p = {p: {t: [] for t in SYM} for p in PRICES}
    for it in eq:
        if it["type"] in SYM and it["price"] in by_p:
            by_p[it["price"]][it["type"]].append(it)
    plan = []
    for p in PRICES:
        for ty, (kind, ratio) in SYM.items():
            arr = by_p[p][ty]
            if arr:
                plan.append(("keep", p, ty, arr[0]["name"]))
            else:
                nm = _gen_name(ty, p)
                plan.append(("new", p, ty, nm))
    return plan


def target_table(T):
    """每价格档 7 部位目标收益分表（主手×ratio 已含生存/输出拆分前总目标）。"""
    lines = []
    for p in PRICES:
        t_main = T.get(p, 0)
        cells = {ty: t_main * ratio for ty, (_k, ratio) in SYM.items()}
        lines.append((p, cells))
    return lines


def main():
    apply = "--apply" in sys.argv
    eq = json.load(open(EQ_FILE, encoding="utf-8"))["equipment"]
    fg = json.load(open(FG_FILE, encoding="utf-8")).get("recipes", [])
    rare = json.load(open(RARE_FILE, encoding="utf-8")).get("items", [])

    T = main_hand_targets(eq)
    print("== 主手目标分曲线 T_main(p) ==")
    for p in PRICES:
        print(f"  ¥{p:<6} T={T.get(p, 0):.1f}")

    # 1) 补齐对称部位规划
    print("\n== 每价格档 7 部位补齐规划（keep=已有 / new=需新增）==")
    plan = plan_full_symmetry(eq)
    for p in PRICES:
        row = []
        for ty, (_k, _r) in SYM.items():
            # 找该档 ty 的第一条
            m = next((x for x in plan if x[1] == p and x[2] == ty), None)
            row.append(f"{'·' if m[0] == 'keep' else '+'}{ty[:2]}={m[3]}")
        print(f"  ¥{p:<6} " + "  ".join(row))

    # 2) 每档目标收益分表
    print("\n== 每价格档 7 部位目标收益分 ==")
    print("  档位   | 武器 法杖  战甲 法袍  坚盾 法器  饰品")
    for p, cells in target_table(T):
        print(f"  ¥{p:<5} | " + " ".join(f"{cells[t]:6.1f}" for t in
              ("weapon", "staff", "armor", "robe", "shield", "focus", "accessory")))

    # 3) 补齐后：同档 战士3+1 vs 法师3+1（同价全套，应相等）
    print("\n== 补齐后 同价格档 战士全套 vs 法师全套（锚定一致 ⇒ 结构必然相等）==")
    for p in PRICES:
        t_main = T.get(p, 0)
        w_total = t_main * (1.00 + 0.90 + 0.62 + 0.55)
        m_total = t_main * (1.00 + 0.90 + 0.62 + 0.55)
        print(f"  ¥{p:<6} 战士全套 {w_total:6.1f}  法师全套 {m_total:6.1f}  差 {m_total-w_total:+.1f} ✓")

    print("\n说明：new 为占位名（待用户定名）；凑分只调六维，id/price/line/tier 语义不变。")
    print("⚠️ 本方案为「每价格档 7 部位全对称」的彻底版（需新增约 30 件）。")
    print("   若只想最小改动落「法师下调」，可选另一档位口径（同 tier 顶配对位，见 regen 的 --min 演示）。")
    if not apply:
        print("\n[dry-run] 未写盘。确认后运行: python docs/regen_linear_score.py --apply")
        return 0

    for path, data in ((EQ_FILE, {"equipment": new_eq}),
                       (FG_FILE, {"recipes": fg}),
                       (RARE_FILE, {"items": rare})):
        shutil.copy(path, path + ".bak-lin")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"已写盘 {path}（备份 .bak-lin）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
