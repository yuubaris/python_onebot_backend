# -*- coding: utf-8 -*-
"""生成 golden 快照：python3 dungeon_service/tests/regenerate.py

固定 seed 重放全部场景，把输出写入 dungeon_service/tests/golden/<name>.txt。
首次运行即建立回归基线；重构后若需更新基线，重新运行本脚本并人工审阅 diff。
"""
import os
import sys
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 仓库根
from scenarios import SCENARIOS, run_scenario  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")


def normalize(text: str) -> str:
    """归一化可变 token：日期 → <DATE>（其余输出应完全确定）。"""
    return re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", text)


def main():
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    for name in sorted(SCENARIOS):
        text = normalize(run_scenario(name))
        path = os.path.join(GOLDEN_DIR, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"✓ {name}  ({len(text)} 字符)")
    print(f"\n共 {len(SCENARIOS)} 个场景 → {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
