# -*- coding: utf-8 -*-
"""QQ 开放平台官方机器人接入层（WebSocket 网关 + OpenAPI 发送）。

独立模块，不依赖业务代码；由上层（app.py）按需启用。核心能力：

1. **AccessToken 管理**：用 AppID + AppSecret 换取 AccessToken（有效期 2 小时），
   自动续期；之后所有请求带 `Authorization: QQBot {token}`。
2. **WebSocket 网关**：`GET /gateway/bot` 取网关地址 → 连接 → 鉴权(op2) →
   心跳(op1) → 断线恢复(op6) → 接收群事件。
3. **事件归一化**：把 GROUP_AT_MESSAGE_CREATE 等归一化为
   `(group_openid, user_openid, text, msg_id)`，交给回调处理。
4. **发送消息**：`POST /v2/groups/{group_openid}/messages`（文本被动回复）。

约定：官方**不返回真实 QQ 号**，用户/群标识均为 openid 字符串。

参考文档：https://bot.q.qq.com/wiki/develop/api-v2/
依赖：websocket-client（已在 requirements.txt）
"""
import json
import threading
import time
import urllib.request

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover - 便于无依赖环境下导入本模块做纯逻辑测试
    websocket = None

# ---- 接口地址 ----
API_BASE = "https://api.bot.qq.com"
TOKEN_URL = API_BASE + "/app/getAppAccessToken"
GATEWAY_URL = API_BASE + "/gateway/bot"

# ---- Intents：群聊 + 单聊事件（1 << 25） ----
INTENTS_GROUP_AND_C2C = 1 << 25  # 33554432

# ---- WebSocket opcode ----
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# ---- 关注的群事件 ----
EVENT_GROUP_AT_MESSAGE = "GROUP_AT_MESSAGE_CREATE"   # 群里 @机器人 的消息
EVENT_GROUP_MESSAGE = "GROUP_MESSAGE_CREATE"         # 群内全部消息（群主开启"获取群内全部消息"后）
EVENT_GROUP_ADD_ROBOT = "GROUP_ADD_ROBOT"            # 被拉进群
EVENT_GROUP_DEL_ROBOT = "GROUP_DEL_ROBOT"            # 被移出群
GROUP_MESSAGE_EVENTS = (EVENT_GROUP_AT_MESSAGE, EVENT_GROUP_MESSAGE)


