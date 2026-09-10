# -*- coding: utf-8 -*-
"""四大 Boss 专属特殊道具（v2.12.4）：1000/2000/3000 各新增一个，与 3600 万瓜圣契同类、极低掉率一致。"""
import json
import os

import pytest

sys_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys  # noqa: E402
sys.path.insert(0, sys_path)

from dungeon_service import boss  # noqa: E402


def _materials():
    with open(os.path.join(sys_path, "materials.json"), encoding="utf-8") as f:
        return json.load(f)["materials"]


def test_four_big_bosses_have_souvenir():
    """1000/2000/3000/3600 各有专属特殊道具，且材料表齐全、category=souvenir。"""
    mats = _materials()
    by_id = {m["id"]: m for m in mats}
    assert set(boss.SOUVENIR_LAYERS) == {1000, 2000, 3000, 3600}
    for layer in (1000, 2000, 3000, 3600):
        mid = boss.SOUVENIR_LAYERS[layer]
        m = by_id.get(mid)
        assert m is not None, f"材料 {mid} 缺失"
        assert m["category"] == "souvenir"
        assert m["rarity"] == "myth"


def test_souvenir_low_drop_rate():
    """专属特殊道具为极低掉率（与 3600 万瓜圣契一致）。"""
    assert 0 < boss.SOUVENIR_DROP_RATE <= 0.2
    # 3600 万瓜圣契与新材料共用同一掉率
    assert boss.SOUVENIR_LAYERS[3600] == boss.FINAL_BOSS_RELIC


def test_myriad_gem_still_defined():
    """万宝源晶（万宝符原料，四大大 Boss 必掉）不受影响。"""
    mats = _materials()
    assert boss.MYRIAD_GEM in {m["id"] for m in mats}
