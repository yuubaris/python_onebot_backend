# -*- coding: utf-8 -*-
"""数据库模型定义。

货币统一以最小单位“铜币”存储（整数），展示时再换算成银币/金币/王国币/圣王国币。
换算关系：100 铜币 = 1 银币；100 银币 = 1 金币；100 金币 = 1 王国币；100 王国币 = 1 圣王国币。
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Config(db.Model):
    """键值对配置（连接参数等）。"""
    __tablename__ = "config"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, default="")


class GroupWhitelist(db.Model):
    """群聊白名单。"""
    __tablename__ = "group_whitelist"

    group_id = db.Column(db.BigInteger, primary_key=True)
    group_name = db.Column(db.String(128), default="")
    group_openid = db.Column(db.String(128), default="", nullable=False, index=True)  # QQ 官方群 openid（官方通道用）
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.now)


class User(db.Model):
    """用户（群成员身份）。"""
    __tablename__ = "user"

    user_id = db.Column(db.BigInteger, primary_key=True)
    nickname = db.Column(db.String(128), default="")
    # QQ 官方机器人：官方不返回真实 QQ 号，用 openid 标识用户（官方通道用户；空=OneBot 用户）
    openid = db.Column(db.String(128), default="", nullable=False, index=True)
    copper = db.Column(db.BigInteger, default=0, nullable=False)       # 资产，单位：铜币
    checkin_streak = db.Column(db.Integer, default=0, nullable=False)  # 连续签到天数
    total_checkin = db.Column(db.Integer, default=0, nullable=False)   # 累计签到次数
    last_checkin_date = db.Column(db.Date, nullable=True)
    # 地下城状态（0 = 不在）
    dungeon_layer = db.Column(db.Integer, default=0, nullable=False)
    dungeon_progress = db.Column(db.Float, default=0.0, nullable=False)   # 当前层剩余进度
    dungeon_last_update = db.Column(db.Float, nullable=True)              # 上次结算时间戳
    dungeon_coin_acc = db.Column(db.Float, default=0.0, nullable=False)   # 未满 1 铜币的小数累积
    dungeon_cleared = db.Column(db.Integer, default=0, nullable=False)    # 累计通关层数
    dungeon_coins_earned = db.Column(db.Integer, default=0, nullable=False)  # 地下城累计铜币
    dungeon_run_coins = db.Column(db.Integer, default=0, nullable=False)  # 本次地下城获得铜币
    # 地下城保存进度（退出时保留，再次进入可直达续上；0 = 无保存进度）
    saved_dungeon_layer = db.Column(db.Integer, default=0, nullable=False)
    saved_dungeon_progress = db.Column(db.Float, default=0.0, nullable=False)
    # 地下城稀有矿石（400 层以上）：进入时资格快照 + 上次矿石结算时间戳
    dungeon_ore_eligible = db.Column(db.Integer, default=0, nullable=False)  # 本轮是否有矿石资格
    dungeon_ore_last = db.Column(db.Float, nullable=True)                    # 上次矿石结算时间戳
    # 地下城封顶（v8）：是否已通关 3600 封顶层（晋 T7 依据）；通关后驻留 3600 层持续产出收益
    dungeon_capped = db.Column(db.Integer, default=0, nullable=False)
    # 地下城草药（v8）：进入时层数 ≥150 获得草药周期资格（与矿石周期并列、互不抢占）
    dungeon_herb_eligible = db.Column(db.Integer, default=0, nullable=False)
    dungeon_herb_last = db.Column(db.Float, nullable=True)                    # 上次草药结算时间戳
    # 职业与阶级（装备系统 v3）：profession=职业标识(空=未转职)；tier=当前阶级(0~6，默认0)
    profession = db.Column(db.String(32), default="", nullable=False)
    tier = db.Column(db.Integer, default=0, nullable=False)
    shop_filter = db.Column(db.Integer, default=0, nullable=False)  # 武器库开关：1=只显示当前档(隐藏低等级) 0=全部
    turn_date = db.Column(db.String(10), default="", nullable=False)   # 转转乞讨日期 YYYY-MM-DD
    turn_count = db.Column(db.Integer, default=0, nullable=False)      # 当日已乞讨次数
    # 对战（/挑战）：每日发起次数限制（3 次/天，按日期重置）
    challenge_date = db.Column(db.String(10), default="", nullable=False)   # YYYY-MM-DD
    challenge_count = db.Column(db.Integer, default=0, nullable=False)      # 当日已发起次数
    # 未知指令计数（累计）：超过 3 次后发 beat.jpeg 并停止响应其未知指令
    unknown_count = db.Column(db.Integer, default=0, nullable=False)
    # 最近活跃群（v2.11.68）：/地下城 排名 按群展示的依据；0=尚未归群（多群活跃时漂移为最近说话群）
    group_id = db.Column(db.BigInteger, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class TurnItem(db.Model):
    """转转公共库：玩家捐赠的装备，其他玩家可乞讨（每天 3 次，取走即删除）。"""
    __tablename__ = "turn_item"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    item_id = db.Column(db.String(64), nullable=False, index=True)
    donor_id = db.Column(db.BigInteger, nullable=False)
    donated_at = db.Column(db.DateTime, default=datetime.now)


class UserItem(db.Model):
    """用户装备栏（数据库只记录装备 id，属性在 equipment.json 中维护）。"""
    __tablename__ = "user_item"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    item_id = db.Column(db.String(64), nullable=False)   # 对应 equipment.json 中的 id
    is_new = db.Column(db.Integer, default=0, nullable=False)  # Boss 掉落新装备标记(new!)
    equipped = db.Column(db.Integer, default=0, nullable=False)  # 穿戴中标记(v2.11.70)：进入地下城时按最优组合刷新
    acquired_at = db.Column(db.DateTime, default=datetime.now)


class UserOre(db.Model):
    """用户矿石持有量（数据库只记录矿石 id 与数量，属性在 ores.json 中维护）。"""
    __tablename__ = "user_ore"
    __table_args__ = (db.UniqueConstraint("user_id", "ore_id", name="uq_user_ore"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    ore_id = db.Column(db.String(64), nullable=False)   # 对应 ores.json 中的 id
    count = db.Column(db.Integer, default=0, nullable=False)


# 说明：原 B 站直播监控（LiveMonitor）/ UP 主动态监控（DynamicMonitor）已移除。
# 旧数据库仍可能残留 live_monitor / dynamic_monitor 两张表，不影响运行。


class CheckinRecord(db.Model):
    """签到记录。"""
    __tablename__ = "checkin_record"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True)
    group_id = db.Column(db.BigInteger)
    nickname = db.Column(db.String(128), default="")
    checkin_date = db.Column(db.Date, index=True)
    streak_after = db.Column(db.Integer, default=0)   # 本次签到后的连续天数
    reward = db.Column(db.BigInteger, default=0)      # 本次奖励（铜币）
    created_at = db.Column(db.DateTime, default=datetime.now)


class UserMaterial(db.Model):
    """用户材料持有量（草药/特殊物品/boss 材料；属性在 materials.json 中维护）。"""
    __tablename__ = "user_material"
    __table_args__ = (db.UniqueConstraint("user_id", "material_id", name="uq_user_material"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    material_id = db.Column(db.String(64), nullable=False)   # 对应 materials.json 中的 id
    count = db.Column(db.Integer, default=0, nullable=False)


class UserConsumable(db.Model):
    """用户炼金产物持有量（药水/道具；属性在 consumables.json 中维护）。"""
    __tablename__ = "user_consumable"
    __table_args__ = (db.UniqueConstraint("user_id", "item_id", name="uq_user_consumable"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    item_id = db.Column(db.String(64), nullable=False)   # 对应 consumables.json 中的 id
    count = db.Column(db.Integer, default=0, nullable=False)


class UserBoss(db.Model):
    """玩家 × 命名守关 Boss 的进度：首通日期 / 永久累计成功次数 / 当日挑战计数。"""
    __tablename__ = "user_boss"
    __table_args__ = (db.UniqueConstraint("user_id", "boss_id", name="uq_user_boss"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    boss_id = db.Column(db.String(64), nullable=False)        # 对应 bosses.json 中的 id
    first_clear_date = db.Column(db.String(10), default="", nullable=False)  # YYYY-MM-DD 首通日
    total_wins = db.Column(db.Integer, default=0, nullable=False)   # 永久累计成功次数（跨日不清零）
    fight_date = db.Column(db.String(10), default="", nullable=False)  # 最近挑战日 YYYY-MM-DD
    fight_count = db.Column(db.Integer, default=0, nullable=False)     # 当日已挑战次数
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class UserBuff(db.Model):
    """生效中的 BUFF（药水属性 / 道具掉落增益）。

    effect_json 快照产物效果（含 duration 口径）；过期二选一：
    - 时间型：expire_ts 时间戳到期；
    - 层数型：start_layer 起始层 + remain_layers 剩余可推进层数（dungeon 结算时扣减）。
    """
    __tablename__ = "user_buff"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    item_id = db.Column(db.String(64), nullable=False)      # 对应 consumables.json 中的 id
    effect_json = db.Column(db.Text, default="{}", nullable=False)  # 效果快照
    expire_ts = db.Column(db.Float, nullable=True)          # 时间型到期时间戳
    start_layer = db.Column(db.Integer, nullable=True)      # 层数型起始层
    remain_layers = db.Column(db.Integer, nullable=True)    # 层数型剩余层数
    created_at = db.Column(db.DateTime, default=datetime.now)
