# -*- coding: utf-8 -*-
"""命令层（自 commands.py 迁移，单一实现源）。

本模块只被根目录 commands.py 转发引用；业务模块不依赖本文件（单向无环）。
GameCore 门面亦基于本层实现。
"""
import os
import random
import time
from datetime import datetime, timedelta

from .models import db, User, CheckinRecord, UserItem, TurnItem
from .currency import format_currency
from .equipment import load_equipment, find_item, suggest_items
from .classes import (
    class_line, item_line, class_name, find_class, all_classes,
    LINE_ANY, TYPE_NAMES as _CLASSES_TYPE_NAMES,
)
from .tiers import (
    tier_title, tier_of_price, next_promotion, TIER_LAYER, MAX_TIER,
    tier_promotion_cost, tier_price_cap, TIER_ATTR_BONUS,
)
from .dungeon import (
    BASE_STATS, LAYER1_TOTAL, layer_total, coin_rate_per_sec, coin_per_5sec,
    effective_stats, dungeon_speed, item_score, owned_items, owned_item_rows, settle_dungeon,
    effective_layer_total, boss_type, historical_best_layer,
    take_boss_report, rare_item_ids, find_any_item,
)
from . import ore, material, forge, boss, boss_gear, alchemy, consumable, dungeon

TYPE_NAMES = dict(_CLASSES_TYPE_NAMES)  # weapon/shield/armor/robe/accessory/staff/focus...
TYPE_NAMES["other"] = "其他"

ITEM_ATTR_ORDER = [
    ("attack", "攻"),
    ("mp", "魔"),
    ("agility", "敏"),
    ("intelligence", "智"),
    ("hp", "命"),
    ("defense", "防"),
]

_LOTTERY_ALIAS = {
    "1": 1, "一": 1, "铜": 1, "5铜": 1, "铜签": 1, "low": 1, "basic": 1,
    "2": 2, "二": 2, "银": 2, "5银": 2, "银签": 2, "mid": 2, "silver": 2,
    "3": 3, "三": 3, "金": 3, "5金": 3, "金签": 3, "high": 3, "gold": 3,
}

_TURN_DAILY_LIMIT = 3


def local_today():
    """签到日期（取服务器本机日期；建议部署在中国时区机器上）。"""
    return datetime.now().date()


def _user_title(user):
    """完整晋级称号（如 见习战士）；未转职显示「冒险者」（不带职业阶级前缀）。"""
    prof = (user.profession or "") or ""
    if not prof:
        return "冒险者"
    return tier_title(prof, (user.tier or 0))


def _item_tier(it):
    t = it.get("tier")
    if t is None:
        t = tier_of_price(it.get("price", 0))
    return int(t)


def _item_desc(it):
    parts = []
    for key, label in ITEM_ATTR_ORDER:
        val = it.get(key)
        if val:
            parts.append(f"{label}{val}")
    return " ".join(parts)


def cmd_bag(user, group_id, args, at_qqs=None):
    """查看当前持有的武具与矿石（/背包）。"""
    rows = owned_item_rows(user)
    if not rows:
        return ("🎒 你的背包空空如也。\n"
                "前往 /武器库 购买一件装备吧！（持有任意装备即可进入 /地下城）")
    prof = (user.profession or "") or ""
    usage = dungeon._usage_for(prof)
    rare_ids = rare_item_ids()
    lines = [f"🎒 我的背包（共 {len(rows)} 件） · {_user_title(user)}"]
    # 套装效果生效中（v2.12.3）：与推进速度同一选件口径
    try:
        _sb = dungeon.current_set_bonus(user, [it for it, _, _ in rows])
        if _sb > 1.0:
            lines.append(f"⚡ 套装效果生效中：全属性 ×{_sb:g}")
    except Exception:
        pass
    groups = {}
    for it, is_new, eq in rows:
        groups.setdefault(it.get("type", "other"), []).append((it, is_new, eq))
    for t in sorted(groups, key=lambda x: TYPE_NAMES.get(x, x) or x):
        lst = sorted(groups[t], key=lambda x: x[0]["price"])
        lines.append(f"—— {TYPE_NAMES.get(t, t)} ——")
        # 同名合并计数（保留 is_new/equipped 只要有一个即可标）
        seen = {}
        for it, is_new, eq in lst:
            if it["name"] not in seen:
                seen[it["name"]] = {"it": it, "cnt": 1, "new": bool(is_new), "eq": bool(eq)}
            else:
                seen[it["name"]]["cnt"] += 1
                seen[it["name"]]["new"] = seen[it["name"]]["new"] or bool(is_new)
                seen[it["name"]]["eq"] = seen[it["name"]]["eq"] or bool(eq)
        for name, info in seen.items():
            it = info["it"]
            suffix = f" ×{info['cnt']}" if info["cnt"] > 1 else ""
            mark = " new！" if info["new"] else ""
            wear = "（穿戴中）" if info["eq"] else ""
            # Boss 稀有装备：✦ 星号 + (稀有) 标注（D-B12）；命名 Boss 专属：👑 + (限定)
            rare = it.get("id") in rare_ids
            gear = boss_gear.is_gear(it.get("id"))
            disp = f"👑 {name}" if gear else (f"✦ {name}" if rare else name)
            rare_tag = "(限定)" if gear else ("(稀有)" if rare else "")
            # 未到穿戴等级：商店/稀有 tier 超阶级、锻造 Lv 未按历史层解锁（Boss 专属免等级不标）
            lvl_note = ""
            if not dungeon._item_usable(it, user.tier or 0, historical_best_layer(user)):
                f_lv = dungeon._forge_lv(it)
                lvl_note = f"（需锻造Lv{f_lv}）" if f_lv is not None else f"（需T{dungeon._item_tier(it)}）"
            # 转职后另一职业/不可用装备标注“暂不生效”（双线职业按 lines×types 判定）
            unusable = ""
            if usage and not dungeon._usable_line_type(it, *usage):
                unusable = "（本职业不生效）"
            lines.append(f"· {disp}{suffix}{mark}{rare_tag}{wear}{lvl_note}（{_item_desc(it)}）{unusable}")
    # 矿石展示（稀有度前缀）
    ores = ore.owned_ores(user.user_id)
    if ores:
        lines.append("—— ⛏️ 矿石 ——")
        _ore_mark = {"myth": "★", "legendary": "✦", "rare": "◆", "common": "·"}
        for o in ores:
            lines.append(f"{_ore_mark.get(o['rarity'], '·')} {o['name']} ×{o['count']}")
    # 材料展示（草药/特殊/boss 材料 分类独立成块）
    mats = material.owned_materials(user.user_id)
    if mats:
        _cat_name = {"herb": "🌿 草药", "special": "✨ 特殊材料", "boss": "💎 Boss材料", "souvenir": "🏷️ 特殊道具"}
        _mat_mark = {"myth": "★", "legendary": "✦", "rare": "◆", "common": "·"}
        _cat_order = {c: i for i, c in enumerate(("herb", "special", "boss", "souvenir"))}
        cur_cat = None
        # v2.11.83：先按类别稳定排序，避免同类材料被拆到多个分组
        for m in sorted(mats, key=lambda x: _cat_order.get(x["category"], 99)):
            if m["category"] != cur_cat:
                cur_cat = m["category"]
                lines.append(f"—— {_cat_name.get(cur_cat, cur_cat)} ——")
            mark = _mat_mark.get(m["rarity"], "·")
            head = f"{mark} " if mark != "·" else ""
            lines.append(f"· {head}{m['name']} ×{m['count']}")
    # 炼金产物（药水/道具 分类展示）
    cons = consumable.owned_consumables(user.user_id)
    potions = [c for c in cons if c.get("kind") == "potion"]
    tools = [c for c in cons if c.get("kind") != "potion"]
    if potions:
        lines.append("—— 🧪 药水 ——")
        for it in potions:
            lines.append(f"· {it['name']}（Lv{it['level']}）×{it['count']}")
    if tools:
        lines.append("—— 🎫 道具 ——")
        for it in tools:
            lines.append(f"· {it['name']}（Lv{it['level']}）×{it['count']}")
    # 生效中的药水/道具（同槽互斥；到期自动清理）
    buff_block = _buff_status_block(user)
    if buff_block:
        lines.append(buff_block)
    lines.append(f"当前资产：{format_currency(user.copper)}")
    # 已读标记：背包查看即消费 new!，下次起不再提示
    db.session.execute(
        db.update(UserItem).where(
            UserItem.user_id == user.user_id,
            UserItem.is_new == 1,
        ).values(is_new=0)
    )
    db.session.commit()
    return "\n".join(lines)


