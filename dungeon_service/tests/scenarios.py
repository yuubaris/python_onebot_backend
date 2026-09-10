# -*- coding: utf-8 -*-
"""P0 回归基线：无头测试脚手架（内存 sqlite + 标准玩家场景）。

目标：在剥离 dungeon_service 之前，把 15 个地下城命令的现状输出固化为
golden 快照，后续任何搬移/重构都以「逐字一致」为回归门槛。

- regenerate.py   ：固定 seed 重放场景 → 生成 dungeon_service/tests/golden/*.txt
- test_golden.py  ：pytest 重放同一场景 → 与 golden 逐字 diff（日期归一化）
- test_settle.py  ：settle 冒烟（推进/矿石/草药/战报）

场景固定原则：每个命令独立用户 + 每次固定 random.seed(SEED)，保证可复现。
"""
import random

SEED = 20260909

# 场景 A：标准战士（不在线，历史层 1200，持有一套装备/材料/矿石/药水）
def build_player_a(app_ctx):
    from models import db, User, UserItem, UserOre, UserMaterial, UserConsumable
    u = User(user_id=90001, nickname="阿甲", profession="warrior", tier=3, copper=500000,
             saved_dungeon_layer=1200, dungeon_cleared=1200)  # 已通关 1200（晋升按通关层判定 v2.12.17）
    db.session.add(u); db.session.flush()
    for iid in ("iron_sword", "leather_armor"):
        db.session.add(UserItem(user_id=u.user_id, item_id=iid))
    for mid, cnt in (("herb_bloodgrass", 10), ("special_beastsoul", 5),
                     ("special_element", 2), ("herb_dragonblood", 1)):
        db.session.add(UserMaterial(user_id=u.user_id, material_id=mid, count=cnt))
    for oid, cnt in (("copper_ore", 10), ("iron_ore", 100), ("tin_ore", 50)):
        db.session.add(UserOre(user_id=u.user_id, ore_id=oid, count=cnt))
    db.session.add(UserConsumable(user_id=u.user_id, item_id="potion_atk_1", count=2))
    db.session.commit()
    return u

# 场景 B：地下城中的法师（1500 层推进中，矿石/草药资格都在）
def build_player_b(app_ctx):
    import time
    from models import db, User, UserItem, UserOre, UserMaterial
    from dungeon import effective_layer_total
    now = time.time()
    total = effective_layer_total(1500)
    u = User(user_id=90002, nickname="阿法", profession="mage", tier=5, copper=1000000,
             dungeon_layer=1500, dungeon_progress=total * 0.5,
             dungeon_last_update=now, dungeon_coin_acc=0.0, dungeon_run_coins=12345,
             dungeon_coins_earned=99999, dungeon_cleared=1499,
             dungeon_ore_eligible=1, dungeon_ore_last=now - 2 * 900,
             dungeon_herb_eligible=1, dungeon_herb_last=now - 2 * 900)
    db.session.add(u); db.session.flush()
    for iid in ("star_sword", "star_robe"):
        db.session.add(UserItem(user_id=u.user_id, item_id=iid))
    for mid, cnt in (("herb_moonmushroom", 3), ("special_relic", 2)):
        db.session.add(UserMaterial(user_id=u.user_id, material_id=mid, count=cnt))
    db.session.commit()
    return u

# 场景 C：新人（无职业/无装备/无资产）
def build_player_c(app_ctx):
    from models import db, User
    u = User(user_id=90003, nickname="萌新", copper=0)
    db.session.add(u); db.session.commit()
    return u

# 场景清单：命令名 → (玩家构造, handler 调用参数)
# 返回文本或 dict（dict 取 text 字段）
SCENARIOS = {
    "dungeon_help":   ("A", lambda u: __import__("commands").cmd_dungeon(u, 12345, "")),
    "bag":            ("A", lambda u: __import__("commands").cmd_bag(u, 12345, "")),
    "shop":           ("A", lambda u: __import__("commands").cmd_shop(u, 12345, "")),
    "buy":            ("A", lambda u: __import__("commands").cmd_buy(u, 12345, "铁剑")),
    "sell":           ("A", lambda u: __import__("commands").cmd_sell(u, 12345, "铁剑")),
    "sell_all":       ("A", lambda u: __import__("commands").cmd_sell(u, 12345, "全部")),
    "class_":         ("A", lambda u: __import__("commands").cmd_class(u, 12345, "战士")),
    "promote":        ("A", lambda u: __import__("commands").cmd_promote(u, 12345, "")),
    "alchemy_list":   ("A", lambda u: __import__("commands").cmd_alchemy(u, 12345, "配方")),
    "alchemy_craft":  ("A", lambda u: __import__("commands").cmd_alchemy(u, 12345, "狂攻药水1")),
    "use_list":       ("A", lambda u: __import__("commands").cmd_use(u, 12345, "")),
    "lottery":        ("A", lambda u: __import__("commands").cmd_lottery(u, 12345, "")),
    "forge_shop":     ("A", lambda u: __import__("commands").cmd_forge_shop(u, 12345, "")),
    "forge":          ("A", lambda u: __import__("commands").cmd_forge(u, 12345, "断岳重剑")),
    "turn":           ("A", lambda u: __import__("commands").cmd_turn(u, 12345, "")),
    "dungeon_enter":  ("B", lambda u: __import__("commands").cmd_dungeon(u, 12345, "进入")),
    "dungeon_status": ("B", lambda u: __import__("commands").cmd_dungeon(u, 12345, "状态")),
    "challenge_list": ("B", lambda u: __import__("commands").cmd_challenge(u, 12345, "列表")),
    "dungeon_exit":   ("B", lambda u: __import__("commands").cmd_dungeon(u, 12345, "退出")),
    "checkin":        ("A", lambda u: __import__("commands").cmd_checkin(u, 12345, "")),
    "balance":        ("A", lambda u: __import__("commands").cmd_balance(u, 12345, "")),
}

PLAYERS = {"A": build_player_a, "B": build_player_b, "C": build_player_c}


def run_scenario(name):
    """重建内存库 → 构造玩家 → 跑命令 → 返回输出文本（dict 取 text）。

    注：不用 app.create_app（真实 DB 路径 + 会启动机器人线程），自建内存 Flask app。
    """
    import flask, random
    from models import db
    random.seed(SEED)
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        builder = PLAYERS[SCENARIOS[name][0]]
        u = builder(app)
        reply = SCENARIOS[name][1](u)
        if isinstance(reply, dict):
            reply = reply.get("text") or ""
        return (reply or "").strip()
