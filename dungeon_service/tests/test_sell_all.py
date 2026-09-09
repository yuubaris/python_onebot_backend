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


def _mk_user_rare_spares(app):
    """复现 v2.12.8 bug 场景：稀有闲置装备（未穿戴、非本部位最高评分）应被一键出售。
    修复前 meta 仅含商店装备（load_equipment），稀有装备全部被跳过 → 误报无可卖。"""
    u = User(user_id=95002, nickname="测试", profession="warrior", tier=4,
             copper=100000, saved_dungeon_layer=1200)
    db.session.add(u)
    db.session.flush()
    for iid, equipped in (
        ("rare_weapon_4", 1),        # 武器：稀有，穿戴
        ("rare_shield_4", 1),        # 盾牌：稀有，穿戴（部位最高）
        ("star_shield", 0),          # 盾牌：商店 T2，闲置、非最高 → 应卖
        ("rare_armor_4", 1),         # 防具：稀有，穿戴
        ("rare_armor_4", 0),         # 防具：稀有，闲置同件 → 应卖
        ("rare_accessory_3", 0),     # 饰品：稀有，闲置 → 应卖
        ("rare_accessory_4", 0),     # 饰品：稀有，闲置（v2.12.10 曾保留 1 件）
        ("rare_accessory_4", 0),     # 饰品：稀有，闲置 → 应卖
        ("gear_other_700_armor", 1),   # 专属（限定）噬星之印：更高分、不可售
    ):
        db.session.add(UserItem(user_id=u.user_id, item_id=iid, equipped=equipped))
    db.session.commit()
    return u


def test_sell_all_sells_rare_spares(app):
    """稀有掉落闲置件可被一键出售（v2.12.9 修复：meta 合并 rare_drops.json）；
    无更高分专属时，可售最高件（星穹法印×1）仍保留。"""
    u = User(user_id=95003, nickname="测试", profession="warrior", tier=4,
             copper=100000, saved_dungeon_layer=1200)
    db.session.add(u)
    db.session.flush()
    for iid, equipped in (
        ("rare_weapon_4", 1),        # 武器：稀有，穿戴
        ("rare_shield_4", 1),        # 盾牌：稀有，穿戴（部位最高）
        ("star_shield", 0),          # 盾牌：商店 T2，闲置、非最高 → 应卖
        ("rare_armor_4", 1),         # 防具：稀有，穿戴
        ("rare_armor_4", 0),         # 防具：稀有，闲置同件 → 应卖
        ("rare_accessory_3", 0),     # 饰品：稀有，闲置 → 应卖
        ("rare_accessory_4", 0),     # 饰品：稀有，闲置（部位最高保留 1 件）
        ("rare_accessory_4", 0),     # 饰品：稀有，闲置 → 应卖
    ):
        db.session.add(UserItem(user_id=u.user_id, item_id=iid, equipped=equipped))
    db.session.commit()
    reply = cmd_sell(u, 12345, "全部")
    assert "卖出 4 件" in reply
    for nm in ("星辉圣盾", "星穹战铠×1", "月蚀吊坠×1", "星穹法印×1"):
        assert nm in reply
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == u.user_id)
    ).scalars().all()
    ids = {r.item_id for r in rows}
    assert ids == {"rare_weapon_4", "rare_shield_4", "rare_armor_4",
                   "rare_accessory_4"}


def test_sell_all_dont_keep_best_sellable_when_gear_higher(app):
    """已有更高分专属装备时，可售最高件不保留（v2.12.11）：
    饰品部位有噬星之印（专属）> 星穹法印（可售最高），星穹法印×2 全部卖出。"""
    u = _mk_user_rare_spares(app)
    reply = cmd_sell(u, 12345, "全部")
    assert "卖出 5 件" in reply
    assert "星穹法印×2" in reply
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == u.user_id)
    ).scalars().all()
    ids = {r.item_id for r in rows}
    assert ids == {"rare_weapon_4", "rare_shield_4", "rare_armor_4",
                   "gear_other_700_armor"}


def test_sell_all_keeps_best_sellable_when_no_higher_gear(app):
    """无更高分专属/锻造时，可售最高件仍保留（防卖光后无可用）。"""
    u = _mk_user(app)   # iron_sword 穿戴 + short_sword 可卖 + leather_armor 唯一可售
    reply = cmd_sell(u, 12345, "全部")
    assert "卖出 1 件" in reply and "短剑×1" in reply
    rows = db.session.execute(
        db.select(UserItem).where(UserItem.user_id == u.user_id)
    ).scalars().all()
    ids = {r.item_id for r in rows}
    assert ids == {"iron_sword", "leather_armor", "forge_iron_sword",
                   "gear_other_100_weapon"}
