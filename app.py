# -*- coding: utf-8 -*-
"""qq-dungeon-bot —— Flask 入口 / 管理后端（QQ 官方机器人通道）。

启动：  python app.py
管理页： http://127.0.0.1:5000/
"""
import os
import hashlib
import threading
import time

from flask import Flask, render_template, request, jsonify

from models import db, Config, GroupWhitelist, User, CheckinRecord, UserItem, UserOre
from currency import format_currency
from commands import ensure_qq_user, dispatch_command, normalize_command
from logutil import log, get_logs
from qq_official import QQOfficialClient
from ratelimit import RateLimiter
from equipment import load_equipment
from dungeon import layer_total, effective_layer_total, coin_per_5sec, effective_stats, dungeon_speed, owned_items
from repeat import RepeatTracker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "onebot_bot.db")

# 配置默认值（前端可修改）
DEFAULT_CONFIG = {
    # 消息频率限制（按 群×用户 统计指令）
    "rate_limit_enable": "false",  # 是否启用频率限制
    "rate_limit_max": "15",        # 统计窗口内最多消息条数
    "rate_limit_window": "60",     # 统计窗口（秒）
    "rate_limit_cooldown": "60",   # 超出限制后的提示冷却时长（秒），冷却期内静默过滤
    # QQ 官方机器人（开放平台）—— 唯一接入通道
    # 说明：AppSecret 为密钥，仅保存在本地数据库（onebot_bot.db，已被 .gitignore 忽略），
    #       切勿写入代码或提交到仓库；如需更换请在管理页重新填写。
    "qq_official_enable": "true",   # 是否启用官方通道
    "qq_appid": "",                # 官方机器人 AppID
    "qq_appsecret": "",            # 官方机器人 AppSecret（密钥）
    "qq_sandbox": "false",         # 是否使用沙箱环境（预留，当前未实际生效）
    # 命令触发方式：true = 免斜杠（直接发「签到」即可；纯中文命令词才识别，避免误触），
    #              false = 必须带 / 前缀（/签到）
    "plain_command_enable": "true",
}


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH.replace("\\", "/")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False, "timeout": 30}
    }
    db.init_app(app)
    return app


app = create_app()
limiter = RateLimiter()
repeat_tracker = RepeatTracker()  # 复读机：2 个不同用户相同消息后复读一次


# ---------- 配置值类型解析 ----------
def _cfg_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _cfg_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------- 配置读写 ----------
def load_config() -> dict:
    """读取配置。可能被后台机器人线程调用，因此内部自行建立应用上下文。"""
    cfg = dict(DEFAULT_CONFIG)
    with app.app_context():
        rows = db.session.execute(db.select(Config)).scalars()
        for row in rows:
            if row.key in cfg:
                cfg[row.key] = row.value
    return cfg


def save_config(new_cfg: dict):
    for key, value in new_cfg.items():
        if key not in DEFAULT_CONFIG:
            continue
        row = db.session.get(Config, key)
        if row is None:
            db.session.add(Config(key=key, value=str(value)))
        else:
            row.value = str(value)
    db.session.commit()


# ---------- QQ 官方机器人通道 ----------
qqbot = None  # 全局：QQ 官方客户端（配置启用且已填 AppID/AppSecret 时启动）


def _qq_group_int(group_openid):
    """把 32 位 hex 的 group_openid 映射成正整数，复用既有「按群排名」等 int 群号逻辑。

    真实 QQ 群号为 9~10 位十进制，此处取 md5 前 15 位 hex（约 1e18），碰撞概率可忽略。
    """
    return int(hashlib.md5(str(group_openid).encode("utf-8")).hexdigest()[:15], 16)


def send_qq_text(group_openid, text, msg_id=None, msg_seq=1):
    """通过 QQ 官方通道发送群文本消息。"""
    if qqbot is None:
        log("[QQ官方] 客户端未启动，消息未发送")
        return None
    return qqbot.reply_group(group_openid, text, msg_id=msg_id, msg_seq=msg_seq)


def handle_qq_group_message(group_openid, user_openid, text, msg_id, nickname="", mentions=None):
    """QQ 官方群消息入口（由官方客户端线程回调）。"""
    try:
        with app.app_context():
            _handle_qq_group_message_inner(
                group_openid, user_openid, text, msg_id, nickname, mentions
            )
    except Exception as exc:
        log(f"[QQ官方] 消息处理异常: {exc}")


