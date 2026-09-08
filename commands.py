# -*- coding: utf-8 -*-
"""命令处理模块。

规则：群聊中消息以 "/" 开头即进入命令处理；命令名不区分大小写。

命令：
    /签到             每日签到，随机获得铜币/银币
    /余额             查看当前资产
    /武器库           查看可购买的装备（按职业与阶级过滤展示）
    /购买 商品名       消耗铜币购买装备（不能赊账；需职业/阶级符合）
    /出售 商品名       出售装备，获得购买价 60% 的铜币
    /转职 战士|魔法师  选择职业（转职）；可随时切换
    /晋升             按地下城进度达标并消耗货币提升阶级
    /地下城 进入/状态/退出   地下城冒险（Boss 关掉落）
    /铁匠铺 /锻造      查看/制作高级装备
    /帮助             显示可用命令

地下城限制：进入地下城后，仅允许 /签到 与 /地下城（退出/状态），其余动作一律拒绝。
"""
import os
import random
import time
from datetime import datetime, timedelta

from models import db, User, CheckinRecord, UserItem
from currency import format_currency
from equipment import load_equipment, find_item, suggest_items
from classes import (
    class_line, item_line, class_name, find_class, all_classes,
    LINE_ANY, TYPE_NAMES as _CLASSES_TYPE_NAMES,
)
from tiers import (
    tier_title, tier_of_price, next_promotion, TIER_LAYER, MAX_TIER,
    tier_of_layer, tier_promotion_cost, tier_price_cap,
)
from dungeon import (
    BASE_STATS, LAYER1_TOTAL, layer_total, coin_rate_per_sec, coin_per_5sec,
    effective_stats, dungeon_speed, owned_items, owned_item_rows, settle_dungeon,
    effective_layer_total, boss_type, sync_initial_tier, historical_best_layer,
    take_boss_report, rare_item_ids,
)
from kick import check_cooldown, mark_cooldown, build_kick_image, build_beat_image, build_jue_image, build_dalao_image
import ore
import forge

# 未知指令触发阈值：累计超过该次数后，发送 beat.jpeg 且不再响应该用户的未知指令
UNKNOWN_LIMIT = 3
# 项目根目录（用于定位 beat.jpeg）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UNKNOWN_IMAGE = os.path.join(_BASE_DIR, "beat.jpeg")

# 签到奖励概率（单位：铜币）：
#   95% 概率 50~200 铜币；4% 概率 888 铜币；1% 概率 1 铜币
CHECKIN_BIG_COPPER = 888  # 4% 特殊奖励

TYPE_NAMES = dict(_CLASSES_TYPE_NAMES)  # weapon/shield/armor/robe/accessory/staff/focus...
TYPE_NAMES["other"] = "其他"


def local_today():
    """签到日期（取服务器本机日期；建议部署在中国时区机器上）。"""
    return datetime.now().date()


def roll_checkin_reward():
    """按概率返回签到奖励（单位：铜币）。"""
    r = random.random()
    if r < 0.01:             # 1% → 1 铜币
        return 1
    if r < 0.05:             # 4% → 888 铜币
        return CHECKIN_BIG_COPPER
    return random.randint(50, 200)  # 95% → 50~200 铜币


def ensure_user(user_id, nickname):
    """获取或创建用户，并同步昵称（识别群聊用户身份）。"""
    user = db.session.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, nickname=nickname or str(user_id))
        db.session.add(user)
        db.session.commit()
    elif nickname and user.nickname != nickname:
        user.nickname = nickname
        db.session.commit()
    return user


# ---------- 签到 ----------

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


# ---------- 余额 ----------

def cmd_balance(user, group_id, args, at_qqs=None):
    _ensure_initial_tier(user)
    items = owned_items(user)
    prof = (user.profession or "") or ""
    class_txt = f"{class_name(prof)} · {_user_title(user)}" if prof else "未转职·冒险者"
    return (f"当前资产：{format_currency(user.copper)}\n"
            f"职业/称号：{class_txt}\n"
            f"持有装备 {len(items)} 件；累计签到 {user.total_checkin} 次，连续签到 {user.checkin_streak} 天。")


# ---------- 背包 ----------


def _user_title(user):
    """完整晋级称号（如 见习战士）；未转职显示「冒险者」（不带职业阶级前缀）。"""
    prof = (user.profession or "") or ""
    if not prof:
        return "冒险者"
    return tier_title(prof, (user.tier or 0))


