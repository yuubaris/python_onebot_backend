# 地下城功能剥离评估文档（独立服务版）

> 分支：`refactor/dungeon-extract`（基于 v2.11.86 / `57e55a2`）
> 状态：**方案已定稿（评审通过）**，进入 P0 实施阶段
> 评审结论：① 独立服务；② 结算保守（命令触发，与现状一致）；③ 分支内独立组织（等同新仓库）；④ 并行维护

---

## 1. 背景与目标

当前仓库是「QQ 群地下城挂机机器人」单机单体：OneBot 消息收发、命令分发、地下城游戏逻辑、群管理、监控管理全部耦合在 23 个 `.py`（约 6,874 行）中。

**剥离目标**（已拍板）：把「地下城功能」剥离为**独立服务**（进程级隔离），QQ 消息端只做收发，游戏逻辑全部下沉到服务端。剥离后：

1. 游戏服务可独立部署、独立测试（无头运行，不依赖 QQ/WebSocket）；
2. 代码组织上**等同于新仓库**（独立目录、独立入口、独立依赖），将来可无缝拆出独立 GitHub 仓库复用；
3. 剥离期间 main 分支并行维护，分支定期合并 main 防分叉；
4. **线上行为零感知**：命令输出逐字一致、结算时机不变（命令触发）。

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

1. **结算入口挂靠在命令分发**：`dispatch_command` 开头 `if in_dungeon: settle_dungeon(user)`——任何命令都会先触发地下城结算，游戏域与命令层深度互绑（**评审决定：保留此行为，结算保守**）。
2. **命令实现与业务函数同文件**：`commands.py` 1,495 行中 15 个地下城命令实现直接调用业务模块内部函数。
3. **战报队列耦合**：`dungeon._queue_boss_report` 把 Boss 战利品排队，由命令层 `_BOSS_REPORT_COMMANDS` 决定何时播报。
4. **User 表字段混合**：社交字段（签到/群 ID/未知指令计数）与游戏字段（地下城/矿石/材料/装备/挑战）同表 40+ 列。
5. **业务模块互相引用**：`dungeon→{boss, material, ore, equipment, classes}`、`boss→dungeon`、`alchemy→{consumable, material, ore}`、`lottery→{consumable, currency, equipment, material}`。

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

### 3.1 划入「游戏服务」（随地下城剥离）

```
dungeon.py  ore.py  material.py  alchemy.py  consumable.py  lottery.py
boss.py    boss_gear.py  forge.py  classes.py  tiers.py  equipment.py  skills.py  currency.py
```

- **数据**：`User` 的游戏字段、`UserItem`、`UserOre`、`UserMaterial`、`UserConsumable`、`UserBoss`、`UserBuff`、`TurnItem`
- **入口**：`settle_dungeon`（结算）、15 个地下城命令逻辑

### 3.2 留在消息端外壳（不剥离）

- `bot.py` / `cdp.py`：协议接入
- `app.py`：事件编排、HTTP 管理 API、定时任务
- `commands.py`：**非地下城命令**（签到/余额/踢/撅/佬/帮助/排名）与分发骨架
- `kick.py`：群管理
- `dynamon.py` / `livemon.py` / `ratelimit.py` / `repeat.py`：运维监控
- `models.py`：游戏服务自持一份（见 §5.4）

---

## 4. 方案定稿：独立服务（进程级隔离）

### 4.1 总体架构

```
┌─────────────────────┐        ┌──────────────────────────┐
│  QQ 消息端（现状壳） │  HTTP  │  游戏服务 dungeon_service │
│  bot.py / app.py    │ ─────► │  Flask :8xxx（回环+Token）│
│  commands.py(非地下城)│ ◄───── │  core/loot/craft/shop/… │
└─────────────────────┘ 文本回复 │  models.py / texts.py    │
        │                       └──────────┬───────────────┘
        │ 共享 SQLite（WAL+busy_timeout）    │ 唯一 DB 写方（收敛目标）
        └────────────►  data.db ◄───────────┘
```

