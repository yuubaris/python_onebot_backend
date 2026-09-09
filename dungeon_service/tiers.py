# -*- coding: utf-8 -*-
"""阶级（Tier）系统：阶级分层、晋级称号、层数门槛、称号加成、解锁/晋升判定。

设计（见 docs/tier-forge-title-rework-request.md）：
- 装备带 tier(0~5)；玩家当前阶级 tier 决定能购买/装备到多好的装备，
  商店顶配在 T5；锻造（Lv1~Lv4）从商店 T4.5 之上起步为毕业线，供 T5/T6/T7 追装。
- 层门槛：T0=0 / T1=50 / T2=150 / T3=400 / T4=800 / T5=1600 / T6=3200 / T7=3600
  （2026-09-08：地下城封顶 3600，通关 3600 层即晋升 T7；此前 T7=5000）
- 全员一律从 T0（见习）开始，老玩家不自动继承阶级，均通过 /晋升 逐级提升。
- 称号提供属性加成（TIER_ATTR_BONUS）：每阶全属性 +5%，T7 封顶 +35%，
  于 dungeon.effective_stats 内生效（同装备下，高阶称号实力更强）。
- 层数门槛用「历史最高层」判定：以 dungeon_layer / saved_dungeon_layer 最高值为准。
- 晋升需满足层数门槛 + 货币足够，由 /晋升 命令触发。
"""
import os

# 装备 tier 上限。装备档位只到 5（商店 45000）；锻造 Lv1~Lv4 为毕业线，
# 覆盖 T5 之上（供 T6/T7 装备）；7 为称号最顶阶。
MAX_TIER = 7

# 晋级称号前缀（每阶一个；完整称号 = 前缀 + 职业名，如 见习战士 / 疾风魔法师）
TIER_PREFIX = {
    0: "见习",
    1: "疾风",
    2: "烈焰",
    3: "银辉",
    4: "苍穹",
    5: "神谕",
    6: "灭世",
    7: "至尊",
}

# 阶级层数门槛：晋升到 tier i 需历史最高层 ≥ TIER_LAYER[i]
# 2026-09-08（用户口径）：T1=50 / T2=150 / T3=400，中后段等比顺延；
# 地下城封顶 3600 层，通关 3600 = 晋升 T7（最终档）。
TIER_LAYER = {
    0: 0,
    1: 50,
    2: 150,
    3: 400,
    4: 800,
    5: 1600,
    6: 3200,
    7: 3600,
}

# 称号属性加成：玩家当前阶级 tier 的全属性倍率（每阶 +5%，T7 封顶 +35%）。
# 作用于 dungeon.effective_stats（地下城推进 / Boss 通关 / 状态展示）。
# 无称号（T0）为 1.0；晋升 1 阶 → 1.05。
TIER_ATTR_BONUS = {t: 1.0 + 0.05 * t for t in range(MAX_TIER + 1)}

# 晋升消耗（铜币）：晋升到 tier i 所需货币（金币级回收后期盈余，可按 D11 调）
PROMOTION_COST = {
    1: 300,        # 5 银 → 挡位低价段
    2: 1500,       # 15 银
    3: 6000,       # 60 银
    4: 20000,      # 2 金币
    5: 60000,      # 6 金币
    6: 200000,     # 20 金币（灭世）
    7: 500000,     # 50 金币（至尊·称号顶阶 + 全属性 +35%）
}

# 各 tier 解锁的装备价格上限（铜币）——用于「目标数值表」与校验；实施以装备 tier 字段为准
# 商店顶配 T5=45000；T6/T7 不再纯称号——锻造 Lv3/Lv4 为 T6/T7 的毕业追装（tier 见 forge.json）。
TIER_PRICE_CAP = {
    0: 120,
    1: 600,
    2: 3000,
    3: 9000,
    4: 26000,
    5: 45000,      # 商店（武器库）顶配（T5 解锁）
    6: 10 ** 9,    # 兜底档（锻造顶级价格偏高，仅供价格兜底）
}

# 装备价格档（现有 14 档，用于把旧装备按价格归 tier / 校验）
PRICE_TIERS = [
    (50, 0), (75, 0), (120, 0),
    (200, 1), (350, 1), (600, 1),
    (1000, 2), (1700, 2), (3000, 2),
    (5200, 3), (9000, 3),
    (15000, 4), (26000, 4),
    (45000, 5),
]


def tier_of_layer(layer):
    """给定历史最高层，返回当前应处于的 tier（不含 0 门槛自身）。"""
    layer = layer or 0
    best = 0
    for t in sorted(TIER_LAYER):
        if layer >= TIER_LAYER[t]:
            best = t
    return best


def tier_of_price(price):
    """按价格给旧装备兜底推断 tier（新装备 JSON 直接带 tier，此函数仅兜底）。"""
    price = price or 0
    for cap, t in sorted(PRICE_TIERS, key=lambda x: x[1]):
        if price <= cap:
            return t
    return MAX_TIER - 1 if price <= TIER_PRICE_CAP.get(MAX_TIER - 1, 10 ** 9) else MAX_TIER


def tier_title(class_id, tier):
    """完整晋级称号：前缀 + 职业名（如 见习战士）；未转职则只给前缀。"""
    from .classes import class_name
    pre = TIER_PREFIX.get(tier, TIER_PREFIX[0])
    nm = class_name(class_id)
    return f"{pre}{nm}" if nm else pre


def can_promote_to(user_meta, tier):
    """能否晋升到某 tier：返回 (bool, 原因)。user_meta 含 best_layer 与 货币。"""
    if tier <= 0:
        return False, "已是初始阶级。"
    if tier > MAX_TIER:
        return False, "已达最高阶级。"
    best_layer = user_meta.get("best_layer", 0)
    need_layer = TIER_LAYER.get(tier)
    if best_layer < need_layer:
        return False, f"地下城历史最高层需 ≥ {need_layer}（当前 {best_layer}）"
    cost = user_meta.get("cost", 0)
    copper = user_meta.get("copper", 0)
    if copper < cost:
        return False, f"货币不足：晋升需 {cost} 铜币（当前 {copper}）"
    return True, ""


def next_promotion(user_tier):
    """返回玩家下一次可晋升到的 tier（若已达上限返回 None）。"""
    nxt = user_tier + 1
    return nxt if nxt <= MAX_TIER else None


def tier_price_cap(tier):
    return TIER_PRICE_CAP.get(tier, 10 ** 9)


def tier_promotion_cost(tier):
    """晋升到 tier 需的铜币（0 表示初始/无成本）。"""
    return PROMOTION_COST.get(tier, 0)
