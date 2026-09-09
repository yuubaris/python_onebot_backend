# -*- coding: utf-8 -*-
"""P2 验证：GameCore 门面 + 命令转发。

- 根目录 commands.py 的地城命令已转发到 dungeon_service.commands（golden 逐字 diff 由 test_golden 兜底）；
- GameCore.run_command 直调与根 dispatch 输出一致（同 seed 同场景）；
- settle 保守策略：命令触发结算。
"""
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User  # noqa: E402
from dungeon_service.game_core import GameCore  # noqa: E402
from scenarios import build_player_a, build_player_b  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _mk_user():
    return build_player_a(None)


def test_run_command_unknown(app):
    g = GameCore()
    u = _mk_user()
    assert g.run_command("不存在的命令", u, 10001) is None


def test_run_command_bag_matches_golden(app):
    import re as _re
    g = GameCore()
    u = _mk_user()
    random.seed(20260909)
    out = _re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", g.run_command("背包", u, 10001)).strip()
    golden = open(os.path.join(os.path.dirname(__file__), "golden", "bag.txt"), encoding="utf-8").read().strip()
    assert out == golden


def test_run_command_alias_pinyin(app):
    g = GameCore()
    u = _mk_user()
    assert g.run_command("beibao", u, 10001) == g.run_command("背包", u, 10001)


def test_settle_conservative(app):
    """settle 由命令显式触发（保守），不做时间驱动。"""
    g = GameCore()
    u = build_player_b(None)
    u.dungeon_last_update = time.time() - 600
    db.session.commit()
    progress_before = u.dungeon_progress
    g.settle(u)
    # 结算按流逝时间推进: 当前层剩余进度递减, 铜币/收益入账
    assert u.dungeon_progress < progress_before
    assert u.dungeon_coin_acc > 0
    # take_events: 返回列表(战报/掉落事件), 取走即清空
    events = g.take_events(u.user_id)
    assert isinstance(events, list)
    assert g.take_events(u.user_id) == []
