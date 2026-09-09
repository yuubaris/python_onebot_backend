# dungeon_service（准新仓库 · 剥离中）

地下城功能剥离的落地区：先建立无头回归基线，再逐步把游戏域搬入本目录。
**当前进度：P0~P3 全部完成，已合入 main（d301e60）。**

- 根目录 `commands.py` 的 15 个地下城命令已迁移至 `dungeon_service/commands.py`（集中命令层，业务模块不依赖它，单向无环）；根文件仅保留转发 import + 非地下城命令；
- `dungeon_service/game_core.py`：`GameCore` 门面 —— `settle`（保守：命令触发结算）/ `run_command`（地下城命令直调）/ `take_events`（取走战报队列）；
- `tests/test_p2_gamecore.py`：未知命令返回 None / 背包输出与 golden 逐字一致 / 拼音别名等价 / settle 保守结算 + 事件队列；全量 30 用例绿。

- 14 个游戏域模块 + `models` 已复制进本包，顶层/延迟 import 全部改写为包内相对导入；
- 数据文件（equipment/ores/materials/forge/consumables/bosses/boss_gear/alchemy_recipes.json）读取仓库根共享数据源（拆仓库时连同 json 一起带走）；
- `tests/test_p1_package.py`：0 外壳 import 断言 + 包内/根目录确定性函数等价 + 同 seed 随机掉落等价 + 包内 settle 冒烟。

## 测试

```bash
pip install pytest
python3 -m pytest dungeon_service/tests/ -q          # 全量回归（22 用例）
python3 dungeon_service/tests/regenerate.py          # 重生成 golden 基线（行为变更时用）
```

- `tests/golden/*.txt`：18 个地下城命令在固定场景下的现状输出快照（日期已归一化）；
- `tests/test_golden.py`：逐字 diff，剥离搬移的回归门槛；
- `tests/test_settle.py`：settle 冒烟（推进/矿石与草药互不抢占/必不掉空/战报队列）。

## 场景

| 场景 | 说明 |
|---|---|
| A 阿甲 | 战士 · tier3 · 历史层 1200 · 一套装备/材料/矿石/药水 |
| B 阿法 | 魔法师 · tier5 · 地下城 1500 层推进中 · 矿石/草药资格 |
| C 萌新 | 空号 |

## 状态

- P0 回归基线 → P1 包搬移 → P2 命令委托 + GameCore → P3 消息端切换，全部完成并合入 main；
- 20 个 golden 场景（含签到/余额）+ settle/等价/门面/dispatch 测试，39 用例全绿；
- 未来拆独立进程：按 `docs/dungeon-extract-plan.md` §4.3 HTTP 契约起 server.py，游戏域零改动（当前单进程不启用）。
