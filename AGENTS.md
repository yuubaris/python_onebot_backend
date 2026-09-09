# AGENTS.md

## 分支策略（用户拍板 2026-09-09）

- **默认不把 `refactor/dungeon-extract` 分支合入 main**。仅当用户明确说「合入 main」时才执行合并（fast-forward 后推送 main）。
- 并行维护方向：main 有新提交 → `git checkout refactor/dungeon-extract && git merge main` → 跑全量测试 → 推送分支。
- 分支文档（README.md / bot_wiki.html）为**地下城聚焦版**（已移除非地下城部分：踢/撅/佬、B 站监控、复读、未知指令封禁等）；与 main 文档分叉属预期，merge 冲突时以分支版本为准。
- 分支上开发的游戏功能改动（如 v2.11.x 炼金/锻造/抽奖迭代）默认只提交分支；是否同步 main 一律等用户明确指示。

## 测试

- 全量回归：`python3 -m pytest dungeon_service/tests/ -q`（当前 39 用例；20 个 golden 逐字 diff）。
- golden 基线重生成：`python3 dungeon_service/tests/regenerate.py`（固定 seed，归一化日期），改用户可见输出后审 diff 再重生成。
- 数据文件（equipment/forge/ores/materials/bosses/consumables/alchemy_recipes/rare_drops/boss_gear json）在**仓库根**，模块按 `__file__` 向上两级定位；改动后勿改成包内路径。

## 约定

- 每次功能/数值/文档改动后：更新 CHANGELOG.md（v2.11.x 版本块）、README.md、bot_wiki.html 三件套，中文输出。
- 剥离项目文档与契约见 `docs/dungeon-extract-plan.md`、`dungeon_service/README.md`。