def _http_json(url, method="GET", payload=None, headers=None, timeout=10):
    """极简 HTTP JSON 请求（仅用标准库，避免新增依赖）。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def strip_at(text):
    """去掉官方消息里的 @机器人 段（形如 <@!123456> 或 <@123456>）。"""
    if not text:
        return ""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "<" and i + 1 < len(text) and text[i + 1] == "@":
            j = text.find(">", i)
            if j != -1:
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out).strip()


class AccessTokenManager:
    """AccessToken 获取与自动续期（线程安全）。"""

    def __init__(self, appid, secret, log=None):
        self.appid = (appid or "").strip()
        self.secret = (secret or "").strip()
        self._log = log or (lambda *a: None)
        self._token = ""
        self._expire_at = 0.0
        self._lock = threading.Lock()

    def get(self):
        """返回有效 AccessToken；临近过期（60 秒内）会自动重新获取。"""
        if not self.appid or not self.secret:
            raise RuntimeError("缺少 AppID / AppSecret，无法获取 AccessToken")
        with self._lock:
            now = time.time()
            if self._token and now < self._expire_at - 60:
                return self._token
            resp = _http_json(TOKEN_URL, method="POST",
                              payload={"appId": self.appid, "clientSecret": self.secret})
            token = resp.get("access_token", "")
            if not token:
                raise RuntimeError(f"换取 AccessToken 失败：{resp}")
            try:
                expires = int(resp.get("expires_in", 7200))
            except (TypeError, ValueError):
                expires = 7200
            self._token = token
            self._expire_at = now + expires
            self._log(f"AccessToken 已刷新（有效期 {expires}s）")
            return token

    def auth_header(self):
        return {"Authorization": "QQBot " + self.get()}

    def gateway_url(self):
        """获取 WebSocket 网关地址。"""
        resp = _http_json(GATEWAY_URL, method="GET", headers=self.auth_header())
        url = resp.get("url", "")
        if not url:
            raise RuntimeError(f"获取网关地址失败：{resp}")
        return url

    def send_group_text(self, group_openid, content, msg_id=None, msg_seq=1):
        """向群发送文本消息。

        - 带 msg_id（被动回复）最省额度；msg_seq 为同一消息的第几个回复（从 1 起）。
        - 返回官方响应 dict。
        """
        url = f"{API_BASE}/v2/groups/{group_openid}/messages"
        payload = {"content": content, "msg_type": 0, "msg_seq": int(msg_seq)}
        if msg_id:
            payload["msg_id"] = msg_id
        return _http_json(url, method="POST", payload=payload, headers=self.auth_header())


class QQOfficialClient:
    """官方机器人 WebSocket 客户端（后台线程运行，自动重连/恢复）。

    - get_config: 返回配置 dict（读 qq_appid / qq_appsecret / qq_sandbox）
    - on_group_message: 收到群消息时回调
      (group_openid, user_openid, text, msg_id, nickname)
    - on_event: 其它事件回调 (event_type, data)，可为 None
    - log: 日志函数
    """

    def __init__(self, get_config, on_group_message=None, on_event=None, log=None):
        if websocket is None:
            raise RuntimeError("缺少 websocket-client 依赖，请先 pip install websocket-client")
        self.get_config = get_config
        self.on_group_message = on_group_message
        self.on_event = on_event
        self._log = log or (lambda *a: None)
        self._thread = None
        self._running = False
        self.ws = None
        self._token_mgr = None
        self._session_id = ""
        self._last_seq = 0
        self._heartbeat_interval = 45.0
        self._next_heartbeat = 0.0
        self.status = {"connected": False, "last_error": "", "connected_at": None,
                       "last_event_at": None, "bot": None}

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        cfg = self.get_config() or {}
        self._token_mgr = AccessTokenManager(cfg.get("qq_appid"), cfg.get("qq_appsecret"),
                                             log=self._log)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="qq-official-ws")
        self._thread.start()
        self._log("QQ 官方机器人线程已启动")

    def stop(self):
        self._running = False
        if self.ws is not None:
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
        self._log("QQ 官方机器人线程已停止")

    # ---------- 主循环 ----------
    def _run(self):
        backoff = 1
        while self._running:
            try:
                if not self._session_id:
                    url = self._token_mgr.gateway_url()
                    self._log(f"连接官方网关：{url}")
                else:
                    url = self._token_mgr.gateway_url()
                self.ws = websocket.create_connection(url, timeout=self._heartbeat_interval)
                self._reset_heartbeat()
                if self._session_id and self._last_seq:
                    self._send_resume()
                else:
                    self._send_identify()
                backoff = 1
                self._recv_loop()
            except Exception as e:
                self.status["connected"] = False
                self.status["last_error"] = str(e)
                self._log(f"连接异常：{e}；{backoff}s 后重连")
            finally:
                try:
                    if self.ws is not None:
                        self.ws.close()
                except Exception:
                    pass
                self.ws = None
            if not self._running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _reset_heartbeat(self):
        self._next_heartbeat = time.time()

    def _recv_loop(self):
        while self._running and self.ws is not None:
            now = time.time()
            if now >= self._next_heartbeat:
                self._send_heartbeat()
                self._next_heartbeat = now + self._heartbeat_interval
            try:
                raw = self.ws.recv()
            except Exception as e:
                if not self._running:
                    return
                # 读超时（无数据）：继续循环以按时发心跳
                if websocket is not None and isinstance(e, websocket.WebSocketTimeoutException):
                    continue
                # 其它异常（连接被关闭/网络断开）：退出循环，交由 _run 重连
                self._log(f"接收异常，准备重连：{e}")
                return
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            self._handle_payload(payload)

    # ---------- 协议发送 ----------
    def _send_identify(self):
        self.ws.send(json.dumps({
            "op": OP_IDENTIFY,
            "d": {
                "token": "QQBot " + self._token_mgr.get(),
                "intents": INTENTS_GROUP_AND_C2C,
                "shard": [0, 1],
            },
        }))

    def _send_resume(self):
        self.ws.send(json.dumps({
            "op": OP_RESUME,
            "d": {
                "token": "QQBot " + self._token_mgr.get(),
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }))

    def _send_heartbeat(self):
        try:
            self.ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._last_seq or None}))
        except Exception:
            pass

    # ---------- 协议接收 ----------
    def _handle_payload(self, payload):
        op = payload.get("op")
        if "s" in payload and payload["s"] is not None:
            self._last_seq = payload["s"]
        if op == OP_HELLO:
            self._heartbeat_interval = float(payload.get("d", {}).get("heartbeat_interval", 45000)) / 1000.0
            self._reset_heartbeat()
        elif op == OP_HEARTBEAT_ACK:
            pass
        elif op == OP_RECONNECT:
            self._log("服务端要求重连")
            try:
                if self.ws:
                    self.ws.close()
            except Exception:
                pass
        elif op == OP_INVALID_SESSION:
            self._session_id = ""
            self._last_seq = 0
        elif op == OP_DISPATCH:
            self._handle_event(payload.get("t"), payload.get("d") or {})

    def _handle_event(self, event_type, data):
        if event_type == "READY":
            self._session_id = data.get("session_id", "")
            self.status["connected"] = True
            self.status["connected_at"] = time.time()
            self.status["bot"] = (data.get("user") or {}).get("username")
            self._log(f"已鉴权上线（机器人：{self.status['bot']}）")
            return
        if event_type == "RESUMED":
            self.status["connected"] = True
            self._log("会话已恢复")
            return
        self.status["last_event_at"] = time.time()
        self._log(f"收到事件：{event_type}")
        if event_type in GROUP_MESSAGE_EVENTS and self.on_group_message:
            group_openid = data.get("group_openid", "")
            author = data.get("author") or {}
            user_openid = author.get("member_openid") or author.get("id") or ""
            nickname = author.get("username") or ""
            text = strip_at(data.get("content", ""))
            msg_id = data.get("id", "")
            try:
                self.on_group_message(group_openid, user_openid, text, msg_id, nickname)
            except Exception as e:
                self._log(f"处理群消息异常：{e}")
            return
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as e:
                self._log(f"处理事件异常：{e}")

    # ---------- 对外发送 ----------
    def reply_group(self, group_openid, content, msg_id=None, msg_seq=1):
        return self._token_mgr.send_group_text(group_openid, content, msg_id, msg_seq)


if __name__ == "__main__":
    # 独立自测：读取环境变量 QQ_APPID / QQ_APPSECRET，连上网关并打印收到的 @消息
    import os

    def _log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    cfg = {"qq_appid": os.environ.get("QQ_APPID", ""),
           "qq_appsecret": os.environ.get("QQ_APPSECRET", "")}

    def on_msg(group, user, text, msg_id, nickname=""):
        _log(f"收到群消息 group={group} user={user} nickname={nickname} text={text!r}")
        if text.strip() in ("/ping", "ping"):
            client.reply_group(group, "pong（来自 QQ 官方机器人）", msg_id=msg_id)
            _log("已回复 pong")

    client = QQOfficialClient(lambda: cfg, on_group_message=on_msg, log=_log)
    client.start()
    _log("按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.stop()