def cmd_shop(user, group_id, args, at_qqs=None):
    """武器库：按等级（T 档 = 所需称号）拆分展示。

    展示规则：未转职先引导转职；已转职把本职业可用装备按「T 档」分组，
    每档标题注明所需称号（如 T3·银辉战士）。当前称号（当前阶级）能买 ≤ 当前档
    的全部装备；更高档标 🔒 并提示需晋升到什么称号（该档只预览最低价几件，防刷屏）。

    自定义开关（/武器库 开关）：开启后**隐藏低于自己等级的装备**，只展示当前档，
    减少输出刷屏；再次切换恢复全部展示。
    """
    prof = (user.profession or "") or ""
    if not prof:
        return ("💡 你是冒险者，请先选择职业：\n"
                "/转职 战士（物理近战）/ 转职 魔法师（魔法施法）/ 转职 魔剑士（物主手+法施法）/ 转职 近战法师（法主手+物防护）\n"
                "选定职业后，/武器库 将展示你可购买的装备。")
    line = class_line(prof)
    usage = dungeon._usage_for(prof)
    my_tier = user.tier or 0
    items = load_equipment()
    # 本职业可用（职业 lines×types 或 通用 any）
    usable = [it for it in items if dungeon._usable_line_type(it, *usage)] if usage else []
    if not usable:
        return "武器库暂无可购装备。"
    # —— 开关子命令：/武器库 开关（切换）/ 开 / 关 ——
    arg = (args or "").strip()
    if arg:
        kw = arg.replace(" ", "").lower()
        if kw in ("开关", "切换", "toggle", "隐藏低级", "仅当前"):
            user.shop_filter = 0 if (user.shop_filter or 0) else 1
            db.session.commit()
            if user.shop_filter:
                return f"✅ 已开启武器库精简模式：隐藏低于当前档（T{my_tier}）的装备，只展示当前档。\n再发「/武器库 开关」可恢复全部展示。"
            return "✅ 已关闭武器库精简模式：恢复展示全部可购装备（T0~当前档）。"
        if kw in ("开", "on", "1"):
            user.shop_filter = 1
            db.session.commit()
            return f"✅ 已开启武器库精简模式：只展示当前档（T{my_tier}）装备。"
        if kw in ("关", "off", "0"):
            user.shop_filter = 0
            db.session.commit()
            return "✅ 已关闭武器库精简模式：恢复展示全部可购装备。"
    # 按 T 档分组
    from collections import defaultdict
    by_tier = defaultdict(list)
    for it in usable:
        by_tier[_item_tier(it)].append(it)
    max_shop = max(_item_tier(it) for it in usable)
    top_buy = min(my_tier, max_shop)          # 当前实际能买到的最高档
    hide_low = bool(user.shop_filter or 0)    # 精简模式：隐藏低于当前档
    head = f"⚔️ 武器库 · {_user_title(user)}"
    if hide_low:
        head += f"（精简模式：只显示 T{top_buy} 档，/武器库 开关 恢复全部）"
    else:
        head += f"（当前称号可购 T0~T{my_tier} 档，/武器库 开关 可隐藏低级）"
    lines = [head]
    for ti in range(0, max_shop + 1):
        arr = sorted(by_tier.get(ti, []), key=lambda x: (x["price"], x["name"]))
        if not arr:
            continue
        title_name = tier_title(prof, ti)  # 如 见习战士 / 疾风战士 …
        if ti <= my_tier:
            if hide_low and ti < top_buy:
                continue   # 精简模式：隐藏低于当前档的装备
            lines.append(f"—— T{ti} · {title_name} ——")
            for it in arr:
                lines.append(f"· {it['name']}（{TYPE_NAMES.get(it.get('type'), it.get('type') or '')}·{_item_desc(it)}）{format_currency(it['price'])}")
        else:
            lines.append(f"—— 🔒 T{ti} · {title_name} 需 /晋升 至「{title_name}」——")
            preview = "、".join(it["name"] for it in arr[:4])
            more = f" 等 {len(arr)} 件" if len(arr) > 4 else ""
            lines.append(f"   预览：{preview}{more}")
    lines.append("—— 提示：/购买 装备名；/晋升 提升称号解锁更高档；/铁匠铺 超越商店顶级 ——")
    return "\n".join(lines)


def cmd_class(user, group_id, args, at_qqs=None):
    """选择/切换职业（/转职 战士|魔法师）。"""
    arg = (args or "").strip()
    if not arg:
        cur = class_name(user.profession) if (user.profession or "") else "未转职"
        opts = "、".join(f"/转职 {c['name']}（{c['desc']}）" for c in all_classes())
        return (f"当前职业：{cur} · {_user_title(user)}\n"
                f"可选职业：\n{opts}\n"
                "转职后：新职业的专属装备生效，另一职业专属装备保留但不生效（可出售）。")
    cid = find_class(arg)
    if cid is None:
        hint = "、".join(c["name"] for c in all_classes())
        return f"没有职业「{arg}」。可选：{hint}"
    user.profession = cid
    db.session.commit()
    return (f"⚔️ 转职成功！你已成为 {class_name(cid)}（{_user_title(user)}）。\n"
            f"地下城将只计算本职业与通用装备的属性（不混搭）。")


def cmd_promote(user, group_id, args, at_qqs=None):
    """晋升阶级（/晋升）：需已通关要求层 + 消耗货币（v2.12.17 起「通关才算」，到达不算）。"""
    cur = user.tier or 0
    nxt = next_promotion(cur)
    if nxt is None:
        return f"你已达最高阶级 {_user_title(user)}！"
    cleared = user.dungeon_cleared or 0   # 已通关层数（顺序推进，= 已通关最高层）
    need_layer = TIER_LAYER.get(nxt, 0)
    cost = tier_promotion_cost(nxt)
    if cleared < need_layer:
        return (f"晋升 {tier_title(user.profession, nxt)} 需要：\n"
                f"地下城已通关层数 ≥ {need_layer}（当前 {cleared}）\n"
                f"货币 ≥ {format_currency(cost)}（当前 {format_currency(user.copper)}）")
    if user.copper < cost:
        return (f"货币不足，不能晋升！\n"
                f"晋升 {tier_title(user.profession, nxt)} 需 {format_currency(cost)}，"
                f"你只有 {format_currency(user.copper)}。\n"
                f"（层数已达标：已通关 {cleared} ≥ {need_layer}）")
    user.copper -= cost
    user.tier = nxt
    db.session.commit()
    # 解锁档位（2026-09-09 口径，层门槛 0/50/150/400/800/1600/2500/3000）：
    # 武器库(商店)档位随阶级解锁；铁匠铺按已通关层分级（Lv1@400…Lv4@3200，穿戴随层解锁）。
    # T6 灭世 / T7 至尊 不再纯称号——称号每阶全属性 +5%（T7=+35%），并配合锻造顶级追装。
    from tiers import TIER_ATTR_BONUS, TIER_LAYER as _TL
    if nxt >= 6:
        forge_note = "铁匠铺 Lv3（已通关 1600）" if nxt == 6 else "铁匠铺 Lv4（已通关 3200）"
        unlock_note = (f"称号加成提升至 全属性 +{int((TIER_ATTR_BONUS[nxt] - 1) * 100)}%！"
                       f"（灭世/至尊为称号·锻造段：{forge_note} 及顶级锻造可追）")
    else:
        unlock_note = f"武器库已解锁 {tier_title(user.profession, nxt)} 阶级的装备！称号加成 +{int((TIER_ATTR_BONUS[nxt] - 1) * 100)}%。"
    return (f"🎉 晋升成功！{tier_title(user.profession, cur)} → {tier_title(user.profession, nxt)}\n"
            f"消耗 {format_currency(cost)}，剩余 {format_currency(user.copper)}。\n"
            f"{unlock_note}")


def _forge_unlocked(user):
    """铁匠铺是否已开放：到达 Lv1 解锁层（400 层，T3 段）即有铁匠铺。"""
    return historical_best_layer(user) >= forge.FORGE_LV_LAYER[1]


def _forge_level_open(user, level):
    """指定锻造 Lv 是否已按历史最高层解锁。"""
    return forge.level_unlocked(historical_best_layer(user), level)


