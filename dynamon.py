# -*- coding: utf-8 -*-
"""B 站 UP 主动态监控模块。

方案：轮询 B 站公开 API（无需登录 / Token）。
- 接口：GET /x/polymer/web-dynamic/v1/feed/space?host_mid={uid}
        （新版空间动态列表，返回该 UP 主按时间倒序的动态）
- 每 POLL_INTERVAL 秒轮询一次，取最新动态 id（id_str），与上次记录比对
- 检测到新动态（id 变化且未推送过）→ 推送「UP 名 / 类型 / 内容 / 时间 / 链接」
- 首次监控只记录最新动态 id、不通知（避免误报历史动态）
- 网络 / 接口异常时跳过本轮、不更新状态（避免误报）

设计参考开源实现：HarukaBot / bili-monitor / bilibili-notify 的动态订阅轮询思路。
"""
import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import cdp

from models import db, DynamicMonitor

POLL_INTERVAL = 200  # 队列轮询：每轮询完一个监控项后等待的间隔（秒）

DYNAMIC_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# B 站动态接口有安全风控：
#   - 无 Cookie 时返回 HTTP 412；Python urllib/requests 的 TLS 指纹易被识别为脚本，
#     即使带 Cookie 也持续返回 code=-352（风控码）。
#   - 实测系统自带 curl.exe（Windows 10+）的 TLS 指纹更接近浏览器，带 Cookie + Chrome UA
#     + Referer 即可稳定返回 code:0，因此本模块统一改用 curl.exe 子进程发起请求。
#   - Cookie 通过 curl 的 -c/-b 持久化到模块同目录的 netscape 格式 cookie 文件。
_CURL = shutil.which("curl.exe") or shutil.which("curl") or "curl"
_COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bili_cookies.txt")
_buvid_ready = False
# 风控冷却：命中 B 站风控后，这段时间内不再请求动态接口（避免被拉黑）
_risk_cooldown_until = 0.0
RISK_COOLDOWN_SECONDS = 300

TYPE_NAMES = {
    "DYNAMIC_TYPE_FORWARD": "转发",
    "DYNAMIC_TYPE_AV": "投稿视频",
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏",
    "DYNAMIC_TYPE_MUSIC": "音频",
    "DYNAMIC_TYPE_LIVE": "直播",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播",
}

# 动态监控专用环形日志（供管理页独立展示）
_dynamon_logs = deque(maxlen=200)


