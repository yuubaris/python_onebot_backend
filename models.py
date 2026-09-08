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
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.now)


class User(db.Model):
    """用户（群成员身份）。"""
    __tablename__ = "user"

    user_id = db.Column(db.BigInteger, primary_key=True)
    nickname = db.Column(db.String(128), default="")
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
    # 职业与阶级（装备系统 v3）：profession=职业标识(空=未转职)；tier=当前阶级(0~6，默认0)
    profession = db.Column(db.String(32), default="", nullable=False)
    tier = db.Column(db.Integer, default=0, nullable=False)
    # 对战（/挑战）：每日发起次数限制（3 次/天，按日期重置）
    challenge_date = db.Column(db.String(10), default="", nullable=False)   # YYYY-MM-DD
    challenge_count = db.Column(db.Integer, default=0, nullable=False)      # 当日已发起次数
    # 未知指令计数（累计）：超过 3 次后发 beat.jpeg 并停止响应其未知指令
    unknown_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class UserItem(db.Model):
    """用户装备栏（数据库只记录装备 id，属性在 equipment.json 中维护）。"""
    __tablename__ = "user_item"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    item_id = db.Column(db.String(64), nullable=False)   # 对应 equipment.json 中的 id
    is_new = db.Column(db.Integer, default=0, nullable=False)  # Boss 掉落新装备标记(new!)
    acquired_at = db.Column(db.DateTime, default=datetime.now)


class UserOre(db.Model):
    """用户矿石持有量（数据库只记录矿石 id 与数量，属性在 ores.json 中维护）。"""
    __tablename__ = "user_ore"
    __table_args__ = (db.UniqueConstraint("user_id", "ore_id", name="uq_user_ore"),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, index=True, nullable=False)
    ore_id = db.Column(db.String(64), nullable=False)   # 对应 ores.json 中的 id
    count = db.Column(db.Integer, default=0, nullable=False)


class LiveMonitor(db.Model):
    """B 站直播间监控项（绑定群号，开播/下播推送到群）。

    约束：一个群最多绑定 1 个直播间；一个直播间可绑定多个群（1 对多）。
    """
    __tablename__ = "live_monitor"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    room_id = db.Column(db.Integer, index=True, nullable=False)      # B 站直播间号（短号/真实号皆可）
    remark = db.Column(db.String(128), default="")                   # 备注（主播名等，可选）
    group_id = db.Column(db.BigInteger, index=True, nullable=False)  # 绑定的群号
    enabled = db.Column(db.Boolean, default=True, nullable=False)    # 是否启用监控
    last_status = db.Column(db.Integer, nullable=True)               # 最近一次轮询的 live_status(0/1/2)
    last_check_at = db.Column(db.DateTime, nullable=True)            # 最近一次成功检测时间
    created_at = db.Column(db.DateTime, default=datetime.now)


class DynamicMonitor(db.Model):
    """B 站 UP 主动态监控项（绑定群号，检测到新动态推送到群）。

    一个群可绑定多个 UP 主；一个 UP 主可绑定多个群（多对多）。
    依据开源参考（HarukaBot / bili-monitor / bilibili-notify）采用
    轮询「空间动态列表」接口 + 记录最新动态 id 防重复的方式。
    """
    __tablename__ = "dynamic_monitor"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uid = db.Column(db.Integer, index=True, nullable=False)          # B 站 UP 主 UID
    remark = db.Column(db.String(128), default="")                   # 备注（UP 主名等，可选）
    group_id = db.Column(db.BigInteger, index=True, nullable=False)  # 绑定的群号
    enabled = db.Column(db.Boolean, default=True, nullable=False)    # 是否启用监控
    last_dynamic_id = db.Column(db.String(64), nullable=True)        # 最近已推送的动态 id（防重复）
    last_check_at = db.Column(db.DateTime, nullable=True)            # 最近一次成功检测时间
    created_at = db.Column(db.DateTime, default=datetime.now)


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
