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
import time
from datetime import datetime, timedelta

from models import db, User, CheckinRecord, UserItem, TurnItem
from currency import format_currency
from equipment import load_equipment, find_item, suggest_items
from classes import (
    class_line, item_line, class_name, find_class, all_classes,
    LINE_ANY, TYPE_NAMES as _CLASSES_TYPE_NAMES,
)
from tiers import (
    tier_title, tier_of_price, next_promotion, TIER_LAYER, MAX_TIER,
    tier_promotion_cost, tier_price_cap, TIER_ATTR_BONUS,
)
from dungeon import (
    BASE_STATS, LAYER1_TOTAL, layer_total, coin_rate_per_sec, coin_per_5sec,
    effective_stats, dungeon_speed, owned_items, owned_item_rows, settle_dungeon,
    effective_layer_total, boss_type, historical_best_layer,
    take_boss_report, rare_item_ids, find_any_item,
)
import ore
import material
import forge
import boss
import boss_gear
import alchemy
import consumable
import dungeon

# 未知指令触发阈值：累计超过该次数后，发出告警且不再响应该用户的未知指令
UNKNOWN_LIMIT = 3

# 签到奖励概率（单位：铜币）：
#   95% 概率 50~200 铜币；4% 概率 888 铜币；1% 概率 1 铜币






# ---- 地下城命令实现已迁移至 dungeon_service.commands（单一实现源），此处仅转发 ----
from dungeon_service.commands import (
    cmd_dungeon, cmd_bag, cmd_shop, cmd_buy, cmd_sell, cmd_class, cmd_promote,
    cmd_forge, cmd_forge_shop, cmd_alchemy, cmd_use, cmd_challenge, cmd_boss,
    cmd_lottery, cmd_turn, cmd_checkin, cmd_balance,
    _user_title, _buff_status_block, local_today,
)
from dungeon_service.game_core import game as _game, _DUNGEON_COMMANDS as _GAMECORE_ALIASES


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


def ensure_qq_user(openid, nickname=""):
    """按 QQ 官方 openid 获取或创建用户（官方通道专用）。

    官方接口不返回真实 QQ 号，因此这类用户分配**负数 user_id**（真实 QQ 号为正值，不会冲突），
    并把 openid 落库，便于后续把同一人的两种身份合并。
    """
    user = db.session.execute(
        db.select(User).where(User.openid == openid)
    ).scalars().first()
    if user is not None:
        if nickname and user.nickname != nickname:
            user.nickname = nickname
            db.session.commit()
        return user

    for _ in range(5):
        min_id = db.session.execute(db.select(db.func.min(User.user_id))).scalar()
        new_id = min(int(min_id or 0), 0) - 1
        user = User(user_id=new_id, nickname=nickname or openid, openid=openid)
        db.session.add(user)
        try:
            db.session.commit()
            return user
        except Exception:
            # 并发建号撞主键：回滚后重算一次最小 id
            db.session.rollback()
            user = None
    return user


def normalize_command(text):
    """允许省略前导 '/'：把「签到」这类纯中文命令词自动补成 '/签到'。

    仅对**纯中文**命令词生效，避免把聊天里单个字母（如 b / shop / help）误判为命令。
    已带 '/' 的输入原样返回。
    """
    s = (text or "").strip()
    if not s or s.startswith("/"):
        return s
    head = s.split(maxsplit=1)[0].lower()
    if head in COMMANDS and all("\u4e00" <= ch <= "\u9fff" for ch in head):
        return "/" + s
    return s


# ---------- 签到 ----------



# ---------- 余额 ----------



# ---------- 背包 ----------








# ---------- 武器库（原武具店） ----------

# 装备属性展示顺序（背包/武器库/锻造/Boss 掉落统一）：攻→魔→敏→智→命→防
# —— 如需调整顺序改此表即可。






# ---------- 转职 / 晋升（v3） ----------





# ---------- 铁匠铺（/铁匠铺、/锻造） ----------













# ---------- 购买 / 出售 ----------