def _forge_status(user):
    """当前已解锁的锻造 Lv（升序列表）。"""
    return sorted(forge.unlocked_levels(historical_best_layer(user)))


def _forge_breakdown(user, name):
    """分解锻造装备：删除装备，按配方矿石成本返还——普通矿石返一半（向下取整），高级矿石每个单位 50% 概率独立判定。"""
    if not name:
        return "用法：/铁匠铺 分解 <装备名>（如 /铁匠铺 分解 断岳重剑）"
    rec = forge.find_forge(name)
    if rec is None:
        other = find_any_item(name) if name else None
        if other is not None:
            return (f"「{other['name']}」不是锻造装备，无法分解。\n"
                    f"仅锻造装备可分解回收矿石；商店/掉落装备可 /出售 或 /转转 捐赠。")
        hits = forge.suggest_forges(name)
        hint = f"，你是不是想分解：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"铁匠铺没有「{name}」这件锻造装备{hint}"
    row = db.session.execute(
        db.select(UserItem).where(
            UserItem.user_id == user.user_id,
            UserItem.item_id == rec["id"],
        ).limit(1)
    ).scalars().first()
    if row is None:
        return f"你还没有锻造过「{rec['name']}」，无法分解。"
    db.session.delete(row)
    # 按配方矿石成本返还：普通返半 / 稀有返六成（向下取整）/ 传说·神话每单位 50% 概率独立判定
    gained = {}
    for oid, cnt in (rec.get("cost") or {}).items():
        meta = ore.ore_meta(oid)
        rarity = (meta or {}).get("rarity")
        if rarity == "common":
            back = cnt // 2
        elif rarity == "rare":
            back = cnt * 3 // 5  # 返还 60%，向下取整
        else:
            back = sum(1 for _ in range(cnt) if random.random() < 0.5)
        if back:
            gained[oid] = back
    ore.grant_ores(user.user_id, gained)
    db.session.commit()
    lines = [f"🔧 分解成功！「{rec['name']}」已拆解，返还矿石："]
    if not gained:
        lines.append("（运气不佳，什么也没返还……）")
    else:
        for oid, cnt in gained.items():
            meta = ore.ore_meta(oid)
            lines.append(f"· {meta['name'] if meta else oid} ×{cnt}")
    return "\n".join(lines)


def cmd_forge_shop(user, group_id, args, at_qqs=None):
    """铁匠铺：/铁匠铺 查看配方；/铁匠铺 分解 <装备名> 分解锻造装回收矿石。"""
    parts = (args or "").split(maxsplit=1)
    if parts and parts[0].lower() in ("分解", "拆解", "fenjie", "breakdown"):
        return _forge_breakdown(user, parts[1].strip() if len(parts) > 1 else "")
    from tiers import tier_of_layer
    prof = (user.profession or "") or ""
    if not _forge_unlocked(user):
        return ("🔨 铁匠铺尚未开放。\n"
                f"到达地下城第 {forge.FORGE_LV_LAYER[1]} 层（≈{tier_title(prof, tier_of_layer(forge.FORGE_LV_LAYER[1]))} 称号段）后开启 Lv1，"
                "可锻造超越武具店顶级的装备！")
    recs = forge.all_forges()
    open_lvs = _forge_status(user)
    lines = [f"🔨 铁匠铺（/锻造 装备名 制作；已开放 Lv{'/'.join(map(str, open_lvs))}）"]
    cur_level = None
    for r in recs:
        if r.get("level") != cur_level:
            cur_level = r.get("level")
            lv_open = cur_level in open_lvs
            need = forge.FORGE_LV_LAYER.get(cur_level)
            tier_need = tier_of_layer(need) if need else 0
            title_req = tier_title(prof, tier_need) or f"T{tier_need} 段"
            # 该 Lv 产物的强度档（配方 tier 集合）
            lv_tiers = sorted({int(rr.get("tier", 0) or 0) for rr in recs if rr.get("level") == cur_level})
            tier_txt = f"产物 T{'/'.join(map(str, lv_tiers))}"
            if lv_open:
                lines.append(f"—— Lv{cur_level}（{tier_txt} · 到 {need} 层 ≈「{title_req}」）——")
            else:
                lines.append(f"—— 🔒 Lv{cur_level}（{tier_txt}）需到地下城 {need} 层 ≈「{title_req}」——")
        cost = forge.format_cost(r.get("cost", {})) + f" + {format_currency(r.get('price', 0))}"
        lines.append(f"· {r['name']}（{TYPE_NAMES.get(r['type'], r['type'])}）{_item_desc(r)}")
        lines.append(f"   消耗：{cost}")
    lines.append("—— 提示：铁匠铺装备无法 /出售 回收；可用 /铁匠铺 分解 回收矿石（普通返半、稀有返六成、传说/神话按50%概率每单位判定）——")
    return "\n".join(lines)


def cmd_forge(user, group_id, args, at_qqs=None):
    """锻造装备：消耗铜币 + 矿石（不能赊账），成功后入背包。"""
    name = (args or "").strip()
    if not name:
        return "用法：/锻造 装备名（/铁匠铺 查看配方；/锻造 推荐 按背包算可锻）"
    if name.lower() in ("推荐", "tuijian", "recommend"):
        return forge.recommend_text(user)
    if not _forge_unlocked(user):
        return (f"🔨 铁匠铺尚未开放。到达地下城第 {forge.FORGE_LV_LAYER[1]} 层后开启 Lv1，"
                f"可锻造超越武器库顶级的装备！")
    rec = forge.find_forge(name)
    if rec is None:
        hits = forge.suggest_forges(name)
        hint = f"，你是不是想锻造：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"铁匠铺没有「{name}」{hint}\n发送 /铁匠铺 查看配方。"
    # 职业校验：锻造产物与商店一致，需职业 lines×types 匹配
    prof = (user.profession or "") or ""
    usage = dungeon._usage_for(prof)
    line = item_line(rec)
    if line != LINE_ANY:
        if not prof:
            return (f"💡 锻造「{rec['name']}」需要职业。\n"
                    f"请先 /转职 选择职业（如 /转职 战士 / 转职 魔法师 / 转职 魔剑士 / 转职 近战法师）。")
        if not usage or not dungeon._usable_line_type(rec, *usage):
            return f"❌ 「{rec['name']}」不是{class_name(prof) or '你'}的锻造装备，你无法使用。"
    # 等级解锁校验（新口径）：配方按铁匠铺 Lv 分级，需历史最高层达到该 Lv 解锁层
    lv = int(rec.get("level", 1))
    if not _forge_level_open(user, lv):
        from tiers import tier_of_layer
        need = forge.FORGE_LV_LAYER.get(lv)
        prof2 = (user.profession or "") or ""
        title_req = tier_title(prof2, tier_of_layer(need)) if need else f"T{lv} 段"
        return (f"🔒 「{rec['name']}」属于铁匠铺 Lv{lv}，需到地下城第 {need} 层"
                f"（≈「{title_req}」称号段）解锁。\n"
                f"（当前历史最高层 {historical_best_layer(user)}）")
    price = rec.get("price", 0)
    if user.copper < price:
        return (f"铜币不足，不能赊账！锻造「{rec['name']}」需 {format_currency(price)}，"
                f"你只有 {format_currency(user.copper)}。")
    missing = ore.consume_ores(user.user_id, rec.get("cost", {}))
    if missing:
        lines = ["矿石不足，不能锻造！"]
        for oid, (need, have) in missing.items():
            meta = ore.ore_meta(oid)
            lines.append(f"缺少 {meta['name'] if meta else oid}（需 {need}，现有 {have}）")
        return "\n".join(lines)
    user.copper -= price
    db.session.add(UserItem(user_id=user.user_id, item_id=rec["id"]))
    db.session.commit()
    return (f"🔨 锻造成功！获得「{rec['name']}」（{TYPE_NAMES.get(rec['type'], rec['type'])}）{_item_desc(rec)}\n"
            f"消耗 {format_currency(price)} 与对应矿石，剩余资产：{format_currency(user.copper)}。")