def _ensure_initial_tier(user):
    """老玩家继承（D13）：未定阶（tier==0）时按历史最高层自动定初始阶级（幂等）。

    在所有会展示称号 / 过滤装备的入口统一调用，保证「转职 / 武器库 / 背包 / 余额 /
    晋升 / 购买 / 进地下城」各路径行为一致，不再出现“先进地下城白送高阶、先转职却要从头升”
    的差别。调用方处于应用上下文内；本函数内部负责 commit。
    """
    if user is None or (user.tier or 0) > 0:
        return
    if historical_best_layer(user) <= 0:
        return
    sync_initial_tier(user)
    db.session.commit()


def _item_tier(it):
    t = it.get("tier")
    if t is None:
        t = tier_of_price(it.get("price", 0))
    return int(t)


def cmd_bag(user, group_id, args, at_qqs=None):
    """查看当前持有的武具与矿石（/背包）。"""
    _ensure_initial_tier(user)
    rows = owned_item_rows(user)
    if not rows:
        return ("🎒 你的背包空空如也。\n"
                "前往 /武器库 购买一件装备吧！（持有任意装备即可进入 /地下城）")
    prof = (user.profession or "") or ""
    cl_line = class_line(prof) if prof else None
    rare_ids = rare_item_ids()
    lines = [f"🎒 我的背包（共 {len(rows)} 件） · {_user_title(user)}"]
    groups = {}
    for it, is_new in rows:
        groups.setdefault(it.get("type", "other"), []).append((it, is_new))
    for t in sorted(groups, key=lambda x: TYPE_NAMES.get(x, x) or x):
        lst = sorted(groups[t], key=lambda x: x[0]["price"])
        lines.append(f"—— {TYPE_NAMES.get(t, t)} ——")
        # 同名合并计数（保留 is_new 只要有一个新即可标 new！）
        seen = {}
        for it, is_new in lst:
            if it["name"] not in seen:
                seen[it["name"]] = {"it": it, "cnt": 1, "new": bool(is_new)}
            else:
                seen[it["name"]]["cnt"] += 1
                seen[it["name"]]["new"] = seen[it["name"]]["new"] or bool(is_new)
        for name, info in seen.items():
            it = info["it"]
            suffix = f" ×{info['cnt']}" if info["cnt"] > 1 else ""
            mark = " new！" if info["new"] else ""
            # Boss 稀有装备：✦ 星号 + (稀有) 标注（D-B12）
            rare = it.get("id") in rare_ids
            disp = f"✦ {name}" if rare else name
            rare_tag = "(稀有)" if rare else ""
            # 转职后另一职业专属装备标注“暂不生效”
            unusable = ""
            if cl_line and item_line(it) not in (LINE_ANY, cl_line):
                unusable = "（另一职业·不生效）"
            lines.append(f"· {disp}{suffix}{mark}{rare_tag}（{_item_desc(it)}）{unusable}")
    # 矿石展示（稀有度前缀）
    ores = ore.owned_ores(user.user_id)
    if ores:
        lines.append("—— ⛏️ 矿石 ——")
        _ore_mark = {"myth": "★", "legendary": "✦", "rare": "◆", "common": "·"}
        for o in ores:
            lines.append(f"{_ore_mark.get(o['rarity'], '·')} {o['name']} ×{o['count']}")
    lines.append(f"当前资产：{format_currency(user.copper)}")
    return "\n".join(lines)


# ---------- 武器库（原武具店） ----------

def _item_desc(it):
    parts = []
    if it.get("attack"):
        parts.append(f"攻{it['attack']}")
    if it.get("defense"):
        parts.append(f"防{it['defense']}")
    if it.get("hp"):
        parts.append(f"命{it['hp']}")
    if it.get("mp"):
        parts.append(f"魔{it['mp']}")
    if it.get("agility"):
        parts.append(f"敏{it['agility']}")
    if it.get("intelligence"):
        parts.append(f"智{it['intelligence']}")
    return " ".join(parts)


