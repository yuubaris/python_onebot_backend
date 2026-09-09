# -*- coding: utf-8 -*-
"""套装效果（v2.12.1）：_set_bonus_multiplier 单元 + effective_stats 集成。

规则：
- 穿戴 4 件稀有（rare_drops，无需同系列）：全属性 ×1.1；
- 穿戴命名 Boss 专属：稀有四件套 1.1 不再计算，按件累乘——
  普通命名 Boss ×1.2/件、三大 Boss（1000/2000/3000）×1.5/件、3600 ×2/件；
- 套装倍率与称号同层（乘装备属性），属性药水点数不乘。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User, UserItem  # noqa: E402
from dungeon_service import dungeon  # noqa: E402
from dungeon_service.boss_gear import gear_layer  # noqa: E402

import json


def _load(fn):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), fn), encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return d
    for k in ("items", "equipment", "recipes"):
        if k in d:
            return d[k]
    return d


RARE = _load("rare_drops.json")
GEAR = _load("boss_gear.json")
FORGE = _load("forge.json")


def _rid(i):
    return {"id": RARE[i]["id"]}


def _fid(lv, typ):
    for r in FORGE:
        if r.get("level") == lv and r.get("type") == typ:
            return {"id": r["id"]}
    raise KeyError((lv, typ))


def _gid(layer):
    for g in GEAR:
        if int(g.get("boss_layer", 0)) == layer:
            return {"id": g["id"]}
    raise KeyError(layer)


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


# —— 单元：倍率判定 ——
def test_four_rare_11(app):
    assert dungeon._set_bonus_multiplier([_rid(i) for i in range(4)]) == pytest.approx(1.1)


def test_four_forge_11(app):
    # 4 件锻造（不同 Lv/系列）→ ×1.1，与稀有四件套同值
    worn = [_fid(1, "weapon"), _fid(2, "shield"), _fid(3, "armor"), _fid(4, "accessory")]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.1)


def test_mixed_rare_forge_no_bonus(app):
    # 2 稀有 + 2 锻造：不足 4 件同源 → 不触发
    worn = [_rid(0), _rid(2), _fid(1, "weapon"), _fid(1, "shield")]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.0)


def test_three_forge_no_bonus(app):
    worn = [_fid(1, "weapon"), _fid(1, "shield"), _fid(1, "armor")]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.0)


def test_rare_plus_common_gear_cancels_rare(app):
    # 4 稀有 + 1 普通命名专属 → 稀有 1.1 取消，只按专属 ×1.2
    worn = [_rid(i) for i in range(4)] + [_gid(100)]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.2)


def test_forge_plus_common_gear_cancels_forge(app):
    # 4 锻造 + 1 普通命名专属 → 锻造 1.1 取消，只按专属 ×1.2
    worn = [_fid(1, "weapon"), _fid(2, "shield"), _fid(3, "armor"), _fid(4, "accessory"), _gid(100)]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.2)


def test_two_common_gear_stacks(app):
    assert dungeon._set_bonus_multiplier([_gid(100), _gid(200)]) == pytest.approx(1.44)


def test_one_big_boss_15(app):
    assert dungeon._set_bonus_multiplier([_gid(1000)]) == pytest.approx(1.5)


def test_three_big_boss_stacks(app):
    worn = [_gid(1000), _gid(2000), _gid(3000)]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.5 ** 3)


def test_3600_x2(app):
    assert dungeon._set_bonus_multiplier([_gid(3600)]) == pytest.approx(2.0)


def test_mixed_gear_multiply(app):
    worn = [_gid(1000), _gid(100)]
    assert dungeon._set_bonus_multiplier(worn) == pytest.approx(1.5 * 1.2)


def test_empty_and_plain(app):
    assert dungeon._set_bonus_multiplier([]) == pytest.approx(1.0)
    assert dungeon._set_bonus_multiplier([{"id": "short_sword"}]) == pytest.approx(1.0)


def test_gear_layer_classification(app):
    assert gear_layer(_gid(1000)["id"]) == 1000
    assert gear_layer(_gid(3600)["id"]) == 3600
    assert gear_layer(_gid(100)["id"]) == 100
    assert gear_layer("not_a_gear") == 0


# —— 集成：effective_stats 应用倍率 ——
def _player(app, items, layer=0):
    u = User(user_id=91001, nickname="套装测试", profession="warrior", tier=0, copper=0,
             saved_dungeon_layer=layer)
    db.session.add(u)
    db.session.flush()
    for iid in items:
        db.session.add(UserItem(user_id=u.user_id, item_id=iid))
    db.session.commit()
    return u


def test_effective_stats_four_rare(app):
    u = _player(app, [RARE[0]["id"], RARE[2]["id"], RARE[4]["id"], RARE[6]["id"]])
    owned = dungeon.owned_items(u)
    stats = dungeon.effective_stats(u, owned)
    # 期望 = (BASE_STATS + 4 件稀有词条) × 1.1（tier0 称号=1.0）
    base = dict(dungeon.BASE_STATS)
    for i in (0, 2, 4, 6):
        for k in dungeon._ATTR_KEYS:
            base[k] += RARE[i].get(k, 0)
    for k in dungeon._ATTR_KEYS:
        assert stats[k] == pytest.approx(base[k] * 1.1)


def test_effective_stats_one_big_boss_gear(app):
    g = _gid(1000)
    u = _player(app, [g["id"]])
    owned = dungeon.owned_items(u)
    stats = dungeon.effective_stats(u, owned)
    # 期望 = (BASE_STATS + 终焉裁决词条) × 1.5（tier0，战士可用专属）
    base = dict(dungeon.BASE_STATS)
    gitem = next(x for x in GEAR if x["id"] == g["id"])
    for k in dungeon._ATTR_KEYS:
        base[k] += gitem.get(k, 0)
    for k in dungeon._ATTR_KEYS:
        assert stats[k] == pytest.approx(base[k] * 1.5)


def test_effective_stats_four_forge(app):
    items = [_fid(1, "weapon"), _fid(2, "shield"), _fid(3, "armor"), _fid(4, "accessory")]
    u = _player(app, [i["id"] for i in items], layer=3300)  # 历史层 3300 ≥ 锻造 Lv4 解锁层 3200
    owned = dungeon.owned_items(u)
    stats = dungeon.effective_stats(u, owned)
    # 期望 = (BASE_STATS + 4 件锻造词条) × 1.1（tier0 称号=1.0；战士物理线可用 weapon/shield/armor + 通用 accessory）
    base = dict(dungeon.BASE_STATS)
    for it in items:
        gitem = next(r for r in FORGE if r["id"] == it["id"])
        for k in dungeon._ATTR_KEYS:
            base[k] += gitem.get(k, 0)
    for k in dungeon._ATTR_KEYS:
        assert stats[k] == pytest.approx(base[k] * 1.1)