def log(message: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    _dynamon_logs.append(line)
    print(line, flush=True)


def get_logs(limit=100):
    return list(_dynamon_logs)[-limit:]


def _curl_get(url, referer):
    """用 curl.exe 发起 GET（模拟浏览器 TLS 指纹 + Cookie）。

    返回 (returncode, body_text)；失败时 (None, None)。
    """
    try:
        proc = subprocess.run(
            [_CURL, "-s", "--max-time", "12",
             "-A", BROWSER_UA,
             "-e", referer,
             "-b", _COOKIE_FILE, "-c", _COOKIE_FILE,
             url],
            capture_output=True, timeout=16)
    except Exception:
        return None, None
    return proc.returncode, proc.stdout.decode("utf-8", "ignore")


def _request_get(url, referer):
    """请求层：优先 CDP（真实 Edge 浏览器，最稳），失败/异常降级 curl.exe。"""
    try:
        rc, body = cdp.cdp_get_text(url, referer)
        if rc == 0 and body:
            return 0, body
    except Exception:
        pass
    return _curl_get(url, referer)


def _ensure_cookie():
    """懒加载：优先使用已注入的登录态 Cookie 文件（含 SESSDATA 即视为就绪）；
    否则用 curl 访问 B 站主页，把 buvid3/b_nut 等 Cookie 持久化到 cookie 文件。"""
    global _buvid_ready
    if _buvid_ready:
        return True
    try:
        with open(_COOKIE_FILE, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
        if "SESSDATA" in content:
            _buvid_ready = True
            log("检测到已注入登录态 Cookie（SESSDATA），跳过浏览器指纹种 Cookie")
            return True
    except Exception:
        pass
    _curl_get("https://www.bilibili.com/", "https://www.bilibili.com/")
    _curl_get("https://api.bilibili.com/x/frontend/finger/spi",
              "https://www.bilibili.com/")
    _buvid_ready = True
    return True


def fetch_latest(uid):
    """拉取指定 UP 主最新动态列表（按时间倒序）。

    返回 items 列表；接口异常 / 返回码非 0 / 触发风控时返回 None（本轮跳过）。
    """
    global _risk_cooldown_until
    if time.time() < _risk_cooldown_until:
        return None  # 风控冷却期，静默跳过本轮
    _ensure_cookie()
    url = (f"{DYNAMIC_URL}?host_mid={uid}&offset=&timezone_offset=-480&platform=web"
           "&features=itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
           "decorationCard,onlyfansAssetsV2,forwardListHidden,ugcDelete")
    rc, body = _request_get(url, f"https://space.bilibili.com/{uid}/dynamic")
    if body is None:
        return None
    try:
        data = json.loads(body)
    except Exception:
        # 非 JSON（通常是 412 风控 HTML 页）
        _risk_cooldown_until = time.time() + RISK_COOLDOWN_SECONDS
        log(f"UP 主 {uid} 请求被 B 站风控拦截（非 JSON 响应），进入 {RISK_COOLDOWN_SECONDS}s 冷却")
        return None
    if data.get("code") != 0:
        if data.get("code") in (-352, -412, -403):
            _risk_cooldown_until = time.time() + RISK_COOLDOWN_SECONDS
            log(f"UP 主 {uid} 触发 B 站风控（code={data.get('code')}），进入 {RISK_COOLDOWN_SECONDS}s 冷却")
        else:
            log(f"UP 主 {uid} 动态接口 code={data.get('code')} msg={data.get('message')}，本轮跳过")
        return None
    data = data.get("data") or {}
    return data.get("items") or []


def _is_live_dynamic(item):
    """判断是否为『直播了』动态（UP 主开播时 B 站自动发的直播动态）。

    这类动态不播报（直播间监控已单独处理开播提醒）。按类型名包含 LIVE 判断。
    """
    try:
        return "LIVE" in str(item.get("type") or "").upper()
    except Exception:
        return False


def parse_item(item):
    """从一条动态解析出可展示字段，返回 dict；解析不出时返回 None。"""
    if not item:
        return None
    id_str = str(item.get("id_str") or "").strip()
    if not id_str:
        return None
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    dyn = modules.get("module_dynamic") or {}

    # 文字内容
    desc = ""
    try:
        desc = ((dyn.get("desc") or {}).get("text") or "").strip()
    except Exception:
        desc = ""
    # 主体（视频 / 专栏 / 图文 / 音频等）
    title = ""
    opus_summary = ""
    pics = []
    major = dyn.get("major") or {}
    if major:
        # 图文动态：文字在 major.opus.summary.text，图片在 major.opus.pics[].url
        opus = major.get("opus") or {}
        try:
            opus_summary = ((opus.get("summary") or {}).get("text") or "").strip()
        except Exception:
            opus_summary = ""
        try:
            pics = []
            for p in (opus.get("pics") or []):
                u = (p.get("url") or "").strip()
                if u:
                    if u.startswith("http://"):
                        u = "https://" + u[len("http://"):]
                    pics.append(u)
        except Exception:
            pics = []
        title = (major.get("title") or "").strip()
        if not title:
            for key in ("archive", "article", "ugc_season", "courses", "pgc", "music", "common"):
                sub = major.get(key) or {}
                if sub.get("title"):
                    title = str(sub["title"]).strip()
                    break
        # 非图文动态（视频/专栏等）以封面兜底作为配图
        if not pics:
            for key in ("archive", "article"):
                cover = (major.get(key) or {}).get("cover")
                if cover:
                    if cover.startswith("http://"):
                        cover = "https://" + cover[len("http://"):]
                    pics = [cover]
                    break
    content = (desc or opus_summary or title or "（无文字内容）").strip()
    if len(content) > 200:
        content = content[:200] + "…"
    return {
        "id_str": id_str,
        "type_name": TYPE_NAMES.get(item.get("type"), "动态"),
        "name": (author.get("name") or "").strip(),
        "pub_time": (author.get("pub_time") or "").strip(),
        "content": content,
        "pics": pics,
    }


def _poll_uid(uid, ms, sender, now):
    """轮询单个 UP 主并更新其绑定的所有监控项（同 UID 一次请求、推送到多个群）。

    调用方负责 db.session.commit()。
    """
    items = fetch_latest(uid)
    if items is None:
        log(f"UP 主 {uid} 动态接口异常，本轮跳过")
        return
    if not items:
        log(f"UP 主 {uid} 暂无动态")
        return
    # B 站空间动态接口会把「置顶动态」放在列表最前（可能是旧动态），
    # 导致 items[0] 不一定是真正最新的动态。这里按真实发布时间 pub_ts 倒序排序，
    # 确保以「真正最新的动态」作为比对基准，避免置顶动态挡住新动态的检测。
    items = sorted(
        items,
        key=lambda it: (((it.get("modules") or {}).get("module_author") or {}).get("pub_ts") or 0),
        reverse=True,
    )
    latest_id = str(items[0].get("id_str") or "")
    for m in ms:
        if not m.last_dynamic_id:
            # 首次监控：只记录最新动态 id，不通知（避免误报历史动态）
            m.last_dynamic_id = latest_id
            m.last_check_at = now
            log(f"首次监控 UP 主 {uid}（备注 {m.remark or '-'}）-> 群 {m.group_id}，"
                f"记录最新动态 {latest_id}，不通知")
            continue
        # 收集比已推送 id 更新的动态（列表按时间倒序）
        new_items = []
        for it in items:
            sid = str(it.get("id_str") or "")
            if sid == m.last_dynamic_id:
                break
            new_items.append(it)
        if not new_items and str(items[0].get("id_str")) != m.last_dynamic_id:
            # 上次 id 不在当前列表（动态被删/被顶出首页），保守只推第一条
            log(f"UP 主 {uid} 上次动态 {m.last_dynamic_id} 不在列表，保守推送第一条")
            new_items = [items[0]]
        # 从旧到新推送，避免顺序颠倒
        for it in reversed(new_items):
            if _is_live_dynamic(it):
                # 『直播了』动态（UP 开播自动发的直播动态）不播报
                log(f"UP 主 {uid} 动态 {it.get('id_str')} 为直播动态，跳过播报")
                continue
            info = parse_item(it)
            if not info:
                continue
            text = (f"📢 UP 主新动态\n"
                    f"UP 主：{info['name'] or uid}\n"
                    f"类型：{info['type_name']}\n"
                    f"内容：{info['content']}\n"
                    f"时间：{info['pub_time'] or '未知'}\n"
                    f"链接：https://t.bilibili.com/{info['id_str']}")
            sender(m.group_id, text, info.get("pics") or [])
            log(f"UP 主 {uid} 新动态 {info['id_str']}（{info['type_name']}）已通知群 {m.group_id}")
        m.last_dynamic_id = latest_id
        m.last_check_at = now


def _enabled_by_uid():
    """读取启用的监控并按 UID 分组（同一 UID 一次请求、推送到其绑定的所有群）。"""
    monitors = db.session.execute(
        db.select(DynamicMonitor).where(DynamicMonitor.enabled.is_(True))
    ).scalars().all()
    by_uid = {}
    for m in monitors:
        by_uid.setdefault(m.uid, []).append(m)
    return by_uid


def run_once(sender):
    """立即全量检测所有启用的监控（手动触发 / 测试用）。"""
    by_uid = _enabled_by_uid()
    if not by_uid:
        log("轮询：暂无启用的动态监控")
        return
    now = datetime.now()
    for uid, ms in by_uid.items():
        _poll_uid(uid, ms, sender, now)
    db.session.commit()
    log(f"轮询完成：{sum(len(v) for v in by_uid.values())} 个监控项")


class DynamicMonitorThread:
    """后台队列轮询线程（daemon）。

    队列式串行轮询：把启用的监控按 UID 去重后逐个轮询，每轮询完一个
    等待 POLL_INTERVAL（3 分钟）再轮询下一个；一轮结束回到队首。
    - 1 个 UP：每 3 分钟轮询 1 次
    - 2 个 UP：依次 A、B、A、B… 每个 UP 约 6 分钟一轮
    目的：拉大单 UP 的请求间隔，规避 B 站风控（-352）。
    """

    def __init__(self, app, sender):
        self.app = app
        self.sender = sender
        self._thread = None
        self._running = False
        self._wake = threading.Event()  # 用于打断等待，便于 stop() 立即退出

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dynamon")
        self._thread.start()

    def stop(self):
        self._running = False
        self._wake.set()  # 唤醒正在等待的轮询，避免最长等 POLL_INTERVAL 才能退出
        if self._thread and self._thread.is_alive() \
                and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _sleep(self, seconds):
        """可被 stop() 立即打断的等待。"""
        self._wake.clear()
        self._wake.wait(timeout=seconds)

    def _run(self):
        while self._running:
            try:
                with self.app.app_context():
                    self._poll_queued(self.sender)
            except Exception as exc:
                try:
                    from bot import log
                    log(f"动态监控异常: {exc}")
                except Exception:
                    pass
            # 间隔由 _poll_queued 内部统一控制（每轮询完一个 UP 等待 POLL_INTERVAL），
            # 此处不再额外 sleep，避免单 UP 时实际间隔变成 2×POLL_INTERVAL。

    def _poll_queued(self, sender):
        """队列式串行轮询一轮（可能跨多个 POLL_INTERVAL）。"""
        by_uid = _enabled_by_uid()
        if not by_uid:
            # 无启用监控时也必须等待，否则外层 while 会变成死循环刷日志
            log(f"队列：暂无启用的动态监控，{POLL_INTERVAL}s 后重试")
            self._sleep(POLL_INTERVAL)
            return
        now = datetime.now()
        for uid, ms in by_uid.items():
            _poll_uid(uid, ms, sender, now)
            db.session.commit()
            if not self._running:
                return
            log(f"队列：已轮询 UP 主 {uid}，{POLL_INTERVAL}s 后轮询下一项")
            self._sleep(POLL_INTERVAL)
