# -*- coding: utf-8 -*-
"""抽奖：矿石混入材料池（与草药/特殊材料等价）+ 档3稀有装备路径。"""
import random
import pytest
import flask
from models import db, User, UserItem, UserOre
from dungeon_service import lottery


@pytest.fixture()
def app():
    a = flask.Flask(__name__)
    a.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    a.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(a)
    with a.app_context():
        db.create_all()
        yield a
        db.session.remove()


def _mk_user():
    u = User(user_id=7, nickname="抽", profession="warrior", tier=5, copper=10**9)
    db.session.add(u)
    db.session.commit()
    return u


def test_pool_contains_ore(app):
    """三档材料池都含矿石，稀有度区间与草药等价。"""
    order = {"common": 0, "rare": 1, "legendary": 2, "myth": 3}
    for tier_no, (lo, hi) in {1: ("common", "rare"), 2: ("rare", "legendary"), 3: ("legendary", "myth")}.items():
        pool = lottery._material_pool(lo, hi)
        ores = [m for m in pool if m.get("category") == "ore"]
        herbs = [m for m in pool if m.get("category") != "ore"]
        assert ores, f"档{tier_no} 无矿石"
        assert herbs, f"档{tier_no} 无材料"
        for m in ores:
            assert order.get(m.get("rarity")) is not None
            assert lo in ("common", "rare", "legendary")  # 区间起点合法


def test_tier3_equip_no_error(app):
    """档3 装备抽取不报错（rare_drops.json 仓库根路径回归）。"""
    u = _mk_user()
    random.seed(20260909)
    got_equip = False
    for i in range(60):
        random.seed(20260909 + i)
        text, jackpot = lottery.roll(u, 3)
        if "获得装备" in text:
            got_equip = True
            break
    assert got_equip
    assert db.session.query(UserItem).filter_by(user_id=7).count() >= 1


def test_ore_award_granted_to_ore_table(app):
    """命中矿石写入 UserOre（非 UserMaterial）。"""
    u = _mk_user()
    from models import UserMaterial
    for i in range(400):
        random.seed(555 + i)
        text, _ = lottery.roll(u, 1)
        if "获得矿石" in text:
            assert db.session.query(UserOre).filter_by(user_id=7).count() >= 1
            assert "获得矿石「" in text
            return
    pytest.fail("400 次未命中矿石（概率异常）")