- **消息端收到 `/地下城` 等 15 个地下城命令** → `POST /api/game/command` → 游戏服务处理（含保守结算）→ 返回文本 → 消息端原样播报。
- **消息端非地下城命令**（签到/余额/踢/帮助/排名）：第一版仍本地处理（现状逻辑），涉及 DB 写入与游戏服务**共享 SQLite 文件**。
- **战报**：服务端结算产生的事件随 `/command` 响应带回，消息端按现有 `_BOSS_REPORT_COMMANDS` 策略播报（仅 /地下城 时展示）。

### 4.2 代码组织（等同新仓库）

评审决定 3-b：**不新建 GitHub 仓库，但在分支内把游戏服务组织成「准新仓库」**，将来可整体拎出。

```
python_onebot_backend/
├── bot.py / app.py / commands.py / kick.py / …   # 消息端壳（现状，仅命令层改委托）
├── dungeon_service/                              # ★ 准新仓库（游戏服务）
│   ├── server.py          # 服务入口（Flask，回环 + Token 鉴权）
│   ├── core.py            # 结算/推进/胜率/Boss 判定（原 dungeon.py 主体）
│   ├── loot.py            # 掉落：矿石/草药/特殊/Boss 材料/装备
│   ├── craft.py           # 炼金 + 锻造 + 使用
│   ├── shop.py            # 商店/背包/购买/出售/抽奖/转转
│   ├── profile.py         # 职业/阶级/装备评分/技能
│   ├── boss.py            # Boss 定义/专属装备/掉落表
│   ├── models.py          # 游戏域数据模型
│   ├── config.py          # 端口/Token/DB 路径/周期常量
│   ├── requirements.txt   # 独立依赖清单
│   ├── tests/             # 无头测试（内存 sqlite）
│   └── run.sh             # 独立启动脚本
├── data.db                # 共享 SQLite
└── run_all.sh             # 一键起双进程
```

**硬约束**：`dungeon_service/` 内 0 import 外壳模块（不 import app/bot/commands/kick 等）；仅依赖 `flask`、`sqlalchemy` 与标准库。

### 4.3 API 契约（第一版）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/game/command` | `{"command":"地下城","user_id":123,"args":"进入","at_qqs":[],"group_id":456}` | `{"ok":true,"reply":"…文本…","events":[…战报事件…]}` |
| POST | `/api/game/settle` | `{"user_id":123}` | `{"ok":true,"events":[…]}`（预留：时间驱动结算的扩展位，评审决定第一版不启用） |
| GET | `/api/game/health` | — | `{"ok":true,"version":"v2.11.x"}` |

- 鉴权：请求头 `Authorization: Bearer <config.token>`，仅监听 `127.0.0.1`
- 失败语义：服务异常返回 `{"ok":false,"error":"…"}`，消息端回退为现状本地提示「指令执行出错：…」，不吞异常

### 4.4 结算保守（评审决定 2）

- 服务端 `/api/game/command` 内部**先按现状逻辑结算**：若该用户 `in_dungeon`，先 `settle`（时间戳驱动、幂等），再执行命令——与 `dispatch_command` 现状逐字一致；
- 不引入服务端定时结算（时间驱动列为后续扩展位，见 §7 P3+）。

### 4.5 数据层：共享 SQLite

- **第一版**：消息端与游戏服务共享同一 `data.db` 文件；`engine` 配置 `connect_args={"timeout": 30}`（SQLite 自带 busy_timeout 语义）+ 开启 WAL（`PRAGMA journal_mode=WAL`）。
- **风险**：双进程写 SQLite，极端并发下可能 `database is locked`。机器人低频（命令/秒级）、WAL 下读写分离，冲突概率低；服务端捕获锁错误重试 1 次。
- **收敛目标（P2+）**：消息端非地下城命令也统一走服务 API，服务端成为**唯一 DB 写方**，彻底消除并发写（届时消息端零 DB 直写）。

### 4.6 并行维护（评审决定 4）

- 分支 `refactor/dungeon-extract` 每合并 main 一次：`git merge main`（或 rebase），解决冲突后跑 golden diff；
- main 上新版本（v2.11.8x+）若改动游戏域，分支同步搬移后回归；若只改外壳，直接合并。

---

## 5. 迁移策略（两阶段）