def cmd_buy(user, group_id, args, at_qqs=None):
    """购买装备（/购买），职业+阶级双重校验。"""
    raw = (args or "").strip()
    if raw in ("全部", "一键", "一键购买", "all", "顶级"):
        return _cmd_buy_best(user)
    names = [n for n in raw.split()]
    if not names:
        return "用法：/购买 商品名 [商品名 ...]（例如 /购买 短剑 圆盾）"
    items, missing = [], []
    for name in names:
        item = find_item(name)
        if item is None:
            missing.append(name)
        else:
            items.append(item)
    if missing:
        hint_lines = []
        for name in missing:
            hits = suggest_items(name)
            hint = f"，你是不是想买：{'、'.join(h['name'] for h in hits)}" if hits else ""
            hint_lines.append(f"武器库没有「{name}」{hint}")
        return "\n".join(hint_lines) + "\n发送 /武器库 查看商品列表。"
    # 职业/阶级校验
    prof = (user.profession or "") or ""
    usage = dungeon._usage_for(prof)
    bad = None
    for it in items:
        line = item_line(it)
        if line != LINE_ANY:
            if not prof:
                return (f"💡 购买「{it['name']}」需要职业。\n"
                        f"请先 /转职 选择职业（如 /转职 战士 / 转职 魔法师 / 转职 魔剑士 / 转职 近战法师）。")
            if not usage or not dungeon._usable_line_type(it, *usage):
                return f"❌ 「{it['name']}」不是{class_name(prof) or '你'}的装备，你无法购买使用。"
        req_tier = it.get("tier")
        if req_tier is None:
            req_tier = tier_of_price(it.get("price", 0))
        if (user.tier or 0) < int(req_tier):
            need_title = tier_title(prof, int(req_tier)) or f"T{req_tier} 段"
            return (f"🔒 「{it['name']}」需晋升至「{need_title}」称号才能购买。\n"
                    f"当前称号 {_user_title(user)}；发送 /晋升 查看晋升条件。")
    total = sum(it["price"] for it in items)
    if user.copper < total:
        return (f"铜币不足，不能赊账！购买这 {len(items)} 件装备共需 {format_currency(total)}，"
                f"你只有 {format_currency(user.copper)}。")
    for it in items:
        user.copper -= it["price"]
        db.session.add(UserItem(user_id=user.user_id, item_id=it["id"]))
    db.session.commit()
    if len(items) == 1:
        it = items[0]
        return (f"购买成功！获得「{it['name']}」×1，消耗 {format_currency(total)}，"
                f"剩余资产：{format_currency(user.copper)}。")
    cnt = {}
    for it in items:
        cnt[it["name"]] = cnt.get(it["name"], 0) + 1
    detail = "、".join(f"{n}×{c}" for n, c in cnt.items())
    return (f"购买成功！获得：{detail}\n"
            f"消耗 {format_currency(total)}，剩余资产：{format_currency(user.copper)}。")


def _cmd_buy_best(user):
    """/购买 全部（一键购买）：购入商店可购买最高档的「每部位评分最好」装备各 1 件。

    - 最高档 = 商店装备中 tier ≤ 当前阶级的最大档（仅商店来源，不含锻造）；
    - 部位 = 本职业可用类型 + 饰品（如魔剑士=武器/法器/法袍/饰品）；
    - 该部位已持有评分 ≥ 目标件的装备 → 跳过（不重复购入）；
    - 不能赊账；未转职先引导转职。
    """
    prof = (user.profession or "") or ""
    usage = dungeon._usage_for(prof)
    if not usage:
        return ("💡 一键购买需要先选择职业：\n"
                "/转职 战士（物理近战）/ 转职 魔法师（魔法施法）/ 转职 魔剑士（物主手+法施法）/ 转职 近战法师（法主手+物防护）。")
    lines, types = usage
    tier_max = user.tier or 0
    items = load_equipment()
    avail = [it for it in items
             if dungeon._usable_line_type(it, lines, types)
             and _item_tier(it) <= tier_max]
    if not avail:
        return "当前没有可购买的装备（请先 /晋升 提升阶级）。"
    top_tier = max(_item_tier(it) for it in avail)
    top = [it for it in avail if _item_tier(it) == top_tier]
    best_by_type = {}
    for it in top:
        t = it.get("type", "other")
        if t not in best_by_type or item_score(it) > item_score(best_by_type[t]):
            best_by_type[t] = it
    # 该部位已有更高分装备 → 跳过
    owned_best = {}
    for it in dungeon.owned_items(user):
        t = it.get("type", "other")
        sc = item_score(it)
        if t not in owned_best or sc > owned_best[t]:
            owned_best[t] = sc
    buys, skipped = [], []
    for t, it in best_by_type.items():
        if owned_best.get(t, -1) >= item_score(it):
            skipped.append(it)
        else:
            buys.append(it)
    if not buys:
        names = "、".join(it["name"] for it in best_by_type.values())
        return f"已拥有 T{top_tier} 档每部位最好装备（{names}），无需重复购买。"
    total = sum(it["price"] for it in buys)
    if user.copper < total:
        need = "、".join(f"{it['name']}({format_currency(it['price'])})" for it in buys)
        return (f"铜币不足，不能赊账！一键购买 {len(buys)} 件（T{top_tier} 档每部位最好）共需 {format_currency(total)}，"
                f"你只有 {format_currency(user.copper)}。\n{need}")
    for it in buys:
        user.copper -= it["price"]
        db.session.add(UserItem(user_id=user.user_id, item_id=it["id"]))
    db.session.commit()
    names = "、".join(f"{it['name']}×1" for it in buys)
    skip_note = f"\n已跳过 {len(skipped)} 件（持有更好）：{'、'.join(it['name'] for it in skipped)}" if skipped else ""
    return (f"🛒 一键购买成功！已购入 T{top_tier} 档每部位最好装备：{names}\n"
            f"消耗 {format_currency(total)}，剩余资产：{format_currency(user.copper)}。{skip_note}")


def cmd_sell(user, group_id, args, at_qqs=None):
    """出售装备（购买价 60%），支持空格分隔批量出售（例：/出售 短剑 圆盾）。

    一键出售：/出售 全部（或 一键/all）——一次性出售所有「可出售、未穿戴、
    非本部位最高评分」的非专属装备；保留：穿戴中、每部位评分最高、锻造、专属。
    """
    args = (args or "").strip()
    if args in ("全部", "一键", "一键出售", "all"):
        return _cmd_sell_all(user)
    names = [n for n in args.split()]
    if not names:
        return "用法：/出售 商品名 [商品名 ...]（例如 /出售 短剑 圆盾）；/出售 全部 可一键出售全部可出售的闲置装备"
    sold, missing, not_owned, not_sellable = [], [], [], []
    total = 0
    for name in names:
        item = find_item(name)
        if item is None:
            item = find_any_item(name)   # 非商店来源（如 Boss 稀有掉落）也可识别
        if item is None:
            missing.append(name)
            continue
        if forge.is_forged_item(item["id"]):
            not_sellable.append(item["name"])
            continue
        if boss_gear.is_gear(item["id"]):   # 命名 Boss 专属装备不可出售（全服限量收藏）
            not_sellable.append(item["name"])
            continue
        row = db.session.execute(
            db.select(UserItem).where(
                UserItem.user_id == user.user_id,
                UserItem.item_id == item["id"],
            ).limit(1)
        ).scalars().first()
        if row is None:
            not_owned.append(item["name"])
            continue
        is_rare = item["id"] in rare_item_ids()
        # 售价：普通装备 = 购买价 60%；掉落稀有装备回收价下调为 30%
        sp = int(item["price"] * (0.3 if is_rare else 0.6))
        sold.append((item["name"], sp, is_rare))
        total += sp
        user.copper += sp
        db.session.delete(row)
    db.session.commit()
    lines = []
    if sold:
        if len(sold) == 1:
            n, sp, is_rare = sold[0]
            note = "（稀有装备回收价 30%）" if is_rare else "（购买价 60%）"
            lines.append(f"出售成功！卖出「{n}」×1，获得 {format_currency(sp)}{note}")
        else:
            detail = "、".join(n for n, _, _ in sold)
            if any(r for _, _, r in sold):
                lines.append(f"出售成功！卖出：{detail}，共获得 {format_currency(total)}（含稀有装备按回收价 30% 计）")
            else:
                lines.append(f"出售成功！卖出：{detail}，共获得 {format_currency(total)}（购买价 60%）")
    if missing:
        lines.append("未找到：" + "、".join(f"「{n}」" for n in missing))
    if not_owned:
        lines.append("没有：" + "、".join(f"「{n}」" for n in not_owned))
    if not_sellable:
        lines.append("不可出售：" + "、".join(f"「{n}」" for n in not_sellable) + "（铁匠铺锻造装备无法出售）")
    if not lines:
        return "你没有可出售的装备。"
    lines.append(f"当前资产：{format_currency(user.copper)}")
    return "\n".join(lines)