def cmd_shop(user, group_id, args, at_qqs=None):
    """武器库：按职业可用(line/any) + 类型分组展示；同类装备排在一起（D-issue3）。

    展示规则：未转职先引导转职；已转职展示本职业可用全部档位并按类型分组，
    组内从低阶到高阶排列 —— 低于/等于当前阶级的都可直接购买（便于低阶换装，D-issue2），
    高于当前阶级的带 🔒（晋升解锁，仅预览不逐条刷屏）。
    """
    _ensure_initial_tier(user)
    prof = (user.profession or "") or ""
    if not prof:
        return ("💡 你是冒险者，请先选择职业：\n"
                "/转职 战士（物理近战） 或 /转职 魔法师（魔法施法）\n"
                "选定职业后，/武器库 将展示你可购买的装备。")
    line = class_line(prof)
    my_tier = user.tier or 0
    items = load_equipment()
    # 本职业可用（职业 line 或 通用 any）
    usable = [it for it in items if item_line(it) in (line, LINE_ANY)]
    # 类型展示顺序（两职业通用的放前，便于换装时一眼找到同类）
    order = ["weapon", "staff", "shield", "focus", "armor", "robe", "accessory", "other"]
    groups = {}
    for it in usable:
        groups.setdefault(it.get("type", "other"), []).append(it)
    lines = [f"⚔️ 武器库 · {_user_title(user)}（同类排在一起；当前阶级可购全部档，更高阶 🔒 需晋升）"]
    for ty in order:
        if ty not in groups:
            continue
        arr = sorted(groups[ty], key=lambda x: (_item_tier(x), x["price"]))
        purchasable = [it for it in arr if _item_tier(it) <= my_tier]
        locked = [it for it in arr if _item_tier(it) > my_tier]
        if not arr:
            continue
        lines.append(f"—— {TYPE_NAMES.get(ty, ty)} ——")
        for it in purchasable:
            lines.append(f"· {it['name']}（{_item_desc(it)}）{format_currency(it['price'])}")
        if locked:
            t0 = _item_tier(locked[0])
            lines.append(f"· 🔒 更高阶 {TYPE_NAMES.get(ty, ty)}（T{t0} 起）需 /晋升 解锁")
    lines.append("—— 提示：/购买 装备名；/转职 切换职业；/晋升 提升阶级解锁更高阶 ——")
    return "\n".join(lines)


# ---------- 转职 / 晋升（v3） ----------

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
    _ensure_initial_tier(user)  # 转职成功：老玩家立即按历史最高层继承对应阶级（不倒退、免逐步付费）
    db.session.commit()  # 确保 profession（及可能的定阶）落库
    return (f"⚔️ 转职成功！你已成为 {class_name(cid)}（{_user_title(user)}）。\n"
            f"地下城将只计算本职业与通用装备的属性（不混搭）。")


def cmd_promote(user, group_id, args, at_qqs=None):
    """晋升阶级（/晋升）：需历史最高层达标 + 消耗货币。"""
    _ensure_initial_tier(user)
    cur = user.tier or 0
    nxt = next_promotion(cur)
    if nxt is None:
        return f"你已达最高阶级 {_user_title(user)}！"
    best = historical_best_layer(user)
    need_layer = TIER_LAYER.get(nxt, 0)
    cost = tier_promotion_cost(nxt)
    if best < need_layer:
        return (f"晋升 {tier_title(user.profession, nxt)} 需要：\n"
                f"地下城历史最高层 ≥ {need_layer}（当前 {best}）\n"
                f"货币 ≥ {format_currency(cost)}（当前 {format_currency(user.copper)}）")
    if user.copper < cost:
        return (f"货币不足，不能晋升！\n"
                f"晋升 {tier_title(user.profession, nxt)} 需 {format_currency(cost)}，"
                f"你只有 {format_currency(user.copper)}。\n"
                f"（层数已达标：历史最高 {best} ≥ {need_layer}）")
    user.copper -= cost
    user.tier = nxt
    db.session.commit()
    # 装备档位最高到 T5（武具店 45000）/ T4~5（铁匠铺顶级也早已解锁）：
    # T6 灭世、T7 至尊 为纯称号荣誉阶，不再新增装备解锁。
    if nxt >= 6:
        unlock_note = "（纯称号荣誉阶：武具店/铁匠铺顶级装备早已解锁，无新装备）"
    else:
        unlock_note = f"武器库已解锁 {tier_title(user.profession, nxt)} 阶级的装备！"
    return (f"🎉 晋升成功！{tier_title(user.profession, cur)} → {tier_title(user.profession, nxt)}\n"
            f"消耗 {format_currency(cost)}，剩余 {format_currency(user.copper)}。\n"
            f"{unlock_note}")


# ---------- 铁匠铺（/铁匠铺、/锻造） ----------

def _forge_unlocked(user):
    """铁匠铺解锁门槛：玩家曾到达地下城 400 层（与矿石资格一致）。"""
    return historical_best_layer(user) >= forge.FORGE_MIN_LAYER


