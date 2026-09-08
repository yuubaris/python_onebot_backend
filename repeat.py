# -*- coding: utf-8 -*-
"""复读机：群内 2 个不同用户发送相同消息后，立刻复读一次。

重复检测规则（防止刷屏）：
- 同一轮（时间窗口内）同一内容只复读一次；
- 一旦因「2 个不同用户」触发复读，之后第 3 个、第 4 个……同内容用户都不再复读；
- 同一用户重复发送相同内容不计入（必须 2 个不同用户才算）；
- 时间窗口过期后自动重置，新的一轮可重新计数。

线程安全：使用全局锁。
"""
import threading
import time

REPEAT_WINDOW_SECONDS = 60   # 相同内容计数窗口（秒），超过后重置
REPEAT_TRIGGER_USERS = 2     # 触发复读所需的不同用户数


class RepeatTracker:
    def __init__(self, window=REPEAT_WINDOW_SECONDS, trigger=REPEAT_TRIGGER_USERS):
        self.window = window
        self.trigger = trigger
        self._states = {}  # (group_id, content) -> {"users": set, "repeated": bool, "ts": float}
        self._lock = threading.Lock()

    def _cleanup(self, now):
        expired = [k for k, v in self._states.items() if now - v["ts"] > self.window]
        for k in expired:
            self._states.pop(k, None)

    def check(self, group_id, user_id, content):
        """记录一条群消息，返回 True 表示「应复读当前消息」（并标记本轮已复读）。

        content 建议传入 strip 后的文本；不同 user_id 才算不同用户。
        """
        content = (content or "").strip()
        if not content:
            return False
        now = time.time()
        key = (int(group_id), content)

        with self._lock:
            self._cleanup(now)
            state = self._states.get(key)

            # 无记录或已过期 → 开启新的一轮
            if state is None or now - state["ts"] > self.window:
                self._states[key] = {"users": {int(user_id)}, "repeated": False, "ts": now}
                return False

            # 本轮已复读过 → 第 3 人及之后不再复读
            if state["repeated"]:
                return False

            # 同一个人重复发送 → 不计数
            if int(user_id) in state["users"]:
                return False

            # 新的不同用户 → 计数
            state["users"].add(int(user_id))
            state["ts"] = now
            if len(state["users"]) >= self.trigger:
                state["repeated"] = True
                return True
            return False
