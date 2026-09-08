# -*- coding: utf-8 -*-
"""阶级（Tier）系统：阶级分层、晋级称号、层数门槛、解锁/晋升判定。

设计（见 docs/equipment-system-redo-plan.md §3.6）：
- 每件装备带 tier(0~6)；玩家当前阶级 tier 决定能购买/装备到多好的装备。
- 层数门槛用「历史最高层」判定：max(dungeon_layer, dungeon_cleared, saved_dungeon_layer)。
- 晋升需满足层数门槛 + 货币足够，由 /晋升 命令触发。
"""
import os

# 装备 tier 上限。装备档位只到 5（武具店顶配）与 6（铁匠铺毕业）；
# 7 为「纯称号」阶（无新装备/新解锁，仅荣誉）。
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
# 校准见 §3.6.1：0/30/80/200/350/500/800
# 末轮（称号收紧）：T6 灭世 800→2000（更稀有），新增 T7 至尊 5000（纯称号，无新装备/解锁）。
# 武器库顶配(T5=500)、铁匠铺(400 层)均早于灭世 —— 装备易得、称号是长期荣誉。
TIER_LAYER = {
    0: 0,
    1: 30,
    2: 80,
    3: 200,
    4: 350,
    5: 500,
    6: 2000,
    7: 5000,
}

# 晋升消耗（铜币）：晋升到 tier i 所需货币（金币级回收后期盈余，可按 D11 调）
PROMOTION_COST = {
    1: 300,        # 5 银 → 挡位低价段
    2: 1500,       # 15 银
    3: 6000,       # 60 银
    4: 20000,      # 2 金币
    5: 60000,      # 6 金币
    6: 200000,     # 20 金币（灭世）
    7: 500000,     # 50 金币（至尊·纯称号）
}

# 各 tier 解锁的装备价格上限（铜币）——用于「目标数值表」与校验；实施以装备 tier 字段为准
TIER_PRICE_CAP = {
    0: 120,
    1: 600,
    2: 3000,
    3: 9000,
    4: 26000,
    5: 45000,     # 武器库全解锁
    6: 10 ** 9,   # 铁匠铺
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
    from classes import class_name
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