def cmd_forge_shop(user, group_id, args, at_qqs=None):
    """查看铁匠铺配方（全部超越武具店顶级）。"""
    if not _forge_unlocked(user):
        return ("🔨 铁匠铺尚未解锁。\n"
                f"到达地下城第 {forge.FORGE_MIN_LAYER} 层后解锁，可锻造超越武具店顶级的装备！")
    recs = forge.all_forges()
    lines = ["🔨 铁匠铺（/锻造 装备名 制作；全部超越武具店顶级）"]
    cur_level = None
    for r in recs:
        if r.get("level") != cur_level:
            cur_level = r.get("level")
            lines.append(f"—— Lv{cur_level} ——")
        cost = forge.format_cost(r.get("cost", {})) + f" + {format_currency(r.get('price', 0))}"
        lines.append(f"· {r['name']}（{TYPE_NAMES.get(r['type'], r['type'])}）{_item_desc(r)}")
        lines.append(f"   消耗：{cost}")
    lines.append("—— 提示：铁匠铺装备无法在 /出售 回收 ——")
    return "\n".join(lines)


def cmd_forge(user, group_id, args, at_qqs=None):
    """锻造装备：消耗铜币 + 矿石（不能赊账），成功后入背包。"""
    _ensure_initial_tier(user)
    name = (args or "").strip()
    if not name:
        return "用法：/锻造 装备名（/铁匠铺 查看配方）"
    if not _forge_unlocked(user):
        return (f"🔨 铁匠铺尚未解锁。到达地下城第 {forge.FORGE_MIN_LAYER} 层后解锁，"
                f"可锻造超越武器库顶级的装备！")
    rec = forge.find_forge(name)
    if rec is None:
        hits = forge.suggest_forges(name)
        hint = f"，你是不是想锻造：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"铁匠铺没有「{name}」{hint}\n发送 /铁匠铺 查看配方。"
    # 职业/阶级校验（v3）：锻造产物同购买一样受职业与阶级限制
    prof = (user.profession or "") or ""
    line = item_line(rec)
    if line != LINE_ANY:
        if not prof:
            return (f"💡 锻造「{rec['name']}」需要职业。\n"
                    f"请先 /转职 战士 或 /转职 魔法师。")
        if line != class_line(prof):
            other = "战士" if line == "physical" else "魔法师"
            return f"❌ 「{rec['name']}」是{other}的锻造装备，你无法使用。"
    # 阶级校验（v3）：仅配方显式带 tier 时按阶级限制；旧配方沿用 400 层铁匠铺门槛即可
    req_tier = rec.get("tier")
    if req_tier is not None and (user.tier or 0) < int(req_tier):
        return (f"🔒 「{rec['name']}」需要晋升到 {tier_title(prof, int(req_tier)) or req_tier} 才能锻造。\n"
                f"当前阶级 {_user_title(user)}。")
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


# ---------- 购买 / 出售 ----------

def cmd_buy(user, group_id, args, at_qqs=None):
    """购买装备（/购买），职业+阶级双重校验。"""
    _ensure_initial_tier(user)
    names = [n for n in (args or "").split()]
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
    bad = None
    for it in items:
        line = item_line(it)
        if line != LINE_ANY:
            if not prof:
                return (f"💡 购买「{it['name']}」需要职业。\n"
                        f"请先 /转职 战士（物理）或 /转职 魔法师（魔法）。")
            if line != class_line(prof):
                other = "战士" if line == "physical" else "魔法师"
                return f"❌ 「{it['name']}」是{other}的装备，你无法购买使用。"
        req_tier = it.get("tier")
        if req_tier is None:
            req_tier = tier_of_price(it.get("price", 0))
        if (user.tier or 0) < int(req_tier):
            return (f"🔒 「{it['name']}」需要晋升到更高阶级才能购买。\n"
                    f"当前阶级 {_user_title(user)}；发送 /晋升 查看晋升条件。")
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


def cmd_sell(user, group_id, args, at_qqs=None):
    """出售装备（购买价 60%），支持空格分隔批量出售（例：/出售 短剑 圆盾）。"""
    names = [n for n in (args or "").split()]
    if not names:
        return "用法：/出售 商品名 [商品名 ...]（例如 /出售 短剑 圆盾）"
    sold, missing, not_owned = [], [], []
    total = 0
    for name in names:
        item = find_item(name)
        if item is None:
            missing.append(name)
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
        sp = int(item["price"] * 0.6)   # 售价 = 购买价 60%
        sold.append((item["name"], sp))
        total += sp
        user.copper += sp
        db.session.delete(row)
    db.session.commit()
    lines = []
    if sold:
        if len(sold) == 1:
            n, sp = sold[0]
            lines.append(f"出售成功！卖出「{n}」×1，获得 {format_currency(sp)}（购买价 60%）")
        else:
            detail = "、".join(n for n, _ in sold)
            lines.append(f"出售成功！卖出：{detail}，共获得 {format_currency(total)}（购买价 60%）")
    if missing:
        lines.append("未找到：" + "、".join(f"「{n}」" for n in missing))
    if not_owned:
        lines.append("没有：" + "、".join(f"「{n}」" for n in not_owned))
    if not lines:
        return "你没有可出售的装备。"
    lines.append(f"当前资产：{format_currency(user.copper)}")
    return "\n".join(lines)


