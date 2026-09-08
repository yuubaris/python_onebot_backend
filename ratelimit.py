# -*- coding: utf-8 -*-
"""消息频率限制器（内存实现，线程安全）。

按「群号 × 用户号」维度统计 "/" 指令的发送频率：
- 在统计窗口内超过最大条数时，触发冷却：返回一条提示，并进入静默过滤期。
- 冷却期内再次收到的消息被静默丢弃（不再显示提示，即“消息过滤”）。
- 冷却结束后重新计时，用户可再次发送。
"""
import threading
import time


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        # key -> {"window_start": float, "count": int, "cooldown_until": float}
        self._state = {}

    def check(self, key, max_count, window_seconds, cooldown_seconds):
        """检查是否允许处理。

        返回 (allowed, reply)：
            - allowed=True             ：允许处理（本次已计入）。
            - allowed=False, reply=str ：超出频率限制，应回复该提示，并进入冷却期。
            - allowed=False, reply=None：处于冷却期，静默过滤（不回复、不处理）。
        """
        now = time.time()
        max_count = max(int(max_count), 1)
        window_seconds = max(int(window_seconds), 1)
        cooldown_seconds = max(int(cooldown_seconds), 1)

        with self._lock:
            st = self._state.get(key)
            if st is None:
                st = {"window_start": now, "count": 0, "cooldown_until": 0.0}
                self._state[key] = st

            # 冷却期：静默过滤
            if now < st["cooldown_until"]:
                return False, None

            # 窗口过期则重置
            if now - st["window_start"] > window_seconds:
                st["window_start"] = now
                st["count"] = 0

            st["count"] += 1
            if st["count"] > max_count:
                # 触发冷却：冷却结束后重新计时
                st["cooldown_until"] = now + cooldown_seconds
                st["window_start"] = now + cooldown_seconds
                st["count"] = 0
                return False, f"发送消息过快，请在 {cooldown_seconds} 秒后再试。"
            return True, None

    def clear(self):
        with self._lock:
            self._state.clear()