def _qq_at_user_ids(mentions):
    """把官方消息 mentions 中的被 @ 用户（openid）映射为本地 user_id 列表。

    跳过机器人自身与 @全体；被 @ 但从未在群里发过言的用户没有本地账号，
    无法参与对战（与旧通道「对方未注册无法挑战」口径一致）。
    """
    ids = []
    for m in mentions or []:
        if not isinstance(m, dict):
            continue
        if m.get("bot") or m.get("is_you"):
            continue
        oid = m.get("id") or m.get("member_openid")
        if not oid:
            continue
        u = db.session.execute(
            db.select(User).where(User.openid == oid)
        ).scalars().first()
        if u is not None and u.user_id not in ids:
            ids.append(u.user_id)
    return ids


def _handle_qq_group_message_inner(group_openid, user_openid, text, msg_id, nickname, mentions=None):
    if not group_openid or not user_openid:
        return
    gid = _qq_group_int(group_openid)

    # 群白名单：官方通道下「机器人被拉进群」即视为授权，首次收到消息自动登记
    wl = db.session.execute(
        db.select(GroupWhitelist).where(GroupWhitelist.group_openid == group_openid)
    ).scalars().first()
    if wl is None:
        wl = GroupWhitelist(group_id=gid, group_name="(QQ官方群)", group_openid=group_openid, enabled=True)
        db.session.add(wl)
        db.session.commit()
        log(f"[QQ官方] 自动登记群 openid={group_openid}")
    if not wl.enabled:
        return

    user = ensure_qq_user(user_openid, nickname)
    if user.group_id != gid:
        user.group_id = gid
        db.session.commit()

    cfg = load_config()

    # 免斜杠模式：把「签到」这类纯中文命令词归一化为「/签到」
    if _cfg_bool(cfg.get("plain_command_enable", "true")):
        text = normalize_command(text)

    # 非命令消息：复读机（群内两个不同用户发相同消息后复读一次）
    if not text or not text.startswith("/"):
        if repeat_tracker.check(gid, user_openid, text):
            send_qq_text(group_openid, text, msg_id=msg_id)
        return

    # 频率限制
    if _cfg_bool(cfg.get("rate_limit_enable", "false")):
        rl_max = _cfg_int(cfg.get("rate_limit_max"), 15)
        rl_window = _cfg_int(cfg.get("rate_limit_window"), 60)
        rl_cooldown = _cfg_int(cfg.get("rate_limit_cooldown"), 60)
        allowed, rate_reply = limiter.check((gid, user_openid), rl_max, rl_window, rl_cooldown)
        if not allowed:
            if rate_reply:
                send_qq_text(group_openid, rate_reply, msg_id=msg_id)
            return  # 冷却期内静默过滤

    # 被 @ 的用户（openid → 本地 user_id），供「挑战 @对方」等玩法使用
    at_qqs = _qq_at_user_ids(mentions)

    reply = dispatch_command(text, user, gid, at_qqs=at_qqs)
    if not reply:
        return

    if isinstance(reply, dict) and reply.get("type") == "challenge_show":
        msgs = reply.get("msgs") or []
        delay = float(reply.get("delay") or 2)
        for i, m in enumerate(msgs):
            send_qq_text(group_openid, m, msg_id=msg_id, msg_seq=i + 1)
            if i < len(msgs) - 1:
                time.sleep(delay)
    elif isinstance(reply, dict) and reply.get("type") == "image":
        # 兜底：理论上已无图片类命令
        send_qq_text(group_openid, reply.get("text") or "", msg_id=msg_id)
    else:
        send_qq_text(group_openid, reply, msg_id=msg_id)


# ---------- 页面 ----------
@app.route("/")
def index():
    return render_template("index.html")


# ---------- 状态 / 配置 ----------
@app.route("/api/status")
def api_status():
    """接入状态（QQ 官方通道）。"""
    if qqbot is None:
        return jsonify({"enabled": False, "connected": False})
    return jsonify({"enabled": True, **dict(qqbot.status)})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    data = request.get_json(force=True) or {}
    save_config(data)
    limiter.clear()  # 配置变更后重置内存中的频率统计
    return jsonify({"ok": True, "config": load_config()})