### 阶段一：包化 + 本地直连（P0~P2，行为零变化）

> 先把游戏域在**同进程内**拆成 `dungeon_service/` 包并用 GameCore 门面跑通（不立即切 HTTP），保证剥离可回退、可逐模块验证。

```python
class GameCore:
    def __init__(self, session_factory): ...
    def settle(self, user) -> list[Event]: ...
    def run_command(self, cmd, user, args, ctx) -> str | dict: ...
    def take_events(self, user_id) -> list[Event]: ...
```

- 消息端 `commands.py` 15 个地下城命令改为 `game.run_command(...)`；
- golden 快照（§6.1）逐字回归；
- 此阶段结算仍走 `dispatch_command` 里保留的调用点（保守）。

### 阶段二：进程拆分（P3，切 HTTP）

- `server.py` 起 Flask，`GameCore` 实例化到服务进程；
- 消息端 `cmd_*` 委托改为 `httpx.post("/api/game/command")`；
- `run_all.sh` 一键起双进程；`dungeon_service/run.sh` 可独立起服务。

---

## 6. 工作量与风险评估

### 6.1 工作量估算

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 回归基线：内存 sqlite + 15 命令 golden 快照 + settle 冒烟 | 0.5~1 天 |
| P1 | `dungeon_service/` 包骨架 + 纯搬移（不改逻辑，每模块跑 golden diff） | 1~2 天 |
| P2 | 命令层改 GameCore 委托 + 服务端保守结算 + WAL 配置 | 0.5~1 天 |
| P3 | `server.py` HTTP 化 + 消息端切 API + 双进程启动脚本 + 鉴权 | 1 天 |
| P4 | 非地下城命令走 API（唯一写方收敛）+ 文档三件套同步 | 1 天 |

合计约 **4~6 天**（按现状单人节奏 ~1 天/版本折算为 4~6 个小版本）。

### 6.2 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 双进程写 SQLite 锁冲突 | 高 | 第一版 WAL + timeout=30 + 锁重试 1 次；P4 收敛唯一写方根治 |
| 命令输出在重构中漂移 | 中 | golden 快照逐字 diff 拦截（P0 先行） |
| `boss→dungeon`、`dungeon→boss` 循环引用 | 中 | 迁包时先拆 `boss.py` 掉落表归属，打破环 |
| User 表字段混合误伤社交字段 | 低 | 第一版零迁移；仅按 import 归属搬代码 |
| 并行维护期间 main 改动冲突 | 中 | 定期 merge main + 冲突解决后跑 golden diff |
| 服务不可用（未启动/崩溃）时消息端表现 | 中 | 消息端捕获异常回退「指令执行出错」；health 探活 + run_all.sh 重启 |

---

## 7. 分阶段实施计划（含并行维护节奏）

1. **P0（本分支下一步）**：建立回归基线 + 无头测试脚手架（`dungeon_service/tests/`）；
2. **P1**：`dungeon_service/` 包骨架 + 纯搬移（每搬一个模块跑 golden diff）；
3. **P2**：命令委托 GameCore + 保守结算入服务 + WAL 配置；
4. **P3**：HTTP 化（server.py + 消息端切 API + 双进程脚本 + 鉴权）；
5. **P4**：唯一写方收敛 + README-dungeon-service.md + 文档三件套同步；
6. **并行维护**：main 每有新版本 → merge 到本分支 → 全量 golden diff → 继续。

---

## 8. 验收标准

- [ ] `dungeon_service/tests/` 全绿；15 个地下城命令输出与剥离前逐字一致（golden diff）；
- [ ] `dungeon_service/` 0 import 外壳模块；可独立 `python server.py` 启动（回环+Token）；
- [ ] 双进程跑通：消息端 /地下城 等命令经 API 返回与现状一致；签到/余额本地处理正常；无 `database is locked` 报错；
- [ ] 服务异常时消息端回退提示不吞异常；
- [ ] 线上 v2.11.x 行为无回归（矿石/材料/掉落/Boss/胜率口径不变）；
- [ ] README / bot_wiki / CHANGELOG 同步；`run_all.sh` / `dungeon_service/run.sh` 文档化。