# ---------- 地下城 ----------

def _set_ore_entry_state(user, layer):
    """进入地下城时快照矿石资格：当前层数 > 400 本轮可掉落矿石（进入时判定，本轮有效）。"""
    user.dungeon_ore_eligible = 1 if layer > ore.ORE_MIN_LAYER else 0
    user.dungeon_ore_last = time.time()


def _ore_entry_note(layer):
    if layer > ore.ORE_MIN_LAYER:
        return "\n💎 你已解锁稀有矿石掉落（每 15 分钟一个结算周期）！"
    return ""


def _dungeon_enter(user):
    if user.dungeon_layer > 0:
        return f"你已在地下城第 {user.dungeon_layer} 层中。"
    count = db.session.execute(
        db.select(db.func.count()).select_from(UserItem).where(UserItem.user_id == user.user_id)
    ).scalar()
    if count < 1:
        return "你还没有任何装备，无法进入地下城。先去 /武器库 购买一件装备吧！"
    sync_initial_tier(user)
    db.session.commit()

    stats = effective_stats(user, owned_items(user))
    speed = dungeon_speed(stats)
    prof = (user.profession or "") or ""
    class_note = ""
    if not prof:
        class_note = ("\n💡 你尚未选择职业（地下城暂按最优自动生效）。\n"
                      f"发送 /转职 战士 或 /转职 魔法师 选定职业（影响装备可用）。")

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
        return (f"⚔️ 已从上次进度继续冒险！（{_user_title(user)}{boss_note}）\n"
                f"你回到地下城第 {layer} 层（剩余进度 {progress:.0f} / 总计 {effective_layer_total(layer):.0f}）\n"
                f"推进速度：{speed:.2f} 进度/秒；金币速度：约 {coin_per_5sec(layer):.4f} 铜币/5秒\n"
                f"地下城内可使用 /签到、/余额、/帮助 与 /地下城 退出。{ore_note}{class_note}")

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
            f"通关本层进度：{effective_layer_total(1):.0f}；金币速度：约 {coin_per_5sec(1):.4f} 铜币/5秒\n"
            f"地下城内可使用 /签到、/余额、/帮助 与 /地下城 退出。{class_note}")


def _dungeon_status(user):
    if user.dungeon_layer <= 0:
        if user.saved_dungeon_layer and user.saved_dungeon_layer > 0:
            total = effective_layer_total(user.saved_dungeon_layer)
            remaining = max(user.saved_dungeon_progress or 0.0, 0.0)
            return (f"💾 你保存了地下城进度：第 {user.saved_dungeon_layer} 层"
                    f"（剩余 {remaining:.0f} / 总计 {total:.0f}）。\n"
                    f"发送 /地下城 进入 可继续冒险。")
        return "你当前不在地下城中。使用 /地下城 进入 开始冒险。"
    total = effective_layer_total(user.dungeon_layer)
    remaining = max(user.dungeon_progress, 0.0)
    done = total - remaining
    pct = (done / total * 100) if total else 0
    bt = boss_type(user.dungeon_layer)
    bt_txt = "（BOSS）" if bt else ""
    return (f"📍 地下城第 {user.dungeon_layer} 层{bt_txt} · {_user_title(user)}\n"
            f"进度：{pct:.1f}%（剩余 {remaining:.0f} / 总计 {total:.0f}）\n"
            f"金币速度：约 {coin_per_5sec(user.dungeon_layer):.4f} 铜币/5秒\n"
            f"本次地下城已获得 {user.dungeon_run_coins} 铜币；累计通关 {user.dungeon_cleared} 层，"
            f"累计获得 {user.dungeon_coins_earned} 铜币。\n"
            f"当前资产：{format_currency(user.copper)}")


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
    # 退出后矿石资格作废，下次进入重新判定
    user.dungeon_ore_eligible = 0
    user.dungeon_ore_last = None
    db.session.commit()
    return (f"🚪 你已离开地下城（第 {layer} 层，剩余进度 {progress:.0f}）。\n"
            f"进度已保存，下次 /地下城 进入 可从第 {layer} 层继续冒险。\n"
            f"本次地下城获得 {run_coins} 铜币，累计通关 {cleared_total} 层。\n"
            f"当前资产：{format_currency(user.copper)}")