def _cmd_sell_all(user):
    """一键出售：所有「可出售、未穿戴、非本部位最高评分」的非专属装备。

    保留：① 穿戴中（equipped=1）；② 每个部位（type）内可出售装备中评分最高的一件——
    但仅当该部位**没有更高评分的不可售装备**（锻造/命名 Boss 专属）时才保留；
    若已有更高分的锻造/专属（如裂渊之印），则可售最高件不保留、一并卖出；
    ③ 锻造装备（只能分解）与命名 Boss 专属装备（全服限量收藏）。
    售价与 /出售 一致：普通装备购买价 60%，掉落稀有装备按回收价 30%。
    """
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == user.user_id)
    ).scalars().all()
    all_meta = dungeon._all_item_meta()   # 商店+锻造+稀有+专属 全量装备表
    sellable = []   # (row, item_meta, item_score)
    for r in rows:
        it = all_meta.get(r.item_id)
        if it is None:
            continue
        if forge.is_forged_item(it["id"]) or boss_gear.is_gear(it["id"]):
            continue
        sellable.append((r, it, item_score(it)))
    if not sellable:
        return "没有可一键出售的装备（无可出售的非锻造/非专属装备）。"
    # 每部位全量最高评分（含锻造/专属），用于判定可售最高件是否值得保留
    max_all_by_type = {}
    for r in rows:
        it = all_meta.get(r.item_id)
        if it is None:
            continue
        t = it.get("type", "other")
        sc = item_score(it)
        if t not in max_all_by_type or sc > max_all_by_type[t]:
            max_all_by_type[t] = sc
    keep = {r.id for r, _, _ in sellable if r.equipped}
    best_by_type = {}
    for r, it, sc in sellable:
        t = it.get("type", "other")
        if t not in best_by_type or sc > best_by_type[t][2]:
            best_by_type[t] = (r, it, sc)
    for t, (r, it, sc) in best_by_type.items():
        # 可售最高件只有在「它就是该部位全量最高（无更高分锻造/专属）」时才保留
        if sc >= max_all_by_type.get(t, -1):
            keep.add(r.id)
    to_sell = [(r, it) for r, it, _ in sellable if r.id not in keep]
    if not to_sell:
        return "没有可一键出售的装备：可出售装备均为穿戴中或本部位最高评分，已全部保留。"
    total = 0
    cnt = {}
    rare = rare_item_ids()
    for r, it in to_sell:
        is_rare = it["id"] in rare
        sp = int(it["price"] * (0.3 if is_rare else 0.6))
        total += sp
        cnt[it["name"]] = cnt.get(it["name"], 0) + 1
        user.copper += sp
        db.session.delete(r)
    db.session.commit()
    detail = "、".join(f"{n}×{c}" for n, c in cnt.items())
    kept = len(sellable) - len(to_sell)
    return ("\n".join([
        f"一键出售完成！卖出 {len(to_sell)} 件：{detail}",
        f"共获得 {format_currency(total)}（稀有装备按回收价 30% 计）",
        f"已保留 {kept} 件：穿戴中、每部位评分最高、锻造与 Boss 专属装备。",
        f"当前资产：{format_currency(user.copper)}",
    ]))


def _set_ore_entry_state(user, layer):
    """进入地下城时快照掉落资格：矿石 >400 层；草药 ≥150 层（进入时判定，本轮有效）。"""
    user.dungeon_ore_eligible = 1 if layer > ore.ORE_MIN_LAYER else 0
    user.dungeon_ore_last = time.time()
    user.dungeon_herb_eligible = 1 if layer >= material.HERB_MIN_LAYER else 0
    user.dungeon_herb_last = time.time()


def _ore_entry_note(layer):
    notes = []
    if layer > ore.ORE_MIN_LAYER:
        notes.append("💎 已解锁稀有矿石掉落（每 15 分钟一个结算周期）！")
    if layer >= material.HERB_MIN_LAYER:
        notes.append("🌿 已解锁草药采集（每 15 分钟一个结算周期）！")
    return ("\n" + "\n".join(notes)) if notes else ""


def _dungeon_enter(user):
    if user.dungeon_layer > 0:
        return f"你已在地下城第 {user.dungeon_layer} 层中。"
    count = db.session.execute(
        db.select(db.func.count()).select_from(UserItem).where(UserItem.user_id == user.user_id)
    ).scalar()
    if count < 1:
        return "你还没有任何装备，无法进入地下城。先去 /武器库 购买一件装备吧！"

    # 进入地下城：刷新「穿戴中」标记（清空后按当前最优组合标记每槽 1 件；
    # 地下城中新获得的装备在重新进入前不会进入穿戴）
    marked = dungeon.mark_best_equipped(user)
    wear_note = ""
    if marked:
        wear_note = "已穿戴：" + "、".join(marked) + "\n"

    stats = effective_stats(user, owned_items(user))
    speed = dungeon_speed(stats)
    prof = (user.profession or "") or ""
    class_note = ""
    if not prof:
        class_note = ("\n💡 你尚未选择职业（地下城暂按最优自动生效）。\n"
                      f"发送 /转职 战士 / 魔法师 / 魔剑士 / 近战法师 选定职业（影响装备可用）。")

    # 优先从保存的进度继续（直达上次的层数与剩余进度）
    if user.saved_dungeon_layer and user.saved_dungeon_layer > 0:
        layer = user.saved_dungeon_layer
        progress = max(user.saved_dungeon_progress or 0.0, 0.0)
        user.saved_dungeon_layer = 0
        user.saved_dungeon_progress = 0.0
        user.dungeon_layer = layer
        user.dungeon_progress = progress
        user.dungeon_last_update = time.time()
        user.dungeon_coin_acc = 0.0
        user.dungeon_run_coins = 0
        _set_ore_entry_state(user, layer)
        db.session.commit()
        ore_note = _ore_entry_note(layer)
        bt = boss_type(layer)
        boss_note = f" · BOSS" if bt else ""
        if bt:
            try:
                import boss as _boss_mod
                if _boss_mod.boss_for_layer(layer):
                    boss_note += "（命名守关：进度满后战力判定，失败重置收益照常）"
            except Exception:
                pass
        return (f"⚔️ 已从上次进度继续冒险！（{_user_title(user)}{boss_note}）\n"
                f"你回到地下城第 {layer} 层（剩余进度 {progress:.0f} / 总计 {effective_layer_total(layer):.0f}）\n"
                f"推进速度：{speed:.2f} 进度/秒\n"
                f"金币速度：约 {coin_per_5sec(layer):.4f} 铜币/5秒\n"
                f"{wear_note}地下城内可使用 /签到、/余额、/帮助 与 /地下城 退出。{ore_note}{class_note}")

    # 新的地下城冒险（第 1 层）
    user.dungeon_layer = 1
    user.dungeon_progress = effective_layer_total(1)
    user.dungeon_last_update = time.time()
    user.dungeon_coin_acc = 0.0
    user.dungeon_run_coins = 0
    _set_ore_entry_state(user, 1)
    db.session.commit()

    return (f"⚔️ 你已进入地下城第 1 层！\n"
            f"推进速度：{speed:.2f} 进度/秒\n"
            f"通关本层进度：{effective_layer_total(1):.0f}\n"
            f"金币速度：约 {coin_per_5sec(1):.4f} 铜币/5秒\n"
            f"{wear_note}地下城内可使用 /签到、/余额、/帮助 与 /地下城 退出。{class_note}")


def _buff_status_block(user):
    """生效中的药水/道具展示块（药水/道具各自同时仅一种）；无则返回空串。"""
    try:
        from consumable import buffs_status_text
        potion_txt, tool_txt = buffs_status_text(user.user_id)
    except Exception:
        return ""
    parts = []
    if potion_txt:
        parts.append(potion_txt)
    if tool_txt:
        parts.append(tool_txt)
    if not parts:
        return ""
    return "生效中：\n" + "\n".join(parts)


