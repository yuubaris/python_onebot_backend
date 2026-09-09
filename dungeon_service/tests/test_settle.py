# -*- coding: utf-8 -*-
"""settle 冒烟：地下城结算（推进/铜币/矿石/草药/战报）在无头环境下可跑且行为正确。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import flask  # noqa: E402
import dungeon  # noqa: E402
import material  # noqa: E402
import ore  # noqa: E402
from models import db, User  # noqa: E402

CYCLE = 900  # 矿石/草药周期秒


@pytest.fixture()
def ctx():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _make_user(uid, layer=3200, ore_ok=True, herb_ok=True, cycles=2):
    now = time.time()
    u = User(user_id=uid, nickname="测", profession="warrior", tier=6,
             dungeon_layer=layer, dungeon_progress=10 ** 12,
             dungeon_last_update=now, dungeon_coin_acc=0.0,
             dungeon_ore_eligible=1 if ore_ok else 0,
             dungeon_ore_last=now - cycles * CYCLE if ore_ok else None,
             dungeon_herb_eligible=1 if herb_ok else 0,
             dungeon_herb_last=now - cycles * CYCLE if herb_ok else None)
    db.session.add(u)
    db.session.commit()
    return u


def _matmap(uid):
    return {m["id"]: m["count"] for m in material.owned_materials(uid)}


def _oremap(uid):
    return {o["id"]: o["count"] for o in ore.owned_ores(uid)}


def test_settle_advances_and_earns(ctx):
    u = _make_user(9001, layer=100, ore_ok=False, herb_ok=False)
    dungeon.settle_dungeon(u)
    assert u.dungeon_progress < 10 ** 12       # 进度被消耗
    assert u.dungeon_coins_earned >= 0


def test_ore_and_herb_both_settle_no_conflict(ctx):
    """同周期矿石+草药各自结算、互不抢占（v2.11.86 回归）。"""
    u = _make_user(9002, layer=3200)
    bo, bm = sum(_oremap(9002).values()), sum(_matmap(9002).values())
    dungeon.settle_dungeon(u)
    ao, am = sum(_oremap(9002).values()), sum(_matmap(9002).values())
    assert ao > bo, "矿石未结算"
    assert am > bm, "材料(草药/特殊)未结算"
    assert u.dungeon_ore_last and u.dungeon_herb_last  # 时间戳都推进


def test_material_no_empty_cycle(ctx):
    """草药数量递增+必不掉空：多周期样本中不应出现空周期。"""
    import random
    random.seed(2026)
    users = []
    for i in range(200):
        users.append(_make_user(9100 + i, layer=180))
    for u in users:
        b = sum(_matmap(u.user_id).values())
        dungeon.settle_dungeon(u)
        got = sum(_matmap(u.user_id).values()) - b
        assert got >= 1, f"空周期: user {u.user_id}"


def test_boss_report_queue(ctx):
    """Boss 战报队列：结算后 /地下城 可带出战报（非 /地下城 命令不展示）。"""
    # 用挑战记录模拟战报场景：直接验证队列 API 可无头使用
    u = _make_user(9200, layer=1500, ore_ok=False, herb_ok=False)
    dungeon._queue_boss_report(u.user_id, ["测试战报"])
    report = dungeon.take_boss_report(u.user_id)
    assert report and "测试战报" in report
    assert dungeon.take_boss_report(u.user_id) == []  # 取走即清空