def cmd_dungeon(user, group_id, args, at_qqs=None):
    sub = (args or "").strip().lower()
    if sub in ("进入", "enter"):
        return _dungeon_enter(user)
    if sub in ("退出", "exit", "离开"):
        return _dungeon_exit(user)
    if sub in ("状态", "status"):
        return _dungeon_status(user)
    if not sub and (user.dungeon_layer > 0
                    or (user.saved_dungeon_layer and user.saved_dungeon_layer > 0)):
        return _dungeon_status(user)
    return ("地下城指令：\n"
            "/地下城 进入 - 进入/继续地下城（退出后保留进度，可直达上次位置）\n"
            "/地下城 状态 - 查看当前层数/进度/金币/保存进度\n"
            "/地下城 退出 - 离开地下城（保存当前进度）")


# ---------- 帮助 ----------

def cmd_help(user, group_id, args, at_qqs=None):
    return ("可用命令（消息以 / 开头）：\n"
            "/签到 - 每日签到，随机获得铜币/银币\n"
            "/余额 - 查看当前资产/职业/称号\n"
            "/背包 - 查看当前持有的武具与矿石\n"
            "/武器库 - 查看可购买的装备（按职业与阶级过滤）\n"
            "/购买 商品名 [商品名...] - 批量购买装备（需职业/阶级符合）\n"
            "/出售 商品名 [商品名...] - 批量出售装备（购买价 60%）\n"
            "/转职 战士|魔法师 - 选择职业（切换职业）\n"
            "/晋升 - 按地下城进度+货币提升阶级\n"
            "/地下城 进入/状态/退出 - 地下城冒险（10/50/100 层 Boss 掉落）\n"
            "/铁匠铺 - 查看锻造配方（400 层解锁，超越武器库顶级）\n"
            "/锻造 装备名 - 消耗铜币+矿石制作装备（需职业/阶级符合）\n"
            "/挑战 @对方 - 发起对战（随机胜负，每天 3 次）\n"
            "/踢 @对方 - 生成踢人图（30 秒冷却）\n"
            "/撅 @对方 - 生成撅人 GIF（30 秒冷却）\n"
            "/佬 @对方 - 生成大佬致敬图（30 秒冷却）\n"
            "/帮助 - 显示本帮助")


# ---------- 踢人（/踢 @对方） ----------

def cmd_kick(user, group_id, args, at_qqs=None):
    """/踢 @B：A 的头像贴在底图 (75,34)，B 的头像贴在 (400,98)，120x120，发图到群。

    返回 dict（图片消息）或文本（提示）。30 秒冷却。
    """
    remain = check_cooldown(user.user_id)
    if remain > 0:
        return f"⏳ 功能冷却中，请 {int(remain) + 1} 秒后再试"
    targets = at_qqs or []
    if not targets:
        return "用法：/踢 @对方（例如 /踢 @张三）"
    target = targets[0]
    if target == user.user_id:
        return "不能踢自己"
    # 被 @ 用户的显示名：优先本地已记录的群名片/昵称，其次 QQ 号
    target_row = db.session.get(User, target)
    target_name = target_row.nickname if target_row and target_row.nickname else str(target)
    try:
        out_path = build_kick_image(user.user_id, target)
    except Exception as exc:
        return f"生成图片失败：{exc}"
    mark_cooldown(user.user_id)
    return {
        "type": "image",
        "file": out_path,
        "text": f"🦵 {user.nickname or user.user_id} 一脚把 {target_name} 踢飞了",
        "target": target,
        "group_id": group_id,
    }


# ---------- 对战（/挑战 @对方） ----------