def _dungeon_status(user):
    if user.dungeon_layer <= 0:
        if user.saved_dungeon_layer and user.saved_dungeon_layer > 0:
            total = effective_layer_total(user.saved_dungeon_layer)
            remaining = max(user.saved_dungeon_progress or 0.0, 0.0)
            return (f"💾 你保存了地下城进度：第 {user.saved_dungeon_layer} 层"
                    f"（剩余 {remaining:.0f} / 总计 {total:.0f}）。\n"
                    f"发送 /地下城 进入 可继续冒险。\n"
                    + _buff_status_block(user)).rstrip()
        buff_block = _buff_status_block(user)
        if buff_block:
            return "你当前不在地下城中。使用 /地下城 进入 开始冒险。\n" + buff_block
        return "你当前不在地下城中。使用 /地下城 进入 开始冒险。"
    total = effective_layer_total(user.dungeon_layer)
    remaining = max(user.dungeon_progress, 0.0)
    done = total - remaining
    pct = (done / total * 100) if total else 0
    bt = boss_type(user.dungeon_layer)
    bt_txt = "（BOSS）" if bt else ""
    buff_block = _buff_status_block(user)
    buff_line = ("\n" + buff_block) if buff_block else ""
    stats = effective_stats(user, owned_items(user))
    speed = dungeon_speed(stats)
    return (f"📍 地下城第 {user.dungeon_layer} 层{bt_txt} · {_user_title(user)}\n"
            f"进度：{pct:.1f}%（剩余 {remaining:.0f} / 总计 {total:.0f}）\n"
            f"推进速度：{speed:.2f} 进度/秒\n"
            f"金币速度：约 {coin_per_5sec(user.dungeon_layer):.4f} 铜币/5秒\n"
            f"本次地下城已获得 {user.dungeon_run_coins} 铜币；累计通关 {user.dungeon_cleared} 层，"
            f"累计获得 {user.dungeon_coins_earned} 铜币。\n"
            f"当前资产：{format_currency(user.copper)}"
            f"{buff_line}")


def _dungeon_exit(user):
    if user.dungeon_layer <= 0:
        return "你当前不在地下城中。"
    settle_dungeon(user)  # 先结算最新进度与金币，再保存
    layer = user.dungeon_layer
    progress = max(user.dungeon_progress, 0.0)
    run_coins = user.dungeon_run_coins
    cleared_total = user.dungeon_cleared
    # 保存进度，退出后下次可直达续上
    user.saved_dungeon_layer = layer
    user.saved_dungeon_progress = progress
    user.dungeon_layer = 0
    user.dungeon_progress = 0.0
    user.dungeon_last_update = None
    user.dungeon_coin_acc = 0.0
    user.dungeon_run_coins = 0
    # 退出后矿石/草药资格作废，下次进入重新判定
    user.dungeon_ore_eligible = 0
    user.dungeon_ore_last = None
    user.dungeon_herb_eligible = 0
    user.dungeon_herb_last = None
    db.session.commit()
    return (f"🚪 你已离开地下城（第 {layer} 层，剩余进度 {progress:.0f}）。\n"
            f"进度已保存，下次 /地下城 进入 可从第 {layer} 层继续冒险。\n"
            f"本次地下城获得 {run_coins} 铜币，累计通关 {cleared_total} 层。\n"
            f"当前资产：{format_currency(user.copper)}")


def _dungeon_rank(user, group_id):
    """/地下城 排名：本群战力 TOP10 + 自己的战力/进度层/专属装备拥有情况。

    战力 = dungeon_speed(effective_stats(当前装备))（同仓库权威口径）；
    进度层 = historical_best_layer(在线/保存层取高)；专属 = boss_gear.json 已拥有件数；
    只统计本群用户（User.group_id == 当前群；未归群用户不展示）。
    """
    from collections import Counter
    from dungeon import _all_item_meta

    meta = _all_item_meta()
    rows = db.session.execute(db.select(UserItem)).scalars().all()
    by_user = {}
    for r in rows:
        it = meta.get(r.item_id)
        if it is None:
            continue
        by_user.setdefault(r.user_id, []).append(it)
    all_users = db.session.execute(
        db.select(User).where(User.group_id == group_id)).scalars().all()

    ranked = []
    for u in all_users:
        owned = by_user.get(u.user_id, [])
        s = dungeon_speed(effective_stats(u, owned))
        layer = historical_best_layer(u)
        gears = [it for it in owned if boss_gear.is_gear(it.get("id"))]
        ranked.append((u, s, layer, gears))
    ranked.sort(key=lambda r: (-r[1], r[0].user_id))
    top = ranked[:10]

    def disp(u):
        n = (u.nickname or "").strip()
        return n if n else str(u.user_id)

    def pad(name, w=8):
        ww = sum(2 if ord(c) > 127 else 1 for c in name)
        return name + " " * max(1, w - ww)

    lines = ["🏆 本群地下城战力 TOP10（战力｜进度层｜专属）"]
    for i, (u, s, layer, gears) in enumerate(top, 1):
        mark = "（我）" if u.user_id == user.user_id else ""
        lines.append(
            f"{i:>2}. {pad(disp(u))} 战力 {int(s):>6} ｜ 到 {layer} 层 ｜ 👑专属 ×{len(gears)}{mark}")
    me = next((r for r in ranked if r[0].user_id == user.user_id), None)
    if me is not None:
        u, s, layer, gears = me
        in_top = any(r[0].user_id == user.user_id for r in top)
        if not in_top:
            lines.append("── 我的排名 ──")
            lines.append(
                f"第 {ranked.index(me) + 1} 名 {pad(disp(u))} 战力 {int(s):>6} ｜ 到 {layer} 层 ｜ 👑专属 ×{len(gears)}（我）")
        if gears:
            cnt = Counter(g["id"] for g in gears)
            seen = {g["id"]: g for g in gears}
            names = "、".join(f"{seen[i]['name']}×{cnt[i]}" for i in seen)
            lines.append(f"我的专属装备：{names}")
        else:
            lines.append("我的专属装备：暂无")
    else:
        owned = by_user.get(user.user_id, [])
        s = dungeon_speed(effective_stats(user, owned))
        layer = historical_best_layer(user)
        gears = [it for it in owned if boss_gear.is_gear(it.get("id"))]
        lines.append("── 我的战力（本群未活跃，暂不参与本群排名）──")
        lines.append(
            f"我 {pad(disp(user))} 战力 {int(s):>6} ｜ 到 {layer} 层 ｜ 👑专属 ×{len(gears)}（我）")
        if gears:
            cnt = Counter(g["id"] for g in gears)
            seen = {g["id"]: g for g in gears}
            names = "、".join(f"{seen[i]['name']}×{cnt[i]}" for i in seen)
            lines.append(f"我的专属装备：{names}")
        else:
            lines.append("我的专属装备：暂无")
        lines.append("（在本群发一条消息即可计入本群排名）")
    return "\n".join(lines)


def cmd_dungeon(user, group_id, args, at_qqs=None):
    sub = (args or "").strip().lower()
    if sub in ("进入", "enter"):
        return _dungeon_enter(user)
    if sub in ("退出", "exit", "离开"):
        return _dungeon_exit(user)
    if sub in ("状态", "status"):
        return _dungeon_status(user)
    if sub in ("排名", "rank", "top", "排行榜"):
        return _dungeon_rank(user, group_id)
    if not sub and (user.dungeon_layer > 0
                    or (user.saved_dungeon_layer and user.saved_dungeon_layer > 0)):
        return _dungeon_status(user)
    return ("地下城指令：\n"
            "/地下城 进入 - 进入/继续地下城（自动穿戴当前最优 4 件；退出后保留进度，可直达上次位置）\n"
            "/地下城 状态 - 查看当前层数/进度/金币/生效中药水·道具(剩余层数/时间)\n"
            "/地下城 排名 - 本群战力 TOP10 + 自己的战力/进度层/专属装备拥有情况\n"
            "/地下城 退出 - 离开地下城（保存当前进度）\n"
            "—— 穿戴中：进入时自动标记每部位收益最高的 1 件，推进值只算穿戴中的装备；"
            "地下城中新获得的装备在重新进入前不会自动穿戴（/背包 可见「穿戴中」标记）——\n"
            "—— Boss 关：命名守关 Boss 打满进度后按战力判定胜负，失败重置本关进度但收益照常；"
            "精英/小Boss/无名字整百层进度跑完即通关 ——")


def _copper_median():
    """全服用户资产（铜币）中位数：仅统计有资产（copper>0）的用户，0 资产不计入。"""
    vals = [row[0] for row in db.session.execute(
        db.select(User.copper).where(User.copper > 0)).all()]
    if not vals:
        return 0
    vals.sort()
    n = len(vals)
    if n % 2:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) // 2


