# 地下城功能剥离评估文档（草案）

> 分支：`refactor/dungeon-extract`（基于 v2.11.86 / `57e55a2`）
> 状态：**评估草案，待评审**——本阶段只产出方案与边界，不动业务代码。

---

## 1. 背景与目标

当前仓库是「QQ 群地下城挂机机器人」单机单体：OneBot 消息收发、命令分发、地下城游戏逻辑、群管理、监控管理全部耦合在 23 个 `.py`（约 6,874 行）中。

**剥离目标**（用户诉求）：把「地下城功能」从消息/协议/管理壳中剥离出来，使其：

1. 业务逻辑可独立维护、独立测试（无头运行，不依赖 QQ/WebSocket）；
2. 边界清晰，后续可复用到其他机器人平台（QQ 官方机器人、微信、Telegram 等）；
3. 剥离过程不破坏现有线上行为（v2.11.x 继续可用）。

**本阶段交付**：剥离方案 + 边界 + 工作量/风险评估，**不含代码改动**。评审通过后再分阶段实施。

---

## 2. 现状架构

### 2.1 分层

| 层 | 文件 | 职责 |
|---|---|---|
| 协议/接入 | `bot.py`(184) / `cdp.py`(122) | OneBot 11 正向 WebSocket 客户端；CDP 接入 |
| 应用/编排 | `app.py`(762) | Flask 应用、事件处理 `_handle_event_inner`、命令分发调用、HTTP 管理 API、定时任务 |
| 命令层 | `commands.py`(1,495) | `COMMANDS` 表 + `dispatch_command` + 全部命令实现（41 个命令含别名） |
| **游戏域** | `dungeon.py`(995)、`ore.py`(186)、`material.py`(196)、`alchemy.py`(200)、`consumable.py`(370)、`lottery.py`(214)、`boss.py`(417)、`boss_gear.py`(77)、`forge.py`(115)、`classes.py`(117)、`tiers.py`(142)、`equipment.py`(57)、`skills.py`(75)、`currency.py`(54) | 地下城结算/挑战/Boss/矿石/材料/炼金/抽奖/锻造/职业/装备 |
| 数据层 | `models.py`(213) | 15 张表（SQLAlchemy） |
| 群管理 | `kick.py`(189) | /踢 等群管理 |
| 运维监控 | `dynamon.py`(376)、`livemon.py`(195)、`ratelimit.py`(58)、`repeat.py`(65) | 动态监控、直播监控、限流、复读 |

### 2.2 关键耦合点

1. **结算入口挂靠在命令分发**：`dispatch_command` 开头 `if in_dungeon: settle_dungeon(user)`——任何命令都会先触发地下城结算，游戏域与命令层深度互绑。
2. **命令实现与业务函数同文件**：`commands.py` 1,495 行中 15 个地下城命令实现直接调用业务模块内部函数（如 `dungeon.settle_dungeon`、`dungeon.mark_best_equipped`、`material.roll_material` 等）。
3. **战报队列耦合**：`dungeon._queue_boss_report` 把 Boss 战利品排队，由命令层 `_BOSS_REPORT_COMMANDS` 决定何时播报（v2.11.81 已收窄到仅 /地下城）。
4. **User 表字段混合**：社交字段（签到/群 ID/未知指令计数）与游戏字段（地下城/矿石/材料/装备/挑战）同表 40+ 列，剥离后需决定表拆分或字段归属。
5. **业务模块互相引用**：`dungeon→{boss, material, ore, equipment, classes}`、`boss→dungeon`、`alchemy→{consumable, material, ore}`、`lottery→{consumable, currency, equipment, material}`——需先确定依赖方向再切。

### 2.3 地下城命令清单（15 个核心）

| 命令 | 入口 | 涉及模块 |
|---|---|---|
| /地下城 | `cmd_dungeon` | dungeon、ore、material、boss |
| /挑战 | `cmd_challenge` | boss、dungeon、skills |
| /boss | `cmd_boss` | boss、dungeon |
| /炼金 | `cmd_alchemy` | alchemy、consumable、material、ore |
| /使用 | `cmd_use` | consumable |
| /抽奖 | `cmd_lottery` | lottery、consumable、currency、equipment、material |
| /背包 | `cmd_bag` | equipment、ore、material、consumable、boss_gear |
| /武器库 | `cmd_shop` | dungeon、equipment |
| /购买 | `cmd_buy` | dungeon、equipment |
| /出售 | `cmd_sell` | dungeon、equipment |
| /转职 | `cmd_class` | classes、dungeon |
| /晋升 | `cmd_promote` | tiers、dungeon |
| /铁匠铺 | `cmd_forge_shop` | forge、dungeon |
| /锻造 | `cmd_forge` | forge、dungeon |
| /转转 | `cmd_turn` | models（TurnItem） |

