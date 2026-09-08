# -*- coding: utf-8 -*-
"""CDP（Chrome DevTools Protocol）请求层 —— 用真实 Edge 浏览器发起 B 站接口请求。

与 curl 子进程相比：请求由真实浏览器内核发出，具备完整 TLS/JS/环境指纹，
Cookie 直接使用 CDP 浏览器 profile 中已有的登录态（用户已在 CDP 浏览器登录 B 站），
无需注入 bili_cookies.txt。

用法（dynamon.py 请求层）：
    rc, body = cdp.cdp_get_text(url, referer)
    # rc: 0 成功 / 非 0 失败；body: 页面文本（API 返回 JSON 或风控 HTML）
"""
import json
import threading
import time
import urllib.request

import websocket  # websocket-client

CDP_ENDPOINT = "http://127.0.0.1:9222"

_ws = None
_ws_url = None
_ws_lock = threading.Lock()
_msg_id = 0


def _http_json(path, method="GET"):
    """访问 CDP HTTP 端点，返回解析后的 JSON。"""
    url = CDP_ENDPOINT + path
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _new_page_ws():
    """复用已有普通页面，否则新建 about:blank 页面，返回其 webSocketDebuggerUrl。"""
    try:
        tabs = _http_json("/json")
        for t in tabs:
            if t.get("type") == "page" and not t.get("url", "").startswith("devtools"):
                return t["webSocketDebuggerUrl"]
    except Exception:
        pass
    try:
        data = _http_json("/json/new?about:blank", method="PUT")
        return data["webSocketDebuggerUrl"]
    except Exception as exc:
        raise RuntimeError(f"CDP 新建页面失败: {exc}")


def _connect():
    global _ws, _ws_url
    ws_url = _new_page_ws()
    _ws = websocket.create_connection(ws_url, timeout=8)
    _ws.settimeout(20)
    _ws_url = ws_url


def _send(method, params=None):
    """发送 CDP 命令并同步等待该 id 的响应；WS 断线自动重连一次。"""
    global _ws, _ws_url, _msg_id
    params = params or {}
    with _ws_lock:
        for attempt in (0, 1):
            try:
                if _ws is None:
                    _connect()
                _msg_id += 1
                mid = _msg_id
                _ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    resp = json.loads(_ws.recv())
                    if resp.get("id") == mid:
                        return resp
            except Exception:
                try:
                    if _ws:
                        _ws.close()
                except Exception:
                    pass
                _ws = None
                if attempt == 0:
                    continue
                raise
    return {}


def cdp_get_text(url, referer=None):
    """用真实 Edge 导航到 url，返回 (0, 页面文本)；失败返回 (非0, None)。

    原生导航不受 CORS 限制，会带上 CDP 浏览器登录态 Cookie（B 站已登录），
    浏览器完整渲染后通过 Runtime.evaluate 读取 document.body.innerText。
    """
    try:
        _send("Page.enable", {})
        _send("Network.enable", {})
        nav_params = {"url": url}
        if referer:
            nav_params["referrer"] = referer
        _send("Page.navigate", nav_params)
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                st = _send("Runtime.evaluate",
                           {"expression": "document.readyState", "returnByValue": True})
                val = (st.get("result") or {}).get("result") or {}
                if val.get("value") in ("interactive", "complete"):
                    break
            except Exception:
                pass
            time.sleep(0.3)
        time.sleep(0.4)
        res = _send("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText : ''",
            "returnByValue": True,
        })
        value = ((res.get("result") or {}).get("result") or {}).get("value")
        if isinstance(value, str):
            return 0, value
        return 1, None
    except Exception as exc:
        return 2, None