# ---------- 群聊白名单 ----------
@app.route("/api/groups", methods=["GET", "POST"])
def api_groups():
    if request.method == "GET":
        rows = db.session.execute(
            db.select(GroupWhitelist).order_by(GroupWhitelist.group_id)
        ).scalars().all()
        return jsonify([{
            "group_id": r.group_id,
            "group_name": r.group_name,
            "enabled": r.enabled,
            "added_at": r.added_at.strftime("%Y-%m-%d %H:%M:%S") if r.added_at else None,
        } for r in rows])

    data = request.get_json(force=True) or {}
    try:
        gid = int(data.get("group_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "群号必须为数字"}), 400
    if gid <= 0:
        return jsonify({"ok": False, "error": "群号非法"}), 400

    row = db.session.get(GroupWhitelist, gid)
    if row is None:
        db.session.add(GroupWhitelist(
            group_id=gid,
            group_name=(data.get("group_name") or "").strip(),
            enabled=True,
        ))
    else:
        row.group_name = (data.get("group_name") or row.group_name).strip()
        row.enabled = True
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/groups/<int:gid>", methods=["DELETE"])
def api_group_delete(gid):
    row = db.session.get(GroupWhitelist, gid)
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/groups/<int:gid>/toggle", methods=["POST"])
def api_group_toggle(gid):
    row = db.session.get(GroupWhitelist, gid)
    if not row:
        return jsonify({"ok": False, "error": "群不存在"}), 404
    row.enabled = not row.enabled
    db.session.commit()
    return jsonify({"ok": True, "enabled": row.enabled})


# ---------- 用户 / 签到记录 ----------
@app.route("/api/users")
def api_users():
    rows = db.session.execute(
        db.select(User).order_by(User.copper.desc())
    ).scalars().all()
    item_counts = {}
    for (uid, cnt) in db.session.execute(
        db.select(UserItem.user_id, db.func.count()).group_by(UserItem.user_id)
    ).all():
        item_counts[uid] = cnt
    return jsonify([{
        "user_id": u.user_id,
        "nickname": u.nickname,
        "copper": u.copper,
        "balance_text": format_currency(u.copper),
        "checkin_streak": u.checkin_streak,
        "total_checkin": u.total_checkin,
        "last_checkin_date": u.last_checkin_date.strftime("%Y-%m-%d")
        if u.last_checkin_date else None,
        "item_count": item_counts.get(u.user_id, 0),
        "dungeon_layer": u.dungeon_layer,
        "dungeon_cleared": u.dungeon_cleared,
        "dungeon_coins": u.dungeon_coins_earned,
        "unknown_count": u.unknown_count or 0,
    } for u in rows])


@app.route("/api/equipment")
def api_equipment():
    """装备列表（来自 equipment.json，便于直接维护）。"""
    items = sorted(load_equipment(), key=lambda x: x.get("price", 0))
    return jsonify([{
        "id": it["id"], "name": it["name"], "type": it.get("type", "other"),
        "line": it.get("line", "any"), "tier": it.get("tier", 0),
        "attack": it.get("attack", 0), "defense": it.get("defense", 0),
        "hp": it.get("hp", 0), "mp": it.get("mp", 0),
        "agility": it.get("agility", 0), "intelligence": it.get("intelligence", 0),
        "price": it.get("price", 0),
    } for it in items])


@app.route("/api/dungeon")
def api_dungeon():
    """地下城玩家状态一览（含退出后保存的进度）。"""
    rows = db.session.execute(
        db.select(User).where(
            db.or_(User.dungeon_layer > 0, User.saved_dungeon_layer > 0)
        )
    ).scalars().all()
    result = []
    for u in rows:
        stats = effective_stats(u, owned_items(u))
        speed = dungeon_speed(stats)
        in_dungeon = u.dungeon_layer > 0
        layer = u.dungeon_layer if in_dungeon else u.saved_dungeon_layer
        progress = (max(u.dungeon_progress, 0.0) if in_dungeon
                    else max(u.saved_dungeon_progress or 0.0, 0.0))
        total = effective_layer_total(layer) if layer and layer > 0 else 0
        pct = (total - progress) / total * 100 if total else 0
        result.append({
            "user_id": u.user_id,
            "nickname": u.nickname,
            "in_dungeon": in_dungeon,
            "profession": u.profession or "",
            "tier": u.tier or 0,
            "layer": layer,
            "progress_pct": round(pct, 1),
            "saved_layer": u.saved_dungeon_layer,
            "saved_progress": round(max(u.saved_dungeon_progress or 0.0, 0.0), 1),
            "speed": round(speed, 2),
            "coin_per_5s": round(coin_per_5sec(max(layer, 1)), 4),
            "cleared": u.dungeon_cleared,
            "run_coins": u.dungeon_run_coins if in_dungeon else 0,
            "total_coins": u.dungeon_coins_earned,
        })
    return jsonify(result)


@app.route("/api/records")
def api_records():
    limit = min(int(request.args.get("limit", 50)), 200)
    rows = db.session.execute(
        db.select(CheckinRecord).order_by(CheckinRecord.id.desc()).limit(limit)
    ).scalars().all()
    return jsonify([{
        "id": r.id,
        "user_id": r.user_id,
        "nickname": r.nickname,
        "group_id": r.group_id,
        "checkin_date": r.checkin_date.strftime("%Y-%m-%d") if r.checkin_date else None,
        "streak_after": r.streak_after,
        "reward": r.reward,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
    } for r in rows])


# ---------- 日志 / 机器人控制 ----------
@app.route("/api/logs")
def api_logs():
    limit = min(int(request.args.get("limit", 100)), 300)
    return jsonify({"logs": get_logs(limit)})


@app.route("/api/bot/restart", methods=["POST"])
def api_bot_restart():
    if qqbot is not None:
        qqbot.stop()
        qqbot.start()
    return jsonify({"ok": True})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    if qqbot is not None:
        qqbot.stop()
    return jsonify({"ok": True})


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    if qqbot is not None:
        qqbot.start()
    return jsonify({"ok": True})


@app.route("/api/qq/status")
def api_qq_status():
    """QQ 官方通道状态（未启用时 enabled=false）。"""
    if qqbot is None:
        return jsonify({"enabled": False})
    return jsonify({"enabled": True, "status": dict(qqbot.status)})


# ---------- 启动 ----------
def _migrate_schema():
    """兼容旧数据库：为 user 表补充「地下城保存进度」字段（无则 ALTER TABLE 添加）。

    已有运行库（如用户副本的 onebot_bot.db）没有新列时，直接在线加列，避免重建库丢数据。
    """
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user)")}
        if "saved_dungeon_layer" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN saved_dungeon_layer INTEGER DEFAULT 0")
        if "saved_dungeon_progress" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN saved_dungeon_progress REAL DEFAULT 0.0")
        if "challenge_date" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN challenge_date VARCHAR(10) DEFAULT ''")
        if "challenge_count" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN challenge_count INTEGER DEFAULT 0")
        if "unknown_count" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN unknown_count INTEGER DEFAULT 0")
        if "dungeon_ore_eligible" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN dungeon_ore_eligible INTEGER DEFAULT 0")
        # 装备系统 v3：职业 + 阶级（存量库补列，默认空/0）
        if "profession" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN profession VARCHAR(32) DEFAULT ''")
        if "tier" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN tier INTEGER DEFAULT 0")
        if "shop_filter" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN shop_filter INTEGER DEFAULT 0")
        if "turn_date" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN turn_date VARCHAR(10) DEFAULT ''")
        if "turn_count" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN turn_count INTEGER DEFAULT 0")
        # 排名按群（v2.11.68）：最近活跃群（0=未归群，/地下城 排名 仅统计同群用户）
        if "group_id" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN group_id BIGINT DEFAULT 0")
        # QQ 官方机器人：openid 标识官方通道用户（官方不返回真实 QQ 号）
        if "openid" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN openid VARCHAR(128) DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_user_openid ON user (openid)")
        conn.commit()
        # group_whitelist 表：QQ 官方群 openid（官方通道用于匹配白名单）
        wl_cols = {r[1] for r in conn.execute("PRAGMA table_info(group_whitelist)")}
        if wl_cols and "group_openid" not in wl_cols:
            conn.execute("ALTER TABLE group_whitelist ADD COLUMN group_openid VARCHAR(128) DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_group_whitelist_group_openid ON group_whitelist (group_openid)")
            conn.commit()
        # user_item 表：Boss 掉落 new 标记
        it_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_item)")}
        if "is_new" not in it_cols:
            conn.execute("ALTER TABLE user_item ADD COLUMN is_new INTEGER DEFAULT 0")
        # 穿戴中标记（v2.11.70）：进入地下城按最优组合刷新
        if "equipped" not in it_cols:
            conn.execute("ALTER TABLE user_item ADD COLUMN equipped INTEGER DEFAULT 0")
        conn.commit()
        if "dungeon_ore_last" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN dungeon_ore_last REAL")
        conn.commit()
        # 地下城封顶 + 草药（v8）：存量库补列
        if "dungeon_capped" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN dungeon_capped INTEGER DEFAULT 0")
        if "dungeon_herb_eligible" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN dungeon_herb_eligible INTEGER DEFAULT 0")
        if "dungeon_herb_last" not in cols:
            conn.execute("ALTER TABLE user ADD COLUMN dungeon_herb_last REAL")
        conn.commit()
        conn.close()
    except Exception as exc:
        log(f"数据库迁移失败: {exc}")


with app.app_context():
    db.create_all()
    _migrate_schema()
    # QQ 官方机器人通道（唯一接入通道）
    _startup_cfg = load_config()
    if not _cfg_bool(_startup_cfg.get("qq_official_enable", "true")):
        log("[QQ官方] 接入已在配置中关闭（qq_official_enable=false）")
    elif not (_startup_cfg.get("qq_appid") and _startup_cfg.get("qq_appsecret")):
        log("[QQ官方] 未配置 AppID / AppSecret，未启动机器人（请在管理页填写后重启）")
    else:
        qqbot = QQOfficialClient(
            get_config=load_config,
            on_group_message=handle_qq_group_message,
            log=log,
        )
        qqbot.start()
        log("[QQ官方] 接入已启用")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