def cmd_challenge(user, group_id, args, at_qqs=None):
    """/挑战 统一入口：
    - /挑战 @对方 → 玩家对战（原逻辑：随机 50/50，三条文字演出，每天 3 次）；
    - /挑战 <Boss名|层数|称号> → Boss 挑战（如 挑战 裂风狼王 / 挑战 1000层 / 挑战 究极，
      兼容完整名/部分名/层数/称号，成功按 Boss 规则结算）。
    """
    targets = at_qqs or []
    if targets:
        return _player_duel(user, group_id, targets)
    text = (args or "").strip()
    if not text:
        return ("用法：/挑战 @对方（玩家对战，每天 3 次）\n"
                "/挑战 列表 - 查看 Boss 清单与今日剩余次数\n"
                "/挑战 装备 [名称] - 查询专属装备属性与全服余量\n"
                "/挑战 <Boss名|层数|称号>（如 挑战 裂风狼王 / 挑战 1000层 / 挑战 究极）")
    if text.lower() in ("列表", "list", "清单", "all", "全部"):
        return boss.boss_list_text(user)
    low = text.lower()
    if low == "装备" or low == "equip" or low.startswith("装备 ") or low.startswith("equip "):
        # /挑战 装备 [名称]：专属装备属性 + 全服余量（v2.12.12）
        rest = text[2:].strip() if low.startswith("装备") else text[5:].strip()
        return boss.gear_list_text(user, rest or None)
    boss_obj = boss.find_boss(text)
    if boss_obj:
        return boss.challenge_boss(user, boss_obj)[0]
    hits = boss.suggest_bosses(text)
    hint = f"，你是不是想挑战：{'、'.join(h['name'] for h in hits)}" if hits else ""
    return (f"没有找到「{text}」对应的 Boss{hint}\n"
            f"发送 /挑战 查看用法，或 /boss 列表 查看全部守关 Boss。")


def _player_duel(user, group_id, targets):
    """/挑战 @B：随机 50/50 胜负，三条文字演出（各间隔 2 秒）。

    赌注基准 = max(全服资产中位数 × 3%, 500 铜币)；
    挑战方败 → 支付 基准×1.2；被挑战方败 → 支付 基准×0.8；
    不能赊账（资产不足则全部支付）；发起方每天限 3 次。
    """
    target = targets[0]
    if target == user.user_id:
        return "不能挑战自己"

    today = local_today().strftime("%Y-%m-%d")
    if user.challenge_date != today:
        user.challenge_date = today
        user.challenge_count = 0
    if user.challenge_count >= 3:
        return f"今天挑战次数已用完（3/3），明天再来吧！"

    target_user = db.session.get(User, target)
    if target_user is None:
        return "对方还没有注册，无法发起挑战"

    # 掷骰定胜负（纯随机 50/50）
    a_win = random.random() < 0.5
    # 基准赌注：中位数×3%；上限 = 双方资产相加×10%；保底 50 铜
    cap = int((user.copper + target_user.copper) * 0.10)
    base = max(min(int(_copper_median() * 0.03), cap), 50)
    if a_win:            # 挑战方 A 胜 → 被挑战方 B 输
        loser, winner = target_user, user
        amount = int(base * 0.8)
    else:                # 挑战方 A 败 → A 输
        loser, winner = user, target_user
        amount = int(base * 1.2)

    pay = min(amount, loser.copper)   # 不能赊账：不足则输家全部资产支付
    winner.copper += pay
    loser.copper -= pay
    user.challenge_count += 1
    db.session.commit()

    an = user.nickname or str(user.user_id)
    bn = target_user.nickname or str(target)
    wname = winner.nickname or str(winner.user_id)
    # 娱乐技能交锋（v2.11.62）：双方按各自等级称号出招，仅文字表述、无效果；三段技能名前后呼应
    from . import skills as _skills
    askill, aeff = _skills.title_skill(user.profession or "", user.tier or 0)
    bskill, beff = _skills.title_skill(target_user.profession or "", target_user.tier or 0)
    wskill, _ = _skills.title_skill(winner.profession or "", winner.tier or 0)
    msg1 = f"{an} 使出【{askill}】，{aeff}！"
    msg2 = f"{bn} 使出【{bskill}】，{beff}！"
    msg3 = f"{wname} 以【{wskill}】技高一筹，{wname} 获胜！{wname} 获得 {format_currency(pay)}。"
    return {
        "type": "challenge_show",
        "group_id": group_id,
        "msgs": [msg1, msg2, msg3],
        "delay": 2,
    }


def cmd_boss(user, group_id, args, at_qqs=None):
    """/boss：列表查看 / 挑战指定守关 Boss（普通每日共 3 次 / 1000·2000·3600 每日各 1 次，成功率按装备强度）。"""
    arg = (args or "").strip()
    low = arg.lower()
    if low.startswith("挑战") or low.startswith("fight") or low.startswith("challenge"):
        # /boss 挑战 <Boss名> 或 /挑战 boss名（兼容）
        parts = arg.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else parts[0][2:].strip()
        if not name:
            return "用法：/boss 挑战 <Boss名>（如 /boss 挑战 裂风狼王·灰鬃）；统一入口 /挑战 <Boss名|层数|称号>"
        target = boss.find_boss(name)
        if target is None:
            hits = boss.suggest_bosses(name)
            hint = f"，你是不是想挑战：{'、'.join(h['name'] for h in hits)}" if hits else ""
            return f"没有「{name}」这个 Boss{hint}\n发送 /boss 列表 查看全部守关 Boss。"
        return boss.challenge_boss(user, target)[0]
    if low in ("", "列表", "list", "状态", "status", "我的"):
        return boss.boss_list_text(user)
    # 直接给 Boss 名 → 当作挑战
    target = boss.find_boss(arg)
    if target is None:
        hits = boss.suggest_bosses(arg)
        hint = f"，你是不是想挑战：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"没有「{arg}」这个 Boss{hint}\n发送 /boss 列表 查看全部守关 Boss。"
    return boss.challenge_boss(user, target)[0]


def cmd_lottery(user, group_id, args, at_qqs=None):
    """抽奖：消耗金钱抽取金钱/装备/材料/矿石/道具，共 3 档（5铜/5银/5金）。

    支持批量（v2.11.92）：/抽奖 <档位> <次数>，次数为第二个参数，默认 1，最多 10 次。
    """
    from . import lottery
    raw = (args or "").strip()
    if not raw:
        return lottery.rule_text()
    parts = raw.split(maxsplit=1)
    name = parts[0]
    tier_no = _LOTTERY_ALIAS.get(name.lower(), None)
    if tier_no is None:
        return f"不认识「{name}」。\n用法：/抽奖 1|2|3（或 /抽奖 铜|银|金）[次数]，/抽奖 查看规则"
    count = 1
    if len(parts) == 2:
        if not parts[1].isdigit():
            return (f"抽奖次数无效：「{parts[1]}」。\n"
                    f"用法：/抽奖 {name} <次数>（最多 10 次）")
        count = max(1, min(int(parts[1]), 10))
    cost = lottery.TIERS[tier_no]["cost"]
    total = cost * count
    if user.copper < total:
        return (f"余额不足！{lottery.TIERS[tier_no]['cost_txt']}/次 ×{count} 共需 {format_currency(total)}，"
                f"你只有 {format_currency(user.copper)}。")
    user.copper -= total
    from models import db
    lines = [f"🎰 {lottery.TIERS[tier_no]['name']} ×{count} · 消耗 {format_currency(total)}"]
    jackpot_count = 0
    for _ in range(count):
        text, jackpot = lottery.roll(user, tier_no)
        if jackpot:
            jackpot_count += 1
            text += " ✨大奖！"
        lines.append(text)
    db.session.commit()
    if jackpot_count:
        lines.append(f"✨ 中出 {jackpot_count} 次大奖！")
    lines.append(f"剩余资产：{format_currency(user.copper)}")
    return "\n".join(lines)


def _turn_usable(user, item):
    """该装备是否对乞讨者「符合等级」可用：商店/稀有 tier ≤ 当前阶级；锻造按铁匠铺 Lv 解锁层。"""
    try:
        return dungeon._item_usable(item, user.tier or 0, historical_best_layer(user))
    except Exception:
        return False


