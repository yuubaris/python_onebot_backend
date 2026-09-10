# -*- coding: utf-8 -*-
"""OneBot 11 签到机器人 —— Flask 入口 / 管理后端。

启动：  python app.py
管理页： http://127.0.0.1:5000/
"""
import os
import threading
import time
import urllib.request

from flask import Flask, render_template, request, jsonify

from models import db, Config, GroupWhitelist, User, CheckinRecord, UserItem, LiveMonitor, DynamicMonitor, UserOre
from currency import format_currency
from commands import ensure_user, dispatch_command
from bot import OneBotClient, log, get_logs
from ratelimit import RateLimiter
from equipment import load_equipment
from dungeon import layer_total, effective_layer_total, coin_per_5sec, effective_stats, dungeon_speed, owned_items
from livemon import LiveMonitorThread, run_once, STATUS_TEXT, log as livemon_log, get_logs as livemon_logs
from dynamon import (
    DynamicMonitorThread, run_once as dyn_run_once,
    log as dynamon_log, get_logs as dynamon_logs,
)
from repeat import RepeatTracker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "onebot_bot.db")

# 连接配置默认值（前端可修改）
DEFAULT_CONFIG = {
    "bot_host": "127.0.0.1",       # OneBot 服务端主机
    "bot_port": "6700",            # OneBot 服务端端口
    "bot_path": "/",               # 连接路径（"/" 为 Universal）
    "bot_token": "",               # 授权 Token（可为空）
    "bot_message_format": "array", # 消息格式：数组
    "bot_role": "universal",       # 连接角色：Universal
    # 消息频率限制（按 群×用户 统计 "/" 指令）
    "rate_limit_enable": "false",  # 是否启用频率限制
    "rate_limit_max": "15",        # 统计窗口内最多消息条数
    "rate_limit_window": "60",     # 统计窗口（秒）
    "rate_limit_cooldown": "60",   # 超出限制后的提示冷却时长（秒），冷却期内静默过滤
    # QQ 官方机器人（开放平台）接入
    # 说明：AppSecret 为密钥，仅保存在本地数据库（onebot_bot.db，已被 .gitignore 忽略），
    #       切勿写入代码或提交到仓库；如需更换请在管理页重新填写。
    "qq_official_enable": "false",  # 是否启用 QQ 官方机器人接入（true 时与 OneBot 可并存）
    "qq_appid": "",                 # 官方机器人 AppID
    "qq_appsecret": "",             # 官方机器人 AppSecret（密钥）
    "qq_sandbox": "false",          # 是否使用沙箱环境
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
bot = None
limiter = RateLimiter()
monitor = None
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


# ---------- 消息解析 ----------
def message_to_text(message):
    """将 OneBot 消息（数组格式 / 字符串）转换为纯文本。"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)
    return "" if message is None else str(message)


def extract_at_qq(message, exclude=None):
    """从 OneBot 数组消息中提取所有被 @ 的 QQ 号（排除 @全体 与 exclude 指定的号）。"""
    if not isinstance(message, list):
        return []
    result = []
    for seg in message:
        if isinstance(seg, dict) and seg.get("type") == "at":
            qq = (seg.get("data") or {}).get("qq")
            if qq and qq != "all" and qq != exclude:
                try:
                    result.append(int(qq))
                except (TypeError, ValueError):
                    pass
    return result


# ---------- 事件处理 ----------
def handle_event(data):
    try:
        with app.app_context():
            _handle_event_inner(data)
    except Exception as exc:
        log(f"事件处理异常: {exc}")


def _handle_event_inner(data):
    post_type = data.get("post_type")

    # 元事件：生命周期/心跳
    if post_type == "meta_event":
        if data.get("meta_event_type") == "lifecycle":
            log(f"生命周期事件: {data.get('sub_type')}")
        return

    # 仅处理群聊消息
    if post_type != "message" or data.get("message_type") != "group":
        return

    group_id = data.get("group_id")
    user_id = data.get("user_id")
    if group_id is None or user_id is None:
        return

    # 群聊白名单校验
    wl = db.session.execute(
        db.select(GroupWhitelist).where(
            GroupWhitelist.group_id == group_id,
            GroupWhitelist.enabled.is_(True),
        )
    ).scalars().first()
    if wl is None:
        return

    # 识别群聊用户身份：所有群消息都记录发送者群名片/昵称，
    # 供 /踢 等命令显示被@用户的群昵称（优先取群名片，其次昵称）
    sender = data.get("sender") or {}
    nickname = sender.get("card") or sender.get("nickname") or str(user_id)
    user = ensure_user(user_id, nickname)

    # 记录最近活跃群（/地下城 排名 等按群展示的依据；仅群变化时写入，避免频繁 commit）
    if user.group_id != group_id:
        user.group_id = group_id
        db.session.commit()

    # 提取消息文本
    text = message_to_text(data.get("message"))

    # 非命令消息：复读机检测（2 个不同用户发相同消息后复读一次）
    if not text or not text.lstrip().startswith("/"):
        # 排除机器人自身消息（避免把机器人复读的消息再次计入）
        if int(user_id) != int(data.get("self_id") or 0):
            if repeat_tracker.check(group_id, user_id, text):
                send_group_text(group_id, text)
        return

    # 提取消息中被 @ 的 QQ（排除 @全体 与机器人自身）
    at_qqs = extract_at_qq(data.get("message"), exclude=data.get("self_id"))

    # 消息频率限制（按 群×用户 统计；可配置）
    cfg = load_config()
    if _cfg_bool(cfg.get("rate_limit_enable", "false")):
        rl_max = _cfg_int(cfg.get("rate_limit_max"), 15)
        rl_window = _cfg_int(cfg.get("rate_limit_window"), 60)
        rl_cooldown = _cfg_int(cfg.get("rate_limit_cooldown"), 60)
        allowed, rate_reply = limiter.check(
            (group_id, user_id), rl_max, rl_window, rl_cooldown
        )
        if not allowed:
            if rate_reply:
                send_group_text(group_id, rate_reply)
            return  # 冷却期内静默过滤，不处理、不提示

    reply = dispatch_command(text, user, group_id, at_qqs=at_qqs)
    if reply:
        if isinstance(reply, dict) and reply.get("type") == "image":
            send_group_image(group_id, reply["file"], reply.get("text"))
            # 异步刷新被@用户的群名片到本地（便于后续 /踢 显示群昵称）
            if reply.get("target") and reply.get("group_id"):
                refresh_member_card_async(reply["group_id"], reply["target"])
        elif isinstance(reply, dict) and reply.get("type") == "challenge_show":
            _send_challenge_show(reply)
        else:
            send_group_text(group_id, reply)


def _send_challenge_show(reply):
    """对战演出：后台线程逐条发送 3 条消息，每条间隔 delay 秒（不阻塞事件处理）。"""
    def worker():
        msgs = reply.get("msgs") or []
        delay = float(reply.get("delay") or 2)
        for i, m in enumerate(msgs):
            send_group_text(reply["group_id"], m)
            if i < len(msgs) - 1:
                time.sleep(delay)
    threading.Thread(target=worker, daemon=True).start()


def send_group_text(group_id, text):
    """以数组格式发送群消息。"""
    message = [{"type": "text", "data": {"text": text}}]
    bot.send_action("send_group_msg", {"group_id": group_id, "message": message})


def send_group_image(group_id, file_path, text=None):
    """以数组格式发送群消息：可选文本 + 本地图片（file:/// 绝对路径）。"""
    segments = []
    if text:
        segments.append({"type": "text", "data": {"text": text}})
    file_uri = "file:///" + os.path.abspath(file_path).replace("\\", "/")
    segments.append({"type": "image", "data": {"file": file_uri}})
    bot.send_action("send_group_msg", {"group_id": group_id, "message": segments})


def download_dynamic_pics(urls, out_dir=None):
    """下载动态配图到本地 tmp，返回成功下载的本地路径列表（单张失败跳过，不影响推送）。

    B 站图片带 Referer + UA 直下，避免把网络 URL 直接交给协议端（更稳定）。
    """
    if out_dir is None:
        out_dir = os.path.join(BASE_DIR, "tmp")
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, url in enumerate((urls or [])[:3]):
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
                "Referer": "https://www.bilibili.com/",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if not data:
                continue
            ext = ".jpg"
            if url.lower().endswith(".png"):
                ext = ".png"
            elif url.lower().endswith(".gif"):
                ext = ".gif"
            p = os.path.join(out_dir, f"dyn_{int(time.time() * 1000)}_{i}{ext}")
            with open(p, "wb") as fh:
                fh.write(data)
            paths.append(p)
        except Exception:
            continue
    return paths


def send_group_dynamic(group_id, text, pics=None):
    """动态监控推送：文本 + 最多 3 张本地图片（下载到 tmp 后以 file:/// 发送）。"""
    segments = [{"type": "text", "data": {"text": text}}]
    for p in download_dynamic_pics(pics):
        file_uri = "file:///" + os.path.abspath(p).replace("\\", "/")
        segments.append({"type": "image", "data": {"file": file_uri}})
    bot.send_action("send_group_msg", {"group_id": group_id, "message": segments})


def refresh_member_card_async(group_id, user_id):
    """异步查询群成员名片并写入本地库（fire-and-forget，不阻塞命令处理）。

    由于 OneBot WS 为单线程回调，命令处理中无法同步等待 API 结果；
    这里只发送请求，结果在回调（bot 线程空闲时）中写入数据库。
    """
    def cb(data):
        try:
            info = data.get("data") or {}
            card = info.get("card") or info.get("nickname")
            if not card:
                return
            with app.app_context():
                u = db.session.get(User, user_id)
                if u and u.nickname != card:
                    u.nickname = card
                    db.session.commit()
        except Exception:
            pass

    if bot is None:
        return
    try:
        bot.send_action("get_group_member_info",
                        {"group_id": group_id, "user_id": user_id}, callback=cb)
    except Exception:
        pass


# ---------- 页面 ----------
@app.route("/")
def index():
    return render_template("index.html")


# ---------- 状态 / 配置 ----------
@app.route("/api/status")
def api_status():
    return jsonify({
        "connected": bot.status.get("connected", False),
        "url": bot.status.get("url", ""),
        "connected_at": bot.status.get("connected_at"),
        "last_event_at": bot.status.get("last_event_at"),
        "last_error": bot.status.get("last_error", ""),
    })


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


# ---------- 直播间监控 ----------
@app.route("/api/livemon")
def api_livemon():
    rows = db.session.execute(
        db.select(LiveMonitor).order_by(LiveMonitor.id)
    ).scalars().all()
    return jsonify([{
        "id": r.id,
        "room_id": r.room_id,
        "remark": r.remark,
        "group_id": r.group_id,
        "enabled": r.enabled,
        "last_status": r.last_status,
        "status_text": STATUS_TEXT.get(r.last_status, "未知"),
        "last_check_at": r.last_check_at.strftime("%Y-%m-%d %H:%M:%S")
        if r.last_check_at else None,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
    } for r in rows])


@app.route("/api/livemon/logs")
def api_livemon_logs():
    limit = min(int(request.args.get("limit", 100)), 200)
    return jsonify({"logs": livemon_logs(limit)})


@app.route("/api/livemon", methods=["POST"])
def api_livemon_add():
    data = request.get_json(force=True) or {}
    try:
        group_id = int(data.get("group_id"))
        room_id = int(data.get("room_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "群号与房间号必须为数字"}), 400
    if group_id <= 0 or room_id <= 0:
        return jsonify({"ok": False, "error": "群号与房间号非法"}), 400
    # 约束：一个群最多绑定 1 个直播间
    exists = db.session.execute(
        db.select(LiveMonitor).where(LiveMonitor.group_id == group_id)
    ).scalars().first()
    if exists:
        return jsonify({
            "ok": False,
            "error": f"群 {group_id} 已绑定直播间 {exists.room_id}，请先删除后再绑定",
        }), 400
    db.session.add(LiveMonitor(
        room_id=room_id,
        remark=(data.get("remark") or "").strip(),
        group_id=group_id,
        enabled=True,
    ))
    db.session.commit()
    livemon_log(f"新增监控: 房间 {room_id} -> 群 {group_id}")
    return jsonify({"ok": True})


@app.route("/api/livemon/<int:mid>", methods=["DELETE"])
def api_livemon_delete(mid):
    row = db.session.get(LiveMonitor, mid)
    if row:
        db.session.delete(row)
        db.session.commit()
        livemon_log(f"删除监控 id={mid}（房间 {row.room_id} -> 群 {row.group_id}）")
    return jsonify({"ok": True})


@app.route("/api/livemon/<int:mid>/toggle", methods=["POST"])
def api_livemon_toggle(mid):
    row = db.session.get(LiveMonitor, mid)
    if not row:
        return jsonify({"ok": False, "error": "监控项不存在"}), 404
    row.enabled = not row.enabled
    db.session.commit()
    livemon_log(f"监控 id={mid}（房间 {row.room_id}）已{'启用' if row.enabled else '停用'}")
    return jsonify({"ok": True, "enabled": row.enabled})


@app.route("/api/livemon/check", methods=["POST"])
def api_livemon_check():
    """立即执行一次检测（手动触发，按正常逻辑仅在状态翻转时通知）。"""
    livemon_log("手动触发立即检测")
    run_once(send_group_dynamic)
    return jsonify({"ok": True})


# ---------- UP 主动态监控 ----------
@app.route("/api/dynamon")
def api_dynamon():
    rows = db.session.execute(
        db.select(DynamicMonitor).order_by(DynamicMonitor.id)
    ).scalars().all()
    return jsonify([{
        "id": r.id,
        "uid": r.uid,
        "remark": r.remark,
        "group_id": r.group_id,
        "enabled": r.enabled,
        "last_dynamic_id": r.last_dynamic_id,
        "last_check_at": r.last_check_at.strftime("%Y-%m-%d %H:%M:%S")
        if r.last_check_at else None,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
    } for r in rows])


@app.route("/api/dynamon/logs")
def api_dynamon_logs():
    limit = min(int(request.args.get("limit", 100)), 200)
    return jsonify({"logs": dynamon_logs(limit)})


@app.route("/api/dynamon", methods=["POST"])
def api_dynamon_add():
    data = request.get_json(force=True) or {}
    try:
        group_id = int(data.get("group_id"))
        uid = int(data.get("uid"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "群号与 UP 主 UID 必须为数字"}), 400
    if group_id <= 0 or uid <= 0:
        return jsonify({"ok": False, "error": "群号与 UP 主 UID 非法"}), 400
    db.session.add(DynamicMonitor(
        uid=uid,
        remark=(data.get("remark") or "").strip(),
        group_id=group_id,
        enabled=True,
    ))
    db.session.commit()
    dynamon_log(f"新增动态监控: UP 主 {uid} -> 群 {group_id}")
    return jsonify({"ok": True})


@app.route("/api/dynamon/<int:mid>", methods=["DELETE"])
def api_dynamon_delete(mid):
    row = db.session.get(DynamicMonitor, mid)
    if row:
        db.session.delete(row)
        db.session.commit()
        dynamon_log(f"删除动态监控 id={mid}（UP 主 {row.uid} -> 群 {row.group_id}）")
    return jsonify({"ok": True})


@app.route("/api/dynamon/<int:mid>/toggle", methods=["POST"])
def api_dynamon_toggle(mid):
    row = db.session.get(DynamicMonitor, mid)
    if not row:
        return jsonify({"ok": False, "error": "监控项不存在"}), 404
    row.enabled = not row.enabled
    db.session.commit()
    dynamon_log(f"动态监控 id={mid}（UP 主 {row.uid}）已{'启用' if row.enabled else '停用'}")
    return jsonify({"ok": True, "enabled": row.enabled})


@app.route("/api/dynamon/check", methods=["POST"])
def api_dynamon_check():
    """立即执行一次检测（手动触发，按正常逻辑仅在出现新动态时通知）。"""
    dynamon_log("手动触发立即检测")
    dyn_run_once(send_group_text)
    return jsonify({"ok": True})


# ---------- 日志 / 机器人控制 ----------
@app.route("/api/logs")
def api_logs():
    limit = min(int(request.args.get("limit", 100)), 300)
    return jsonify({"logs": get_logs(limit)})


@app.route("/api/bot/restart", methods=["POST"])
def api_bot_restart():
    bot.restart()
    return jsonify({"ok": True})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    bot.stop()
    return jsonify({"ok": True})


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    bot.start()
    return jsonify({"ok": True})


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
    bot = OneBotClient(get_config=load_config, on_event=handle_event)
    bot.start()
    monitor = LiveMonitorThread(app, send_group_dynamic)
    monitor.start()
    dmonitor = DynamicMonitorThread(app, send_group_dynamic)
    dmonitor.start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
