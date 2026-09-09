# -*- coding: utf-8 -*-
"""抽奖模块：3 档抽奖（5 铜 / 5 银 / 5 金），各档中奖率与卡池稀有度不同。

- 档1（5 铜）：小奖为主——高概率小额铜币返还，实物多为低级装备/草药/低阶消耗品；
- 档2（5 银）：中奖率提升、稀有度上移——装备 tier2、稀有~传说材料、中阶消耗品；
- 档3（5 金）：高投入——大额金钱、tier4~5 装备、传说~神话材料、高阶消耗品、稀有装备大奖。
- 期望设计（v2.11.72/73）：三档均为**负期望**（EV/成本 ≈ 0.87 / 0.65 / 0.45），档位越高亏得越多，
  长期抽奖整体趋向亏损（铜币回收口径；材料/消耗品无定价未计入，实际略高于该值）。
  **高级货保留且高频**：档3 每抽 40% 出 tier4~5 装备（其中 20% 稀有大奖）、16% 出传说~神话材料
  （神话级 3 种：万瓜圣辉/万瓜圣契/万宝源晶）。

奖励发放：金钱 → user.copper；装备 → UserItem；材料 → material.grant_materials；
消耗品 → consumable.grant_consumables。调用方负责 commit。
"""

import random
import equipment
import material
import consumable
from currency import format_currency

# 档位成本（统一换算成铜币：100 铜 = 1 银，100 银 = 1 金）
TIERS = {
    1: {"name": "一档·铜签", "cost": 5, "cost_txt": "5 铜币"},
    2: {"name": "二档·银签", "cost": 500, "cost_txt": "5 银币"},
    3: {"name": "三档·金签", "cost": 50000, "cost_txt": "5 金币"},
}


def _pick(weighted):
    """按权重随机取一项：weighted=[(weight, value), ...]"""
    total = sum(w for w, _ in weighted)
    r = random.random() * total
    acc = 0.0
    for w, v in weighted:
        acc += w
        if r < acc:
            return v
    return weighted[-1][1]


def _rand_item(pool, rnd=random.choice):
    return rnd(pool)


_RARITY_ORDER = {"common": 0, "rare": 1, "legendary": 2, "myth": 3}


def _award_mark(kind, tier_no, it=None, rarity=None, jackpot=False):
    """抽奖奖等（v2.11.78）：按奖品本身稀有度/价值分等，跨档一致。
    🥉铜奖(低级) / 🥈银奖(中级) / 🥇金奖(高级) / 👑特等奖(稀有大奖)。
    """
    if kind == "money":
        return ("🥉", "铜奖") if tier_no == 1 else (("🥈", "银奖") if tier_no == 2 else ("🥇", "金奖"))
    if kind == "equip":
        if jackpot:
            return ("🥇", "金奖")  # 稀有装备：v2.11.80 从特等下放至金奖
        t = int(it.get("tier", 0) or 0)
        if t <= 1:
            return ("🥉", "铜奖")
        if t <= 3:
            return ("🥈", "银奖")
        return ("🥇", "金奖")
    if kind == "material":
        r = _RARITY_ORDER.get((rarity or "common"), 0)
        if r >= 3:
            return ("👑", "特等奖")  # 神话材料：全场最稀有（3 种 / 6%），独占特等
        if r == 2:
            return ("🥇", "金奖")  # 传说材料：v2.11.80 从银奖升至金奖
        return ("🥉", "铜奖")
    # consumable
    lv = int(it.get("level", 1) or 1)
    return ("🥉", "铜奖") if lv <= 2 else (("🥈", "银奖") if lv == 3 else ("🥇", "金奖"))


def _equip_pool(min_tier, max_tier, include_rare=False):
    """商店装备池（tier 区间）；include_rare 时附加大奖池信息返回 (商店池, 稀有池或None)。"""
    shop = [it for it in equipment.load_equipment()
            if (it.get("tier") or 0) >= min_tier and (it.get("tier") or 0) <= max_tier]
    if not include_rare:
        return shop, None
    import json, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rare_drops.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("rare_drops", data.get("items", data))
    rares = [it for it in data
             if (it.get("tier") or 0) >= min_tier and (it.get("tier") or 0) <= max_tier]
    return shop, rares


def _material_pool(min_rarity, max_rarity):
    """材料池（category herb/special/boss，按稀有度区间）"""
    order = {"common": 0, "rare": 1, "legendary": 2, "myth": 3}
    lo, hi = order[min_rarity], order[max_rarity]
    return [m for m in material.load_materials()
            if order.get((m.get("rarity") or "common"), 0) >= lo
            and order.get((m.get("rarity") or "common"), 0) <= hi]


def _consumable_pool(min_level, max_level):
    """消耗品池（药水/道具，按 Lv 区间）"""
    return [c for c in consumable.load_consumables()
            if (c.get("level") or 1) >= min_level and (c.get("level") or 1) <= max_level]


