# -*- coding: utf-8 -*-
"""环形内存日志（供管理页面查看）。

原先放在 bot.py（OneBot 客户端）内；移除 OneBot 通道后独立成模块，
供 app.py / qq_official.py 等共用。
"""
import time
from collections import deque

_logs = deque(maxlen=300)


def log(message: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    _logs.append(line)
    print(line, flush=True)


def get_logs(limit=100):
    return list(_logs)[-limit:]
