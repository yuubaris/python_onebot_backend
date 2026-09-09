# -*- coding: utf-8 -*-
"""/挑战 装备：命名 Boss 专属装备属性 + 全服余量（v2.12.12）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User, UserItem  # noqa: E402
from dungeon_service.boss import gear_list_text  # noqa: E402
from dungeon_service.commands import cmd_challenge  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _mk_user(app):
    u = User(user_id=96001, nickname="测试", profession="warrior", tier=4,
             copper=100000, saved_dungeon_layer=1200)
    db.session.add(u)
    db.session.flush()
    db.session.commit()
    return u


def test_gear_list_shows_all_groups_and_remaining(app):
    _mk_user(app)
    txt = gear_list_text()
    assert "命名 Boss 专属装备" in txt
    assert "十方魔主·终焉（1000 层" in txt and "虚空大君·裂渊（800 层" in txt  # 按 Boss 名分组
    assert "终焉裁决" in txt and "裂渊之印" in txt
    assert "余量3/3" in txt      # 4 大 Boss 限量 3，未产出
    assert "余量5/5" in txt      # 其他命名 Boss 限量 5，未产出


def test_gear_list_remaining_decreases_when_owned(app):
    u = _mk_user(app)
    db.session.add(UserItem(user_id=u.user_id, item_id="gear_big_1000_weapon"))
    db.session.commit()
    txt = gear_list_text()
    assert "余量2/3" in txt       # 已产出一件终焉裁决


def test_gear_list_single_detail(app):
    txt = gear_list_text(gear_name="终焉裁决")
    assert "十方魔主·终焉 专属" in txt
    assert "战·主手" in txt
    assert "评分" in txt and "掉率" in txt


def test_gear_list_not_found(app):
    assert "没有找到专属装备" in gear_list_text(gear_name="不存在的装备")


def test_cmd_challenge_gear_keyword(app):
    u = _mk_user(app)
    reply = cmd_challenge(u, 99999, "装备")
    assert "命名 Boss 专属装备" in reply
    reply2 = cmd_challenge(u, 99999, "装备 终焉裁决")
    assert "十方魔主·终焉 专属" in reply2
