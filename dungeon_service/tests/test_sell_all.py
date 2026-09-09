# -*- coding: utf-8 -*-
"""一键出售（/出售 全部，v2.12.7）：可出售、未穿戴、非本部位最高评分的非专属装备全部卖出。"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User, UserItem  # noqa: E402
from dungeon_service.commands import cmd_sell  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _mk_user(app):
    random.seed(20260909)
    u = User(user_id=95001, nickname="测试", profession="warrior", tier=3,
             copper=100000, saved_dungeon_layer=1200)
    db.session.add(u)
    db.session.flush()
    # weapon 两件：铁剑(评分高,穿戴中) + 短剑(评分低,可卖)；armor 一件；锻造一件；专属一件
    for iid, equipped in (("iron_sword", 1), ("short_sword", 0),
                          ("leather_armor", 0), ("forge_iron_sword", 0),
                          ("gear_other_100_weapon", 0)):
        db.session.add(UserItem(user_id=u.user_id, item_id=iid, equipped=equipped))
    db.session.commit()
    return u


def test_sell_all_sells_only_lowest_same_slot(app):
    u = _mk_user(app)
    reply = cmd_sell(u, 12345, "全部")
    assert "卖出 1 件" in reply and "短剑×1" in reply
    assert "30" in reply  # 短剑 50 铜 × 60%
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == u.user_id)
    ).scalars().all()
    ids = {r.item_id for r in rows}
    assert ids == {"iron_sword", "leather_armor", "forge_iron_sword", "gear_other_100_weapon"}
    assert u.copper == 100000 + 30


def test_sell_all_alias_onekey(app):
    u = _mk_user(app)
    reply = cmd_sell(u, 12345, "一键")
    assert "卖出 1 件" in reply


def test_sell_all_no_sellable(app):
    u = _mk_user(app)
    cmd_sell(u, 12345, "全部")   # 已卖完可卖的
    reply = cmd_sell(u, 12345, "全部")
    assert "没有可一键出售的装备" in reply
