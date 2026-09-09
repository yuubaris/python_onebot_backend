# -*- coding: utf-8 -*-
"""golden 回归：重放 18 个地下城命令场景，与基线逐字对比（日期归一化）。"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from scenarios import SCENARIOS, run_scenario  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")


def _normalize(text: str) -> str:
    return re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", text)


def _golden_path(name: str) -> str:
    return os.path.join(GOLDEN_DIR, f"{name}.txt")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_golden(name):
    path = _golden_path(name)
    assert os.path.exists(path), f"缺少 golden 基线 {path}（运行 regenerate.py 生成）"
    with open(path, encoding="utf-8") as f:
        expected = f.read().strip()
    actual = _normalize(run_scenario(name))
    assert actual == expected, (
        f"[{name}] 输出与基线不一致！\n"
        f"── 期望（golden）──\n{expected}\n"
        f"── 实际 ──\n{actual}\n"
        f"若为有意的行为变更，重跑 regenerate.py 并人工审阅 diff。"
    )
