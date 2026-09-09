# -*- coding: utf-8 -*-
"""晋升门槛（v2.12.17）：T7=3000（T6=2500），须「已通关」要求层（到达不算）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User  # noqa: E402
from dungeon_service.commands import cmd_promote  # noqa: E402
from dungeon_service.tiers import TIER_LAYER, next_promotion  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _mk(app, uid, tier, cleared, layer=0):
    u = User(user_id=uid, nickname="测试", profession="warrior", tier=tier,
             copper=10 ** 9, dungeon_cleared=cleared, dungeon_layer=layer)
    db.session.add(u)
    db.session.commit()
    return u


def test_tier_layer_values():
    assert TIER_LAYER[6] == 2500
    assert TIER_LAYER[7] == 3000
    assert TIER_LAYER[6] < TIER_LAYER[7]  # 无倒挂


def test_promote_requires_cleared_not_reached(app):
    """到达 3000 层但未通关（cleared=2999）→ 不能晋升 T7。"""
    u = _mk(app, 98001, tier=6, cleared=2999, layer=3000)
    reply = cmd_promote(u, 99999, "")
    assert "已通关层数" in reply and "3000" in reply and "2999" in reply
    assert u.tier == 6


def test_promote_t7_after_clearing_3000(app):
    u = _mk(app, 98002, tier=6, cleared=3000)
    assert cmd_promote(u, 99999, "") and u.tier == 7


def test_promote_t6_requires_2500_cleared(app):
    # 到达 2500 未通关 → 不能晋升 T6
    u = _mk(app, 98003, tier=5, cleared=2499, layer=2500)
    reply = cmd_promote(u, 99999, "")
    assert "已通关层数" in reply and "2500" in reply
    assert u.tier == 5
    # 通关 2500 → 可晋升
    u2 = _mk(app, 98004, tier=5, cleared=2500)
    cmd_promote(u2, 99999, "")
    assert u2.tier == 6


def test_next_promotion_chain():
    assert next_promotion(6) == 7
    assert next_promotion(7) is None