---

## 3. 剥离边界定义

### 3.1 划入「游戏域」（随地下城剥离）

```
dungeon.py  ore.py  material.py  alchemy.py  consumable.py  lottery.py
boss.py    boss_gear.py  forge.py  classes.py  tiers.py  equipment.py  skills.py  currency.py
```

- **数据**：`User` 的游戏字段、`UserItem`、`UserOre`、`UserMaterial`、`UserConsumable`、`UserBoss`、`UserBuff`、`TurnItem`
- **入口**：`settle_dungeon`（结算）、`dispatch_command` 中 15 个地下城命令逻辑

### 3.2 留在外壳（不剥离）

- `bot.py` / `cdp.py`：协议接入
- `app.py`：事件编排、HTTP API、定时任务（保留对游戏域的调用）
- `commands.py`：命令**分发骨架**保留，地下城命令 handler 改为委托
- `kick.py`：群管理
- `dynamon.py` / `livemon.py` / `ratelimit.py` / `repeat.py`：运维监控
- `models.py`：保留（游戏域可依赖同一 models，或拆 `models_game.py`——见方案）

---

## 4. 候选方案对比

### 方案 A：同仓库包化（推荐起步）

把游戏域整体迁移到独立包目录，通过**统一接口**（`GameCore`）暴露，命令层只调接口。

- 包结构：`dungeon_system/`（含 `core.py`、`settle.py`、`loot.py`、`craft.py`、`shop.py`、`models.py`）
- 命令层：`commands.py` 只保留文本解析，调用 `dungeon_system` 接口
- 优点：改动可控、风险低、可立即获得无头测试能力
- 缺点：仍是同仓库同进程，物理上未与消息层隔离

### 方案 B：独立 Python 库（同仓库 `lib/` 或独立仓库）

游戏域打成无 UI 依赖的库（只依赖 SQLAlchemy + json），任意机器人平台可复用。

- 在方案 A 基础上，把包对外依赖收敛为 0（不再 import app/commands/bot）
- 优点：平台无关、可单测、可发布
- 缺点：一次性收敛依赖工作量大（当前游戏域内部引用较散）

### 方案 C：独立服务（微服务，进程/网络隔离）

游戏域独立进程，通过 HTTP/gRPC 与消息服务通信。

- 优点：最强隔离、可横向扩展
- 缺点：改造最大（DB 共享或拆分、RPC 契约、部署拓扑），**对单人 QQ 群机器人过度设计**，不推荐近期实施

### 推荐

**A → B 两步走**：先在同仓库内完成「包化 + 接口化 + 无头测试」（方案 A，1~2 个版本周期），稳定后再收敛依赖为纯库（方案 B）。方案 C 记录在案，作为多平台扩展时的升级路径。

---

## 5. 推荐方案（A）详细设计

### 5.1 目标包结构

```
dungeon_system/
├── __init__.py          # GameCore 门面：对外唯一入口
├── core.py              # 结算/推进/胜率/Boss 判定（原 dungeon.py 主体）
├── loot.py              # 掉落：矿石/草药/特殊/Boss 材料/装备（ore/material/boss 掉落部分）
├── craft.py             # 炼金 + 锻造 + 使用（alchemy/forge/consumable）
├── shop.py              # 商店/背包/购买/出售/抽奖/转转（equipment/lottery/turn）
├── profile.py           # 职业/阶级/装备评分/技能（classes/tiers/equipment/skills）
├── boss.py              # Boss 定义/专属装备/掉落表（boss/boss_gear）
├── models.py            # 游戏域数据模型（迁移自 models.py 游戏部分）
└── texts.py             # 全中文提示文案收敛（可选，后期做 i18n 用）
```

### 5.2 统一接口（GameCore 门面）

```python
class GameCore:
    def __init__(self, session_factory): ...      # 注入 DB 会话，不 import flask
    def settle(self, user) -> list[Event]:        # 结算：推进/掉落/Boss，返回事件
    def run_command(self, cmd: str, user, args, ctx) -> str | dict:
        # 地下城命令统一入口（文本输出与现在保持逐字一致，保证线上无感知）
        ...
    # 事件：掉落、战报、Buff 到期 → 由外壳决定如何播报
    def take_events(self, user_id) -> list[Event]: ...
```

**关键约束**：`GameCore.run_command` 返回的文本与现状**完全一致**（回归基准），外壳只负责收发与播报策略。

### 5.3 命令层改造