def _turn_today(user):
    today = local_today().strftime("%Y-%m-%d")
    if user.turn_date != today:
        user.turn_date = today
        user.turn_count = 0
    return today


def cmd_turn(user, group_id, args, at_qqs=None):
    """转转：捐赠不需要的装备入公共库 / 乞讨一件符合自己等级的装备（每天 3 次）。

    - /转转 捐赠 <装备名>：把自己的装备放入公共库（他人可乞讨）；
    - /转转 乞讨：从公共库随机取一件「符合自己等级」的装备（tier ≤ 当前阶级 / 锻造 Lv 已解锁），每天 3 次；
    - /转转：查看规则、今日剩余次数与库中情况。
    """
    parts = (args or "").split(maxsplit=1)
    act = parts[0].strip().lower() if parts else ""
    if not act:
        _turn_today(user)
        total = db.session.execute(db.select(db.func.count()).select_from(TurnItem)).scalar() or 0
        rows = db.session.execute(db.select(TurnItem)).scalars().all()
        fit = 0
        for r in rows:
            meta = dungeon.find_any_item(r.item_id)
            if meta and _turn_usable(user, meta):
                fit += 1
        return (f"🎁 转转 · 互助公共库\n"
                f"· /转转 捐赠 <装备名> - 把不需要的装备放入公共库（他人可乞讨）\n"
                f"· /转转 乞讨 - 随机获得一件符合自己等级的装备（每天 {_TURN_DAILY_LIMIT} 次）\n"
                f"今日剩余乞讨：{max(0, _TURN_DAILY_LIMIT - user.turn_count)} 次\n"
                f"公共库：共 {total} 件，其中 {fit} 件符合你的等级")
    if act in ("捐赠", "捐", "donate"):
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            return "用法：/转转 捐赠 <装备名>（例如 /转转 捐赠 短剑）"
        item = find_item(name)
        if item is None:
            item = find_any_item(name)
        if item is None:
            return f"没有找到「{name}」这件装备。"
        if forge.is_forged_item(item["id"]):
            return f"锻造装备无法捐赠，可去 /铁匠铺 分解 回收矿石。"
        row = db.session.execute(
            db.select(UserItem).where(
                UserItem.user_id == user.user_id,
                UserItem.item_id == item["id"],
            ).limit(1)
        ).scalars().first()
        if row is None:
            return f"你还没有「{item['name']}」，无法捐赠。"
        db.session.delete(row)
        db.session.add(TurnItem(item_id=item["id"], donor_id=user.user_id))
        db.session.commit()
        return f"🎁 捐赠成功！「{item['name']}」已放入公共库，其他冒险者可乞讨。"
    if act in ("乞讨", "讨", "beg"):
        _turn_today(user)
        if user.turn_count >= _TURN_DAILY_LIMIT:
            return f"今天乞讨次数已用完（{_TURN_DAILY_LIMIT}/{_TURN_DAILY_LIMIT}），明天再来吧！"
        rows = db.session.execute(db.select(TurnItem)).scalars().all()
        fits = []
        for r in rows:
            meta = dungeon.find_any_item(r.item_id)
            if meta and _turn_usable(user, meta):
                fits.append((r, meta))
        if not fits:
            return "公共库空空如也，或暂时没有符合你等级的装备。去 /转转 捐赠 一件装备帮帮别人吧！"
        row, meta = random.choice(fits)
        db.session.delete(row)
        db.session.add(UserItem(user_id=user.user_id, item_id=meta["id"], is_new=0))
        user.turn_count += 1
        db.session.commit()
        return (f"🎁 乞讨成功！获得「{meta['name']}」（{_item_desc(meta)}）\n"
                f"今日剩余乞讨：{max(0, _TURN_DAILY_LIMIT - user.turn_count)} 次。\n"
                f"也欢迎 /转转 捐赠 不需要的装备回馈大家～")
    return f"不认识的操作「{parts[0]}」。\n用法：/转转 捐赠 <装备名> · /转转 乞讨 · /转转"


def cmd_alchemy(user, group_id, args, at_qqs=None):
    """/炼金：查看配方 / 制作药水·道具（消耗材料+矿石+铜币）。

    关键词「配方/列表/全部/查看」可查看所有配方；空参数同样列出全部。
    """
    name = (args or "").strip()
    if not name or name.lower() in ("配方", "列表", "全部", "查看", "peifang", "list", "all"):
        return alchemy.recipe_list_text(user)
    if name.lower() in ("推荐", "tuijian", "recommend"):
        return alchemy.recommend_text(user)
    # v2.11.85 批量制作：/炼金 <配方名> <数量>，数量为空默认 1
    count = 1
    _parts = name.rsplit(maxsplit=1)
    if len(_parts) == 2 and _parts[1].isdigit():
        name, count = _parts[0], max(1, min(int(_parts[1]), 99))
    recipe = alchemy.find_recipe(name)
    if recipe is None:
        hits = alchemy.suggest_recipes(name)
        hint = f"，你是不是想炼：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"炼金铺没有「{name}」配方{hint}\n发送 /炼金 查看全部配方。"
    return alchemy.forge_recipe(user, recipe, count)


def cmd_use(user, group_id, args, at_qqs=None):
    """/使用 <药水/道具名>：消耗 1 个并生效（药水→属性 BUFF / 道具→掉落增益）。"""
    name = (args or "").strip()
    if not name:
        # 展示持有的炼金物品
        own = consumable.owned_consumables(user.user_id)
        if not own:
            return "你还没有任何药水/道具。先 /炼金 制作，或 /boss 挑战 /地下城 获得材料。"
        lines = ["🎒 我的炼金物品："]
        for it in own:
            kind = "药水" if it["kind"] == "potion" else "道具"
            lines.append(f"· {it['name']}（{kind} Lv{it['level']}）×{it['count']}")
        lines.append("—— 用法：/使用 <物品名> 生效 ——")
        return "\n".join(lines)
    return consumable.use_by_name(user, name)

# ---------- 签到/余额（v2.11.87 起归入地下城命令组，走 GameCore 门面） ----------

# 签到奖励概率（单位：铜币）：95% 概率 50~200 铜币；4% 概率 888 铜币；1% 概率 1 铜币
CHECKIN_BIG_COPPER = 888  # 4% 特殊奖励


def roll_checkin_reward():
    """按概率返回签到奖励（单位：铜币）。"""
    r = random.random()
    if r < 0.01:             # 1% → 1 铜币
        return 1
    if r < 0.05:             # 4% → 888 铜币
        return CHECKIN_BIG_COPPER
    return random.randint(50, 200)


def cmd_checkin(user, group_id, args, at_qqs=None):
    today = local_today()
    if user.last_checkin_date == today:
        return (f"今天已经签到过啦～\n"
                f"目前已连续签到 {user.checkin_streak} 天，"
                f"当前资产：{format_currency(user.copper)}")

    yesterday = today - timedelta(days=1)
    if user.last_checkin_date == yesterday:
        user.checkin_streak += 1
    else:
        user.checkin_streak = 1

    reward = roll_checkin_reward()
    user.total_checkin += 1
    user.copper += reward
    user.last_checkin_date = today

    record = CheckinRecord(
        user_id=user.user_id,
        group_id=group_id,
        nickname=user.nickname,
        checkin_date=today,
        streak_after=user.checkin_streak,
        reward=reward,
    )
    db.session.add(record)
    db.session.commit()

    return (f"签到成功！已连续签到 {user.checkin_streak} 天，累计签到 {user.total_checkin} 次。\n"
            f"获得 {format_currency(reward)}，当前资产：{format_currency(user.copper)}")


def cmd_balance(user, group_id, args, at_qqs=None):
    items = owned_items(user)
    prof = (user.profession or "") or ""
    t = user.tier or 0
    bonus = int((TIER_ATTR_BONUS.get(t, 1.0) - 1) * 100) if t > 0 else 0
    class_txt = f"{class_name(prof)} · {_user_title(user)}" if prof else "未转职·冒险者"
    bonus_txt = f"（称号加成 +{bonus}% 全属性）" if bonus else ""
    base = (f"当前资产：{format_currency(user.copper)}\n"
            f"职业/称号：{class_txt} {bonus_txt}\n"
            f"持有装备 {len(items)} 件；累计签到 {user.total_checkin} 次，连续签到 {user.checkin_streak} 天。")
    buff_block = _buff_status_block(user)
    if buff_block:
        return base + "\n" + buff_block
    return base
