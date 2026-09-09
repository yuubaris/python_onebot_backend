# -*- coding: utf-8 -*-
"""P3 验证：消息端 dispatch_command 已切换为 GameCore 门面直调（保守）。

- 地下城命令：经 dispatch_command 完整消息路径执行，输出与 golden 逐字一致；
- 结算入口合并：in_dungeon 时由 game.settle 结算（等价原 settle_dungeon）；
- 非地下城命令（签到等）仍走外壳 handlers。
"""
import os
import random
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db  # noqa: E402
from commands import dispatch_command  # noqa: E402
from scenarios import build_player_a, build_player_b  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _golden(name):
    p = os.path.join(os.path.dirname(__file__), "golden", f"{name}.txt")
    with open(p, encoding="utf-8") as f:
        return f.read().strip()


def test_dispatch_bag_matches_golden(app):
    u = build_player_a(None)
    random.seed(20260909)
    out = dispatch_command("/背包", u, 12345)
    assert out == _golden("bag")


def test_dispatch_dungeon_status(app):
    u = build_player_b(None)
    out = dispatch_command("/地下城 状态", u, 12345)
    assert "第 1500 层" in out


def test_dispatch_checkin_shell_command(app):
    """非地下城命令仍走外壳 handler。"""
    u = build_player_a(None)
    out = dispatch_command("/签到", u, 12345)
    assert "签到成功" in out


def test_dispatch_unknown_first(app):
    u = build_player_a(None)
    out = dispatch_command("/不存在的命令", u, 12345)
    assert out == "未知指令，发送 /帮助 查看可用命令。"


def test_dispatch_boss_report_only_on_dungeon(app):
    """战报仅在 /地下城 展示；/背包 不夹带战报(v2.11.81 行为保持)。"""
    u = build_player_b(None)
    u.dungeon_last_update = time.time() - 600  # 有结算量, 可能产生掉落战报
    db.session.commit()
    bag = dispatch_command("/背包", u, 12345)
    assert "📦" not in (bag or "")
