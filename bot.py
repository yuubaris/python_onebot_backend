# -*- coding: utf-8 -*-
"""OneBot 11 正向 WebSocket（Universal）客户端。

本程序作为 WebSocket 客户端主动连接 OneBot 服务端（即“正向 WebSocket”，
配置项：主机 / 端口 / 路径 / 授权 Token / 消息格式 / 角色）。
- 路径建议使用 "/"（Universal：同一连接既用于 API 调用，也接收事件推送）。
- 角色字段用于记录/展示连接角色（Universal）。
- 消息格式为“数组”：上报与发送消息均使用消息段数组格式。
"""
import json
import threading
import time
from collections import deque

import websocket

# 环形内存日志（供管理页面查看）
_logs = deque(maxlen=300)


def log(message: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    _logs.append(line)
    print(line, flush=True)


def get_logs(limit=100):
    return list(_logs)[-limit:]


class OneBotClient:
    """OneBot 正向 WebSocket 通用客户端（后台线程运行，自动重连）。"""

    def __init__(self, get_config, on_event):
        self.get_config = get_config   # 返回配置 dict 的可调用对象
        self.on_event = on_event       # 事件回调（在 bot 线程中执行，需自行处理 app 上下文）
        self.ws = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._callbacks = {}           # echo -> callback
        self._echo_seq = 0
        self.status = {
            "connected": False,
            "url": "",
            "last_error": "",
            "connected_at": None,
            "last_event_at": None,
        }
        # 连接超时（秒），避免主机不可达时长时间阻塞
        websocket.setdefaulttimeout(10)

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="onebot-ws")
        self._thread.start()
        log("机器人线程已启动")

    def stop(self):
        self._running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.status["connected"] = False
        if self._thread and self._thread.is_alive() \
                and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None
        log("机器人线程已停止")

    def restart(self):
        log("正在重启机器人连接…")
        self.stop()
        time.sleep(0.5)
        self.start()

    # ---------- 连接主循环（自动重连） ----------
    def _run(self):
        while self._running:
            cfg = self.get_config()
            host = (cfg.get("bot_host") or "127.0.0.1").strip()
            port = str(cfg.get("bot_port") or "6700").strip()
            path = (cfg.get("bot_path") or "/").strip()
            if not path.startswith("/"):
                path = "/" + path
            token = (cfg.get("bot_token") or "").strip()
            url = f"ws://{host}:{port}{path}"
            self.status["url"] = url

            headers = []
            if token:
                headers.append(f"Authorization: Bearer {token}")
            headers.append("X-Client-Role: Universal")

            try:
                ws = websocket.WebSocketApp(
                    url,
                    header=headers,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws = ws
                log(f"正在连接 OneBot 服务端: {url}")
                ws.run_forever()
            except Exception as exc:
                self._set_error(str(exc))
                log(f"连接异常: {exc}")

            self.ws = None
            self.status["connected"] = False
            if self._running:
                log("连接断开，3 秒后重连…")
                time.sleep(3)

    # ---------- WebSocket 回调 ----------
    def _on_open(self, ws):
        self.status["connected"] = True
        self.status["connected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.status["last_error"] = ""
        log("已连接 OneBot 服务端")

    def _on_close(self, ws, close_status_code, close_msg):
        self.status["connected"] = False
        log(f"连接关闭: code={close_status_code} msg={close_msg}")

    def _on_error(self, ws, error):
        self._set_error(str(error))
        log(f"连接错误: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            log(f"收到无法解析的消息: {str(message)[:200]}")
            return
        if "echo" in data:
            self._dispatch_response(data)
        else:
            self.status["last_event_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                self.on_event(data)
            except Exception as exc:
                log(f"事件处理异常: {exc}")

    # ---------- API 调用 ----------
    def send_action(self, action, params=None, callback=None):
        """向 OneBot 发送 API 调用，返回 echo（失败返回 None）。"""
        echo = None
        payload = {"action": action, "params": params or {}}
        if callback is not None:
            self._echo_seq += 1
            echo = f"echo-{self._echo_seq}"
            payload["echo"] = echo
            self._callbacks[echo] = callback
        with self._lock:
            if self.ws is None:
                return None
            try:
                self.ws.send(json.dumps(payload, ensure_ascii=False))
                return echo
            except Exception as exc:
                log(f"发送失败: {exc}")
                return None

    def _dispatch_response(self, data):
        echo = data.get("echo")
        cb = self._callbacks.pop(echo, None)
        if cb:
            try:
                cb(data)
            except Exception as exc:
                log(f"回调执行异常: {exc}")

    def _set_error(self, msg):
        self.status["last_error"] = msg
        self.status["connected"] = False