def roll(user, tier_no, rnd=None):
    """执行一次抽奖：扣费并发放奖励。返回 (播报文本, 是否大奖)。

    tier_no: 1/2/3；余额不足返回 (None, False)，调用方需先检查余额。
    """
    if tier_no not in TIERS:
        raise ValueError(f"未知抽奖档位: {tier_no}")
    tier = TIERS[tier_no]
    from models import db, UserItem
    from material import grant_materials
    from consumable import grant_consumables

    if tier_no == 1:
        kind = _pick([
            (38, "thanks"), (45, "money"), (6, "equip"), (6, "material"), (5, "consumable"),
        ])
    elif tier_no == 2:
        kind = _pick([
            (30, "thanks"), (38, "money"), (18, "equip"), (8, "material"), (6, "consumable"),
        ])
    else:
        kind = _pick([
            (6, "thanks"), (36, "money"), (40, "equip"), (16, "material"), (2, "consumable"),
        ])

    jackpot = False
    if kind == "thanks":
        text = "🙏 谢谢惠顾，再接再厉！"
    elif kind == "money":
        if tier_no == 1:
            amt = random.randint(1, 5)
        elif tier_no == 2:
            amt = random.randint(80, 650)
        else:
            amt = random.randint(10000, 70000)
        user.copper += amt
        mark, aword = _award_mark("money", tier_no)
        text = f"{mark} {aword} · 幸运金钱！获得 {format_currency(amt)}"
    elif kind == "equip":
        if tier_no == 1:
            shop, _ = _equip_pool(0, 1)
            it = _rand_item(shop)
        elif tier_no == 2:
            shop, _ = _equip_pool(2, 2)
            it = _rand_item(shop)
        else:
            shop, rares = _equip_pool(4, 5, include_rare=True)
            if rares and random.random() < 0.20:
                it = _rand_item(rares)
                jackpot = True
            else:
                it = _rand_item(shop)
        db.session.add(UserItem(user_id=user.user_id, item_id=it["id"], is_new=0))
        mark, aword = _award_mark("equip", tier_no, it, rarity=None, jackpot=jackpot)
        tag = "（稀有掉落！）" if jackpot else ""
        text = f"{mark} {aword} · 获得装备「{it['name']}」{tag}"
    elif kind == "material":
        if tier_no == 1:
            pool = _material_pool("common", "rare")
            cnt = random.randint(1, 2)
        elif tier_no == 2:
            pool = _material_pool("rare", "legendary")
            cnt = random.randint(1, 3)
        else:
            pool = _material_pool("legendary", "myth")
            cnt = random.randint(1, 3)
        if pool:
            it = _rand_item(pool)
            grant_materials(user.user_id, {it["id"]: cnt})
            mark, aword = _award_mark("material", tier_no, it, rarity=it.get("rarity"))
            text = f"{mark} {aword} · 获得材料「{it['name']}」×{cnt}"
        else:
            user.copper += tier["cost"]
            text = f"（材料池暂空，退回 {tier['cost_txt']}）"
    else:
        if tier_no == 1:
            pool = _consumable_pool(1, 2)
        elif tier_no == 2:
            pool = _consumable_pool(2, 3)
        else:
            pool = _consumable_pool(4, 5)
        if pool:
            it = _rand_item(pool)
            grant_consumables(user.user_id, {it["id"]: 1})
            kind_txt = "药水" if it.get("kind") == "potion" else "道具"
            mark, aword = _award_mark("consumable", tier_no, it)
            text = f"{mark} {aword} · 获得{kind_txt}「{it['name']}」×1"
        else:
            user.copper += tier["cost"]
            text = f"（消耗品池暂空，退回 {tier['cost_txt']}）"
    return text, jackpot


def rule_text():
    """抽奖规则说明（三档成本 + 概率/稀有度概览）。"""
    lines = ["🎰 抽奖（/抽奖 1|2|3 或 /抽奖 铜|银|金）",
             "奖等（按奖品稀有度/价值，跨档一致）：🥉铜奖·低级 | 🥈银奖·中级 | 🥇金奖·高级(稀有大奖/传说材料) | 👑特等奖·神话材料"]
    for no in (1, 2, 3):
        t = TIERS[no]
        if no == 1:
            desc = "38% 谢谢惠顾 · 45% 小额铜币(1~5) · 6% 低级装备(tier0) · 6% 草药/稀有材料 · 5% 低阶药水道具（期望≈0.87，长期略亏）"
        elif no == 2:
            desc = "30% 谢谢惠顾 · 38% 金钱(80~650) · 18% 装备(tier2) · 8% 稀有~传说材料 · 6% 中阶药水道具（期望≈0.65，亏损）"
        else:
            desc = "6% 谢谢惠顾 · 36% 金钱(1万~7万) · 40% 装备(tier4~5,20% 稀有掉落) · 16% 传说~神话材料 · 2% 高阶药水道具（期望≈0.45，仍亏；高级货高频）"
        lines.append(f"· {t['name']}：{t['cost_txt']}/次 —— {desc}")
    return "\n".join(lines)