# 演出招式库（随机组合，纯文字演出）
_MOVE_OPEN = [
    "使出一记上勾拳，", "一记飞腿直踹，", "挥拳重重砸向，", "一记肘击横扫，",
    "凌空一记膝撞顶向", "一记摆拳呼啸而至，",
]
_MOVE_HIT = [
    "正中胸口", "被扫倒在地", "击中面门", "被震退数步", "被撞在墙上",
    "被命中小腹", "被击得连连后退",
]
_MOVE_RETURN = [
    "反手一记右鞭腿，", "随即一记头槌，", "侧身一记回旋踢，", "紧跟着一记直拳，",
    "借势一记下劈腿，", "转身一记扫堂腿，",
]
_FINISH = [
    "以一记上勾拳终结了比赛", "用一记头槌终结了比赛", "以一记回旋踢终结了比赛",
    "用一记升龙拳结束了战斗", "以一记抱摔终结了比赛", "用一记膝撞终结了比赛",
]


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
    """/挑战 @B：随机 50/50 胜负，三条文字演出（各间隔 2 秒）。

    赌注基准 = max(全服资产中位数 × 3%, 500 铜币)；
    挑战方败 → 支付 基准×1.2；被挑战方败 → 支付 基准×0.8；
    不能赊账（资产不足则全部支付）；发起方每天限 3 次。
    """
    targets = at_qqs or []
    if not targets:
        return "用法：/挑战 @对方（每天 3 次）"
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
    msg1 = f"{an} {random.choice(_MOVE_OPEN)}{bn} {random.choice(_MOVE_HIT)}！"
    msg2 = f"{bn} {random.choice(_MOVE_RETURN)}{an} {random.choice(_MOVE_HIT)}！"
    msg3 = f"{wname} {random.choice(_FINISH)}，{wname} 获胜！{wname} 获得 {format_currency(pay)}。"
    return {
        "type": "challenge_show",
        "group_id": group_id,
        "msgs": [msg1, msg2, msg3],
        "delay": 2,
    }


# ---------- 撅（/撅 @对方） ----------

def cmd_jue(user, group_id, args, at_qqs=None):
    """/撅 @B：多帧 GIF 合成。A 头像 120x120 圆形、B 头像 120x120 圆形逆时针旋转 90°，
    按 3 帧序列底图逐帧贴上（每帧坐标见 kick.JUGE_POSITIONS），发 GIF 到群。30 秒冷却。
    5% 概率 A/B 位置互换（A 撅失败被 B 反撅）。
    """
    remain = check_cooldown(user.user_id)
    if remain > 0:
        return f"⏳ 功能冷却中，请 {int(remain) + 1} 秒后再试"
    targets = at_qqs or []
    if not targets:
        return "用法：/撅 @对方（例如 /撅 @张三）"
    target = targets[0]
    if target == user.user_id:
        return "不能撅自己"
    target_row = db.session.get(User, target)
    target_name = target_row.nickname if target_row and target_row.nickname else str(target)
    an = user.nickname or str(user.user_id)
    bn = target_name
    flip = random.random() < 0.02   # 2% 概率位置互换
    try:
        out_path = build_jue_image(user.user_id, target, flip=flip)
    except Exception as exc:
        return f"生成图片失败：{exc}"
    mark_cooldown(user.user_id)
    text = (f"😵 {an} 撅 {bn} 失败，被 {bn} 撅了！" if flip
            else f"🍑 {an} 撅了 {bn}")
    return {
        "type": "image",
        "file": out_path,
        "text": text,
        "target": target,
        "group_id": group_id,
    }


# ---------- 佬（/佬 @对方） ----------

def cmd_lao(user, group_id, args, at_qqs=None):
    """/佬 @B：dalao.png 底图合成。A 头像 54x54 圆形贴 (91,121)、B 头像 54x54 圆形贴 (200,3)，
    发图到群。30 秒冷却。
    """
    remain = check_cooldown(user.user_id)
    if remain > 0:
        return f"⏳ 功能冷却中，请 {int(remain) + 1} 秒后再试"
    targets = at_qqs or []
    if not targets:
        return "用法：/佬 @对方（例如 /佬 @张三）"
    target = targets[0]
    if target == user.user_id:
        return "不能称自己为佬"
    target_row = db.session.get(User, target)
    target_name = target_row.nickname if target_row and target_row.nickname else str(target)
    try:
        out_path = build_dalao_image(user.user_id, target)
    except Exception as exc:
        return f"生成图片失败：{exc}"
    mark_cooldown(user.user_id)
    return {
        "type": "image",
        "file": out_path,
        "text": f"🤗 {user.nickname or user.user_id} 抱住了 {target_name} 的大腿",
        "target": target,
        "group_id": group_id,
    }
