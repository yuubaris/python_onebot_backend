# -*- coding: utf-8 -*-
"""P1 验证：dungeon_service 包与根目录业务模块等价（纯搬移门槛）。

- 0 import 外壳（app/commands/bot/kick）；
- 关键业务函数：包内 == 根目录（同 seed / 同输入）；
- 包内 settle 冒烟可独立跑通。
"""
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import flask  # noqa: E402
import dungeon  # noqa: E402
import material  # noqa: E402
import ore  # noqa: E402
import alchemy  # noqa: E402

import dungeon_service.dungeon as sd  # noqa: E402
import dungeon_service.material as sm  # noqa: E402
import dungeon_service.ore as so  # noqa: E402
import dungeon_service.alchemy as sa  # noqa: E402
from dungeon_service.models import db as sdb, User  # noqa: E402


def test_no_shell_import():
    """包内模块不得 import 外壳(循环依赖防线)。"""
    import re as _re
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shell_pat = _re.compile(r"^\s*(import|from)\s+(app|commands|qq_official|logutil)\b", _re.M)
    checked = 0
    for fn in sorted(os.listdir(pkg_dir)):
        if not fn.endswith(".py") or fn in ("__init__.py", "models.py"):
            continue
        src = open(os.path.join(pkg_dir, fn), encoding="utf-8").read()
        m = shell_pat.search(src)
        assert m is None, f"{fn} 引用外壳: {m.group(0).strip()}"
        checked += 1
    assert checked >= 15, f"扫描文件数异常: {checked}"


def test_deterministic_functions_equal():
    assert sd.layer_total(1234) == dungeon.layer_total(1234)
    assert sd.boss_type(1000) == dungeon.boss_type(1000)
    assert sd.coin_per_5sec(800) == dungeon.coin_per_5sec(800)
    eq = {"id": "iron_sword", "stats": {"attack": 64, "agility": 21, "hit": 36, "defense": 14}}
    assert sd.item_score(eq) == dungeon.item_score(eq)
    assert sa.find_recipe("狂攻")["name"] == alchemy.find_recipe("狂攻")["name"]


def test_random_drops_equal_same_seed():
    random.seed(42)
    a = [sm.roll_material(2000) for _ in range(20)]
    random.seed(42)
    b = [material.roll_material(2000) for _ in range(20)]
    assert a == b
    random.seed(7)
    c = [so.pick_ore(2000) for _ in range(20)]
    random.seed(7)
    d = [ore.pick_ore(2000) for _ in range(20)]
    assert c == d


def test_package_settle_smoke():
    app = flask.Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    sdb.init_app(app)
    with app.app_context():
        sdb.create_all()
        now = time.time()
        u = User(user_id=1, nickname="测", profession="warrior", tier=6, copper=10 ** 9,
                 dungeon_layer=3200, dungeon_progress=10 ** 12, dungeon_last_update=now,
                 dungeon_ore_eligible=1, dungeon_ore_last=now - 2 * 900,
                 dungeon_herb_eligible=1, dungeon_herb_last=now - 2 * 900)
        sdb.session.add(u)
        sdb.session.commit()
        sd.settle_dungeon(u)
        assert u.dungeon_layer == 3200
        assert sum(o["count"] for o in so.owned_ores(1)) > 0
        assert sum(m["count"] for m in sm.owned_materials(1)) > 0
