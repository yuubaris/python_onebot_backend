# -*- coding: utf-8 -*-
"""B 站直播间开播/下播监控模块。

方案：轮询 B 站公开 API（无需登录 / Token）。
- 批量接口 getRoomBaseInfo 一次查询所有被监控房间（监控规模小）
- 每 60 秒轮询一次，仅 live_status == 1 视为“直播中”（轮播 2 不算开播）
- 状态翻转：0/2 → 1 为开播，推送「标题 / 主播 / 开播时间 / 链接」；
           1 → 0/2 为下播，推送「标题 / 主播」
- 首次监控只记录状态，不通知；网络/接口异常时跳过本轮、不更新状态（避免误报）
"""
import json
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime

from models import db, LiveMonitor

POLL_INTERVAL = 60  # 轮询间隔（秒）

ROOM_BASE_URL = ("https://api.live.bilibili.com/xlive/web-room/v1/index/"
                 "getRoomBaseInfo?req_biz=web_room_componet&room_ids=")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

STATUS_TEXT = {0: "未开播", 1: "直播中", 2: "轮播中", None: "待检测"}

# 直播间监控专用环形日志（供管理页独立展示）
_livemon_logs = deque(maxlen=200)


def log(message: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    _livemon_logs.append(line)
    print(line, flush=True)


def get_logs(limit=100):
    return list(_livemon_logs)[-limit:]


def fetch_room_infos(room_ids):
    """批量查询房间信息。

    返回 {room_id(int): data}；接口异常 / 返回异常时返回 None（表示本轮不可用）。
    """
    ids = [int(r) for r in room_ids if int(r) > 0]
    if not ids:
        return {}
    url = ROOM_BASE_URL + ",".join(str(i) for i in ids)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if body.get("code") != 0:
        return None
    data = body.get("data") or {}
    by_rooms = data.get("by_room_ids") or {}
    return {int(k): v for k, v in by_rooms.items()}


def _fmt_time(ts):
    """unix 秒时间戳 → 'YYYY-MM-DD HH:MM:SS'；空返回空串。"""
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def _room_name(data, monitor):
    return (data.get("uname") or monitor.remark or str(monitor.room_id)).strip()


def _room_title(data, monitor):
    return (data.get("title") or monitor.remark or f"房间 {monitor.room_id}").strip()


def _match_info(infos, room_id):
    """在批量结果中按 请求号 / 返回的 room_id 字段匹配。"""
    rid = int(room_id)
    if rid in infos:
        return infos[rid]
    for v in infos.values():
        if int(v.get("room_id") or 0) == rid:
            return v
    return None


def run_once(sender):
    """执行一次轮询检测。sender(group_id, text) 用于发送群消息。

    调用方需处于应用上下文内；网络异常时跳过本轮，不更新状态。
    """
    monitors = db.session.execute(
        db.select(LiveMonitor).where(LiveMonitor.enabled.is_(True))
    ).scalars().all()
    if not monitors:
        log("轮询：暂无启用的直播监控")
        return

    infos = fetch_room_infos({m.room_id for m in monitors})
    if infos is None:
        log("批量查询 B 站接口异常，本轮跳过")
        return  # 接口异常：本轮跳过

    now = datetime.now()
    roundup = {}
    processed = 0
    for m in monitors:
        data = _match_info(infos, m.room_id)
        if data is None:
            log(f"房间 {m.room_id} 未返回数据（可能不存在/已下架）")
            continue  # 房间不存在/下架，跳过
        cur = int(data.get("live_status", 0))
        prev = m.last_status
        processed += 1
        roundup[cur] = roundup.get(cur, 0) + 1
        if prev is None:
            # 首次监控：只记录状态，不通知
            m.last_status = cur
            m.last_check_at = now
            log(f"首次监控 房间 {m.room_id} -> 群 {m.group_id}，状态「{STATUS_TEXT.get(cur, cur)}」，仅记录不通知")
            continue
        if prev != 1 and cur == 1:
            title = _room_title(data, m)
            uname = _room_name(data, m)
            # 开播时间优先取 B 站接口返回的 live_time（已格式化字符串）；
            # 缺失时兜底为最后一次 fetch 时间
            live_time = (data.get("live_time") or "").strip()
            if not live_time:
                live_time = now.strftime("%Y-%m-%d %H:%M:%S")
            text = (f"🟢 开播提醒\n直播间：{title}\n主播：{uname}\n"
                    f"开播时间：{live_time}\n"
                    f"链接：https://live.bilibili.com/{m.room_id}")
            # 开播推送附带直播间封面图（由 sender 下载到本地后以图片消息发送）
            cover = (data.get("cover") or "").strip()
            sender(m.group_id, text, [cover] if cover else None)
            log(f"房间 {m.room_id} 开播（{STATUS_TEXT.get(prev, prev)}→直播中），已通知群 {m.group_id}")
        elif prev == 1 and cur != 1:
            title = _room_title(data, m)
            uname = _room_name(data, m)
            text = f"🔴 下播通知\n直播间：{title}\n主播：{uname}"
            sender(m.group_id, text)
            log(f"房间 {m.room_id} 下播（直播中→{STATUS_TEXT.get(cur, cur)}），已通知群 {m.group_id}")
        m.last_status = cur
        m.last_check_at = now
    db.session.commit()

    # 每轮轮询汇总日志（正常轮询也产生记录，便于确认监控在运行）
    parts = []
    for st in (1, 2, 0):
        if roundup.get(st):
            parts.append(f"{STATUS_TEXT[st]} {roundup[st]}")
    log(f"轮询完成：{processed} 个房间（{'，'.join(parts) if parts else '无有效状态'}）")


class LiveMonitorThread:
    """后台轮询线程（每 POLL_INTERVAL 秒一次，daemon）。"""

    def __init__(self, app, sender):
        self.app = app
        self.sender = sender
        self._thread = None
        self._running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="livemon")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive() \
                and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self):
        while self._running:
            try:
                with self.app.app_context():
                    run_once(self.sender)
            except Exception as exc:
                try:
                    from bot import log
                    log(f"直播间监控异常: {exc}")
                except Exception:
                    pass
            time.sleep(POLL_INTERVAL)