# ---------- 地下城 ----------

















# ---------- 帮助 ----------

def cmd_help(user, group_id, args, at_qqs=None):
    return ("可用命令（直接发命令词即可，加 / 也认）：\n"
            "\n"
            "⚔️ 地下城（冒险）\n"
            "签到 - 每日签到，随机获得铜币/银币\n"
            "余额 - 查看当前资产/职业/称号\n"
            "背包 - 查看当前持有的武具与矿石\n"
            "武器库 - 查看可购买的装备（按职业与阶级过滤；武器库 开关 隐藏低等级装备防刷屏）\n"
            "购买 商品名 [商品名...]（或 全部）- 批量购买装备（需职业/阶级符合；全部=一键购入当前可买最高档每部位最好一件）\n"
            "出售 商品名 [商品名...]（或 全部）- 批量出售装备（购买价 60%；全部=一键出售所有可出售·未穿戴·非本部位最高评分的非专属装备）\n"
            "转职 战士|魔法师|魔剑士|近战法师 - 选择职业（切换职业；魔剑士=物理武器+魔法副手+法袍，近战法师=法杖+物理盾牌+战甲）\n"
            "晋升 - 按地下城进度+货币提升阶级\n"
            "地下城 进入/状态/排名/退出 - 地下城冒险（最高 3600 层；进入自动穿戴最优 4 件、推进只算穿戴中装备；命名守关 Boss 需战力判定，失败重置进度收益照常；状态含生效中药水/道具；排名看本群战力 TOP10 与专属拥有；套装：4件稀有/锻造任意混合全属性×1.1（背包可见生效），命名Boss专属×1.2/件累乘（三大Boss×1.5/件、3600×2，普通套装1.1不叠加））\n"
            "铁匠铺 - 查看锻造配方；铁匠铺 分解 <装备名> 分解锻造装回收矿石（普通返半、稀有返六成、传说/神话按50%概率）\n"
            "锻造 装备名|推荐 - 消耗铜币+矿石制作装备（需职业/阶级符合；推荐 按背包算可锻）\n"
            "boss 列表 - 查看守关 Boss（挑战统一走 挑战 <Boss名|层数|称号>；普通每日共 3 次、1000·2000·3000·3600 每日各 1 次，首通必出 Boss 材料）\n"
            "炼金 [配方名|配方|列表|推荐] - 查看/制作药水·道具（消耗材料+矿石；输入 配方/列表 查看全部、推荐 按背包算可炼）\n"
            "使用 物品名 - 使用药水/道具(药水/道具各同时仅一种, 新用替换并刷新时长)\n"
            "挑战 @对方 · 列表 · 装备 [名称] · <Boss名|层数|称号> - 玩家对战 · Boss 清单与剩余次数 · 专属装备属性与全服余量 · Boss 挑战（如 挑战 1000层）\n"
            "转转 捐赠 <装备名> · 乞讨 - 装备互助：捐赠入公共库，乞讨一件符合自己等级的装备（每天 3 次）\n"
            "祈愿 1|2|3 [次数] - 祈愿：消耗金钱抽装备/材料/矿石/道具/金钱（5铜/5银/5金，各档概率与稀有度不同；第二参数为次数，最多 10；原「抽奖」仍可用）\n"
            "\n"
            "帮助 - 显示本帮助")


# ---------- 对战（/挑战 @对方） ----------

# 演出招式库（随机组合，纯文字演出）
_MOVE_HIT = [
    "正中胸口", "被扫倒在地", "击中面门", "被震退数步", "被撞在墙上",
    "被命中小腹", "被击得连连后退",
]
_FINISH = [
    "以一记上勾拳终结了比赛", "用一记头槌终结了比赛", "以一记回旋踢终结了比赛",
    "用一记升龙拳结束了战斗", "以一记抱摔终结了比赛", "用一记膝撞终结了比赛",
]








# ---------- Boss 挑战（/boss 列表 / 挑战 <名>） ----------



# ---------- 祈愿（/祈愿 1|2|3，原抽奖） ----------