COMMANDS = {
    "签到": cmd_checkin, "checkin": cmd_checkin, "qiandao": cmd_checkin,
    "余额": cmd_balance, "balance": cmd_balance, "yue": cmd_balance,
    "背包": cmd_bag, "bag": cmd_bag, "beibao": cmd_bag,
    "武器库": cmd_shop, "武器": cmd_shop, "wqp": cmd_shop,
    "武具店": cmd_shop, "shop": cmd_shop, "wujudian": cmd_shop,
    "购买": cmd_buy, "buy": cmd_buy, "goumai": cmd_buy,
    "出售": cmd_sell, "sell": cmd_sell, "chushou": cmd_sell,
    "转职": cmd_class, "class": cmd_class, "zhuanzhi": cmd_class,
    "晋升": cmd_promote, "promote": cmd_promote, "jinsheng": cmd_promote,
    "地下城": cmd_dungeon, "dungeon": cmd_dungeon, "dixiacheng": cmd_dungeon,
    "铁匠铺": cmd_forge_shop, "forgeshop": cmd_forge_shop, "tiejiangpu": cmd_forge_shop,
    "锻造": cmd_forge, "forge": cmd_forge, "duanzao": cmd_forge,
    "踢": cmd_kick, "kick": cmd_kick, "ti": cmd_kick,
    "撅": cmd_jue, "jue": cmd_jue,
    "佬": cmd_lao, "lao": cmd_lao,
    "挑战": cmd_challenge, "challenge": cmd_challenge, "tiaozhan": cmd_challenge,
    "帮助": cmd_help, "help": cmd_help, "bangzhu": cmd_help,
}

# 地下城内允许的命令（其余一律拒绝）
DUNGEON_ALLOWED = {"签到", "checkin", "qiandao",
                   "地下城", "dungeon", "dixiacheng",
                   "余额", "balance", "yue",
                   "背包", "bag", "beibao",
                   "武器库", "武器", "wqp", "shop", "wujudian",
                   "转职", "class", "zhuanzhi",
                   "晋升", "promote", "jinsheng",
                   "帮助", "help", "bangzhu",
                   "踢", "kick", "ti",
                   "撅", "jue",
                   "佬", "lao",
                   "挑战", "challenge", "tiaozhan"}


def _prepend_boss_report(user, reply):
    """若本次结算有 Boss 通关掉落，把播报拼到回复开头。"""
    lines = take_boss_report(user.user_id)
    if not lines:
        return reply
    head = "🎁 地下城 Boss 战利品：\n" + "\n".join(lines) + "\n"
    if isinstance(reply, dict) and isinstance(reply.get("text"), str):
        reply["text"] = head + reply["text"]
        return reply
    if isinstance(reply, str):
        return head + reply
    return reply


def dispatch_command(text, user, group_id, at_qqs=None):
    """解析 "/命令 [参数]" 并执行。

    返回值为文本 str，或图片消息 dict（{"type":"image","file":...,"text":...}）。
    """
    text = (text or "").strip()
    parts = text.split(maxsplit=1)
    raw = parts[0] if parts else ""
    if not raw.startswith("/") or len(raw) < 2:
        return None
    name = raw[1:].strip().lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    handler = COMMANDS.get(name)

    # 地下城状态结算与动作限制
    in_dungeon = user.dungeon_layer and user.dungeon_layer > 0
    if in_dungeon:
        settle_dungeon(user)  # 先按流逝时间结算（可能触发 Boss 掉落），再判断
        if handler is None or name not in DUNGEON_ALLOWED:
            return (f"⚠️ 你正在地下城第 {user.dungeon_layer} 层中。\n"
                    f"地下城内可使用 /签到、/余额、/背包、/帮助、/踢、/撅、/佬、/挑战 或 /地下城 退出。")

    if handler is None:
        # 未知指令计数：达到阈值（第 3 次）发 beat.jpg+头像合成图并停止响应
        user.unknown_count = (user.unknown_count or 0) + 1
        db.session.commit()
        if user.unknown_count >= UNKNOWN_LIMIT:
            # 第 3 次（达到阈值）：发 beat+头像合成图；此后（第 4 次起）完全静默
            if user.unknown_count == UNKNOWN_LIMIT:
                try:
                    beat_path = build_beat_image(user.user_id)
                except Exception:
                    beat_path = UNKNOWN_IMAGE
                return {
                    "type": "image",
                    "file": beat_path,
                    "text": f"⚠️ {user.nickname or user.user_id} 无效指令过多，不再响应未知指令",
                }
            return None
        return "未知指令，发送 /帮助 查看可用命令。"

    try:
        reply = handler(user, group_id, args, at_qqs)
        return _prepend_boss_report(user, reply)
    except Exception as exc:
        db.session.rollback()
        return f"指令执行出错：{exc}"