- `commands.py` 保留 `COMMANDS` 表与 `dispatch_command` 骨架；
- 15 个地下城命令 handler 改为 `return game.run_command(name, user, args, ctx)`；
- 非地下城命令（签到/余额/踢/撅/佬/帮助/排名）留在 commands.py；
- `settle_dungeon` 从 `dispatch_command` 中移除，改由 `app.py` 在事件循环/定时任务中调用 `game.settle`（**行为变化点：结算时机从「命令触发」改为「时间驱动」**——需回归验证掉落节奏一致）。

> ⚠️ 结算时机改动是唯一的行为敏感点。可选的保守策略：第一版仍保留「命令触发结算」入口（`game.settle` 幂等，时间戳驱动），时间驱动作为第二阶段优化。

### 5.4 数据层

- 保守方案：游戏域复用现有 `models.py`（同 DB、同表），只做**代码归属**调整，零迁移；
- 进阶方案：拆 `models_game.py` + 迁移脚本（把 `User` 表游戏字段拆到 `user_game` 表，`user.user_id` 1:1 关联）；
- 推荐：**第一版保守（零迁移）**，拆分表留待多平台版再做（涉及 `_migrate_schema` 逻辑改造，风险高、收益低）。

### 5.5 无头测试基建

```bash
pytest tests/
# 用内存 sqlite 起 GameCore，直接调 run_command / settle，断言输出与掉落
```

- 先行补齐回归基线：现状 15 个地下城命令在内存库上的输出快照（golden file）；
- 剥离后跑同一快照 diff，保证文本逐字一致；
- 掉落类（矿石/材料/抽奖）用固定 seed 做分布断言。

---

## 6. 工作量与风险评估

### 6.1 工作量估算（单人，按现有提交节奏 ~1 天/版本）

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 回归基线（内存库 + 15 命令 golden 快照 + settle 冒烟） | 0.5~1 天 |
| P1 | 包骨架 + GameCore 门面 + 模块迁移（纯搬移，不改逻辑） | 1~2 天 |
| P2 | 命令层改委托 + 结算入口收敛 | 0.5 天 |
| P3 | 依赖收敛（游戏域 0 import 外壳）→ 方案 B | 1~2 天 |
| P4 | 文档/README/wiki/CHANGELOG 同步 | 0.5 天 |

### 6.2 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 结算时机从「命令触发」改为「时间驱动」，掉落节奏变化 | 高 | 第一版保留命令触发结算；时间驱动仅作可选优化并做 48h 双跑对比 |
| `boss→dungeon`、`dungeon→boss` 循环引用 | 中 | 迁包时先拆 `boss.py` 的掉落表与结算函数归属，打破环 |
| User 表字段混合，误伤社交字段 | 低 | 第一版零迁移；仅按 import 归属搬代码 |
| 命令文本在重构中漂移 | 中 | golden 快照逐字 diff 拦截 |
| 线上 2 个分支并行（v2.11.x 主分支持续修 bug） | 中 | 剥离分支定期从 main rebase；合并时按文件 diff 走 |

---

## 7. 分阶段实施计划

1. **P0（本分支下一步）**：建立回归基线 + 无头测试脚手架；
2. **P1**：`dungeon_system/` 包骨架 + 纯搬移（行为零变化，每搬一个模块跑一次 golden diff）；
3. **P2**：命令委托 + 结算入口收敛（保守版）；
4. **P3**：依赖收敛为纯库（方案 B），输出 `README-dungeon-core.md` 说明如何在任意平台接入；
5. **P4**：文档三件套同步，合并回 main 时按小版本逐版推进。

---

## 8. 验收标准

- [ ] `pytest tests/` 全绿；15 个地下城命令输出与剥离前逐字一致（golden diff）；
- [ ] `GameCore` 可在无 Flask/websocket 环境下独立实例化运行（内存 sqlite）；
- [ ] 游戏域模块 0 import `app/commands/bot/cdp`（方案 B 完成后）；
- [ ] 线上 v2.11.x 行为无回归（矿石/材料/掉落/Boss/胜率口径不变）；
- [ ] README / bot_wiki / CHANGELOG 同步。

---

## 9. 待确认问题（评审点）

1. 「剥离」目标是**结构解耦**（方案 A/B，同进程）还是**独立服务**（方案 C）？——默认按 A→B。
2. 结算时机是否允许改为时间驱动？——默认保守（保留命令触发）。
3. 是否要求游戏域可脱离本仓库单独发布（独立 pip 包/仓库）？——影响 P3 范围。
4. 剥离期间主分支（v2.11.x）是否继续并行维护修 bug？——影响合并策略。