_LOTTERY_ALIAS = {
    "1": 1, "一": 1, "铜": 1, "5铜": 1, "铜签": 1, "low": 1, "basic": 1,
    "2": 2, "二": 2, "银": 2, "5银": 2, "银签": 2, "mid": 2, "silver": 2,
    "3": 3, "三": 3, "金": 3, "5金": 3, "金签": 3, "high": 3, "gold": 3,
}




# ---------- 转转（捐赠 / 乞讨） ----------

_TURN_DAILY_LIMIT = 3








# ---------- 炼金（/炼金 [配方名]） ----------



# ---------- 使用（/使用 <物品名>） ----------



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
    "挑战": cmd_challenge, "challenge": cmd_challenge, "tiaozhan": cmd_challenge,
    "boss": cmd_boss, "bosslist": cmd_boss, "b": cmd_boss,
    "炼金": cmd_alchemy, "alchemy": cmd_alchemy, "lianjin": cmd_alchemy,
    "使用": cmd_use, "use": cmd_use, "shiyong": cmd_use,
    "转转": cmd_turn, "zhuanzhuan": cmd_turn, "zhuan": cmd_turn, "turn": cmd_turn,
    "祈愿": cmd_lottery, "抽奖": cmd_lottery, "lottery": cmd_lottery, "choujiang": cmd_lottery, "lucky": cmd_lottery,
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
                   "祈愿", "抽奖", "lottery", "choujiang", "lucky",
                   "挑战", "challenge", "tiaozhan",
                   "boss", "bosslist", "b",
                   "炼金", "alchemy", "lianjin",
                   "使用", "use", "shiyong"}


# 会展示“地下城战利品结算”的命令 handler：仅 /地下城（进入/状态/退出/列表）。
# /签到、/余额、/背包 等查看命令不再夹带战报（v2.11.81）——掉落照常入账，
# 战报保留至下次 /地下城 时一并展示。
# 会展示“地下城战利品结算”的命令：仅 /地下城（进入/状态/退出/列表）。
# 地下城命令已走 GameCore 门面，用别名集合判定（原 {cmd_dungeon} 函数对象判定失效）。
_BOSS_REPORT_ALIASES = {"地下城", "dungeon", "dixiacheng"}


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

    # 地下城状态结算与动作限制（保守：命令触发结算，与 v2.11.x 行为一致）
    in_dungeon = user.dungeon_layer and user.dungeon_layer > 0
    if in_dungeon:
        _game.settle(user)  # GameCore 门面结算（等价原 settle_dungeon）
        if handler is None or name not in DUNGEON_ALLOWED:
            return (f"⚠️ 你正在地下城第 {user.dungeon_layer} 层中。\n"
                    f"地下城内可使用 签到、余额、背包、祈愿、帮助、挑战 或 地下城 退出。")

    if handler is None:
        # 未知指令计数：达到阈值（第 3 次）发出告警；此后（第 4 次起）完全静默
        user.unknown_count = (user.unknown_count or 0) + 1
        db.session.commit()
        if user.unknown_count >= UNKNOWN_LIMIT:
            if user.unknown_count == UNKNOWN_LIMIT:
                return f"⚠️ {user.nickname or user.user_id} 无效指令过多，不再响应未知指令"
            return None
        return "未知指令，发送 帮助 查看可用命令。"

    try:
        # 地下城命令统一走 GameCore 门面直调；其余（签到/余额/帮助等）走本地 handler
        if name in _GAMECORE_ALIASES:
            reply = _game.run_command(name, user, group_id, args, at_qqs)
        else:
            reply = handler(user, group_id, args, at_qqs)
        # 战利品结算播报仅在 /地下城 时展示（v2.11.81）；
        # 签到/余额/背包等不再夹带（掉落照常入账，战报保留待下次 /地下城 带出）。
        if name in _BOSS_REPORT_ALIASES:
            return _prepend_boss_report(user, reply)
        return reply
    except Exception as exc:
        db.session.rollback()
        return f"指令执行出错：{exc}"

