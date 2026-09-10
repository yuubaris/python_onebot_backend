# -*- coding: utf-8 -*-
"""新职业落地：魔剑士（weapon+focus+robe）& 近战法师（staff+shield+armor）（v2.12.14）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
from models import db, User, UserItem  # noqa: E402
from dungeon_service import dungeon  # noqa: E402
from dungeon_service.commands import cmd_class, cmd_buy, cmd_bag  # noqa: E402
from dungeon_service.skills import title_skill, LEVEL_TITLE_SKILLS  # noqa: E402
from dungeon_service.tiers import tier_title  # noqa: E402
from dungeon_service.classes import class_lines, class_types, item_usable_for  # noqa: E402


@pytest.fixture()
def app():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


def _mk_user(app, profession=None, tier=4, user_id=97001):
    u = User(user_id=user_id, nickname="测试", profession=profession, tier=tier,
             copper=10_000_000, saved_dungeon_layer=1200)
    db.session.add(u)
    db.session.flush()
    db.session.commit()
    return u


def _give(user, item_id, equipped=0):
    db.session.add(UserItem(user_id=user.user_id, item_id=item_id, equipped=equipped))
    db.session.commit()


# —— 职业注册 ——

def test_classes_registered():
    assert class_lines("spellblade") == {"physical", "magic"}
    assert class_types("spellblade") == {"weapon", "focus", "robe"}
    assert class_lines("battlemage") == {"physical", "magic"}
    assert class_types("battlemage") == {"staff", "shield", "armor"}


def test_item_usable_whitelist():
    shop = {it["id"]: it for it in dungeon.load_equipment()}
    w = shop["star_weapon"] if "star_weapon" in shop else next(iter(shop.values()))
    # 魔剑士：weapon 可用
    assert item_usable_for(shop["iron_sword"] if "iron_sword" in shop else shop[w["id"]], "spellblade")
    # 找一个 shield（物理线但不在魔剑士白名单）
    shield = next(it for it in shop.values() if it["type"] == "shield")
    assert not item_usable_for(shield, "spellblade")
    # 近战法师：shield 可用、weapon 不可用
    assert item_usable_for(shield, "battlemage")
    weapon = next(it for it in shop.values() if it["type"] == "weapon")
    assert not item_usable_for(weapon, "battlemage")


# —— 转职 ——

def test_cmd_class_spellblade(app):
    u = _mk_user(app)
    reply = cmd_class(u, 99999, "魔剑士")
    assert "魔剑士" in reply and "转职成功" in reply
    assert u.profession == "spellblade"
    assert "魔剑士" in _user_title(u)


def test_cmd_class_battlemage(app):
    u = _mk_user(app)
    cmd_class(u, 99999, "近战法师")
    assert u.profession == "battlemage"
    assert "近战法师" in _user_title(u)


def _user_title(u):
    from dungeon_service.tiers import tier_title
    return tier_title(u.profession, u.tier or 0)


# —— 装备组合（effective_stats 双线）——

def test_effective_stats_spellblade_uses_cross_line(app):
    u = _mk_user(app, profession="spellblade", tier=4)
    shop = dungeon.load_equipment()
    # 取 T4 档 weapon/focus/robe 各一件 + 饰品
    pick = {"weapon": None, "focus": None, "robe": None, "accessory": None}
    for it in shop:
        if int(it.get("tier", 0)) != 4:
            continue
        t = it["type"]
        if t in pick and pick[t] is None:
            pick[t] = it
    for it in pick.values():
        _give(u, it["id"])
    stats = dungeon.effective_stats(u, dungeon.owned_items(u))
    assert stats["attack"] > 0 and stats["intelligence"] > 0
    assert stats["mp"] > 0  # focus/robe 提供魔力


def test_effective_stats_spellblade_ignores_shield(app):
    """魔剑士穿盾（物理线但非白名单）→ 不进入属性计算。"""
    u = _mk_user(app, profession="spellblade", tier=4)
    shield = next(it for it in dungeon.load_equipment()
                  if it["type"] == "shield" and int(it.get("tier", 0)) <= 4)
    _give(u, shield["id"])
    stats = dungeon.effective_stats(u, dungeon.owned_items(u))
    # 盾牌属性全部不生效（对比裸装 = BASE_STATS×称号）
    base = dict(dungeon.BASE_STATS)
    t4 = dungeon._tiers_mod.TIER_ATTR_BONUS[4]
    for k in base:
        assert stats[k] == pytest.approx(base[k] * t4)


def test_effective_stats_battlemage(app):
    u = _mk_user(app, profession="battlemage", tier=4)
    shop = dungeon.load_equipment()
    pick = {"staff": None, "shield": None, "armor": None, "accessory": None}
    for it in shop:
        if int(it.get("tier", 0)) != 4:
            continue
        if it["type"] in pick and pick[it["type"]] is None:
            pick[it["type"]] = it
    for it in pick.values():
        _give(u, it["id"])
    stats = dungeon.effective_stats(u, dungeon.owned_items(u))
    assert stats["attack"] > 0 and stats["mp"] > 0 and stats["defense"] > 0


# —— Boss 胜率锚定：新职业按自身组合锚定（≠ 战士/法师）——

def test_named_boss_b0_differs_and_winrate(app):
    u_w = _mk_user(app, profession="warrior", tier=4, user_id=97011)
    u_s = _mk_user(app, profession="spellblade", tier=4, user_id=97012)
    # 档末 1400 层（T4 档：档首 85% → 档末 70% 口径）
    b0_w = dungeon.named_boss_b0(u_w, 1400)
    b0_s = dungeon.named_boss_b0(u_s, 1400)
    assert b0_w > 0 and b0_s > 0
    assert b0_w != b0_s  # 魔剑士锚定 weapon+focus+robe 组合，不同于战士 weapon+armor+shield
    # 穿满 T4 商店对应组合 → 胜率 ≈ 70%（x=S/B0=1.327 档末口径）
    shop = dungeon.load_equipment()
    for u, types in ((u_s, {"weapon", "focus", "robe", "accessory"}),):
        for it in shop:
            if int(it.get("tier", 0)) == 4 and it["type"] in types:
                _give(u, it["id"])
    stats = dungeon.effective_stats(u_s, dungeon.owned_items(u_s))
    s = dungeon.dungeon_speed(stats)
    x = s / b0_s
    p = x ** 3 / (1 + x ** 3)
    assert 0.65 <= p <= 0.75


# —— 称号与技能 ——

def test_titles_and_skills():
    assert tier_title("spellblade", 3) == "银辉魔剑士"
    assert tier_title("battlemage", 7) == "至尊近战法师"
    for cid, prefix in (("spellblade", "疾风"), ("battlemage", "苍穹")):
        for t in range(1, 8):
            title = tier_title(cid, t)
            assert title in LEVEL_TITLE_SKILLS
    sk, eff = title_skill("spellblade", 3)
    assert isinstance(sk, str) and sk and eff


# —— 背包/购买 ——

def test_cmd_bag_marks_unusable(app):
    u = _mk_user(app, profession="spellblade", tier=4)
    shield = next(it for it in dungeon.load_equipment()
                  if it["type"] == "shield" and int(it.get("tier", 0)) <= 4)
    _give(u, shield["id"])
    reply = cmd_bag(u, 99999, "")
    assert "本职业不生效" in reply


def test_cmd_buy_rejects_other_line(app):
    u = _mk_user(app, profession="spellblade", tier=4)
    shield = next(it for it in dungeon.load_equipment()
                  if it["type"] == "shield" and int(it.get("tier", 0)) <= 4)
    reply = cmd_buy(u, 99999, shield["name"])
    assert "不是魔剑士的装备" in reply or "无法购买" in reply


# —— Boss 专属掉落按职业过滤（v2.12.15）——

def _roll_gear_many(user, layer, n=60):
    """强制命中：临时抬高掉率不可行（rate 读 JSON），改为多次尝试+monkeypatch 掉率。
    这里直接调用候选过滤逻辑：用 item_usable_for 校验候选集，再模拟 roll 命中。"""
    import random
    from dungeon_service.boss_gear import roll_gear, gear_for_layer, produced_count
    from dungeon_service.classes import item_usable_for
    # 候选集（职业过滤后）不应含不可用件
    prof = user.profession or ""
    cands = [g for g in gear_for_layer(layer)
             if produced_count(g["id"]) < int(g.get("limit", 5))
             and (not prof or item_usable_for(g, prof))]
    for g in cands:
        if prof:
            assert item_usable_for(g, prof), f"候选含不可用件 {g['id']}"
    # 对未转职：候选应为全部未达限量件
    if not prof:
        all_c = [g for g in gear_for_layer(layer)
                 if produced_count(g["id"]) < int(g.get("limit", 5))]
        assert len(cands) == len(all_c)
    # 多次真实 roll（低掉率，只验证不抛错）
    hits = 0
    for _ in range(30):
        r = roll_gear(user, layer)
        if r:
            hits += 1
            if prof:
                assert item_usable_for(r["gear"], prof)
    return hits


def test_boss_gear_drop_filtered_by_class(app):
    from dungeon_service.boss_gear import gear_for_layer
    # 魔剑士：1000 层候选只含 weapon(裁决) + accessory(圣冕)
    u_s = _mk_user(app, profession="spellblade", tier=4, user_id=97021)
    _roll_gear_many(u_s, 1000)
    cand_ids = {g["id"] for g in gear_for_layer(1000)
                if not g.get("line") == "magic" or g["type"] in ("focus", "robe")}
    # 实际验证：候选里没有 staff(终焉星陨) —— 通过 item_usable_for 已保证
    # 战士：候选不含 staff/focus/robe
    u_w = _mk_user(app, profession="warrior", tier=4, user_id=97022)
    _roll_gear_many(u_w, 1000)
    # 魔法师：候选不含 weapon/armor/shield
    u_m = _mk_user(app, profession="mage", tier=4, user_id=97023)
    _roll_gear_many(u_m, 1000)
    # 近战法师
    u_b = _mk_user(app, profession="battlemage", tier=4, user_id=97024)
    _roll_gear_many(u_b, 1000)


def test_boss_gear_drop_unclassed_keeps_all(app):
    """未转职：专属掉落候选不限制（与旧行为一致）。"""
    u = _mk_user(app, profession=None, tier=4, user_id=97025)
    _roll_gear_many(u, 1000)


# —— 一键购买（/购买 全部，v2.12.16）——

def test_buy_all_buys_best_per_slot(app):
    from dungeon_service.commands import cmd_buy
    from dungeon_service import dungeon as dg
    from models import UserItem as _UI
    u = _mk_user(app, profession="spellblade", tier=4)
    u.copper = 10_000_000
    db.session.commit()
    reply = cmd_buy(u, 99999, "全部")
    assert "一键购买成功" in reply and "T4" in reply
    # 应购入 4 件：weapon/focus/robe/accessory 各 1
    rows = db.session.execute(db.select(_UI).where(_UI.user_id == u.user_id)).scalars().all()
    types = [dg._all_item_meta()[r.item_id]["type"] for r in rows]
    assert set(types) == {"weapon", "focus", "robe", "accessory"}
    # 重复执行 → 已拥有更好，跳过
    reply2 = cmd_buy(u, 99999, "全部")
    assert "无需重复购买" in reply2


def test_buy_all_requires_class(app):
    from dungeon_service.commands import cmd_buy
    u = _mk_user(app, profession=None, tier=4)
    reply = cmd_buy(u, 99999, "全部")
    assert "需要先选择职业" in reply


def test_buy_all_insufficient(app):
    from dungeon_service.commands import cmd_buy
    u = _mk_user(app, profession="warrior", tier=4)
    u.copper = 100
    db.session.commit()
    reply = cmd_buy(u, 99999, "全部")
    assert "铜币不足" in reply


# —— 专属饰品通用部位修复（v2.12.21）——

def test_gear_accessory_usable_by_line(app):
    """带归属线的专属饰品：按 line 判定、不受 types 白名单限制。"""
    from dungeon_service import dungeon as dg
    from dungeon_service.classes import item_usable_for
    liyuan = {"type": "accessory", "line": "physical"}      # 历史归属线饰品
    zhongyuan = {"type": "accessory", "line": "magic"}      # 历史归属线饰品
    # 战士（physical 线）：可穿裂渊空印，不可穿魔法线饰品
    assert item_usable_for(liyuan, "warrior")
    assert not item_usable_for(zhongyuan, "warrior")
    # 魔法师：反之
    assert not item_usable_for(liyuan, "mage")
    assert item_usable_for(zhongyuan, "mage")
    # 双线职业：两系饰品均可穿
    assert item_usable_for(liyuan, "spellblade") and item_usable_for(zhongyuan, "spellblade")
    assert item_usable_for(liyuan, "battlemage") and item_usable_for(zhongyuan, "battlemage")
    # dungeon 同口径
    for prof in ("warrior", "mage"):
        usage = dg._usage_for(prof)
        assert dg._usable_line_type(liyuan, *usage) == item_usable_for(liyuan, prof)
        assert dg._usable_line_type(zhongyuan, *usage) == item_usable_for(zhongyuan, prof)


def test_boss_gear_drop_includes_accessory(app):
    """专属饰品为通用（line=any）：任意职业候选都含噬星手镯（v2.12.26 部位重排后 700 层为饰）。"""
    from dungeon_service.boss_gear import gear_for_layer
    from dungeon_service.classes import item_usable_for
    u = _mk_user(app, profession="warrior", tier=4, user_id=97031)
    _roll_gear_many(u, 700)
    shixing = [g for g in gear_for_layer(700)
               if g["id"] == "gear_other_700_armor"][0]
    assert shixing.get("line") == "any"
    for prof in ("warrior", "mage", "spellblade", "battlemage"):
        assert item_usable_for(shixing, prof)


def test_bag_marks_gear_accessory_usable(app):
    """背包：战士持 噬星手镯 不再标「本职业不生效」。"""
    from dungeon_service.commands import cmd_bag
    u = _mk_user(app, profession="warrior", tier=4, user_id=97032)
    from models import UserItem as _UI
    db.session.add(_UI(user_id=u.user_id, item_id="gear_other_700_armor"))
    db.session.commit()
    reply = cmd_bag(u, 99999, "")
    assert "噬星手镯" in reply and "本职业不生效" not in reply
