# dungeon_service（准新仓库 · 剥离中）

地下城功能剥离的落地区：先建立无头回归基线，再逐步把游戏域搬入本目录。
**当前进度：P1 包搬移完成（纯搬移，0 外壳依赖）。**

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

## 下一步（P1）

把 `dungeon.py/ore.py/material.py/alchemy.py/…` 按文档
`docs/dungeon-extract-plan.md` §4.2 迁入本目录（纯搬移，每模块跑一次 golden diff）。
