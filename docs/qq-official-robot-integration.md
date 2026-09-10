# QQ 官方机器人接入指南（群聊地下城机器人）

> 一句话：把机器人从「第三方 OneBot（NapCat / Lagrange）」换成「QQ 官方机器人」，不再有封号风险、官方稳定。
> 版本：v2.0 · 2026-09-10（接口域名/鉴权已按官方文档核对）
> 官方文档：https://bot.q.qq.com/wiki/ ｜ 开放平台：https://q.qq.com

---

## 0. 先花 1 分钟看懂全貌

**要做什么**：现在机器人是靠一个"第三方 QQ 登录"（NapCat/Lagrange）跑在普通 QQ 号上；我们改成去 QQ 开放平台申请一个**官方机器人**，用官方接口收消息、发消息。

**分两大块工作**：

| 块 | 谁做 | 内容 | 大概耗时 |
|---|---|---|---|
| **平台侧** | 你（网页操作） | 注册开放平台 → 实名认证 → 创建机器人 → 拿到 AppID/AppSecret → 配沙箱群 → 提交审核上线 | 半天（含实名等待） |
| **代码侧** | 改代码 | 新增 1 个文件 `qq_official.py`（连官方、收发消息）+ 给数据库加 1 个字段 | 1~2 天 |

**最关键的一句话**：
> 所有 `/命令` 的业务逻辑（地下城/签到/锻造/炼金/抽奖/Boss…）**一行都不用改**。
> 要改的只是最外面的"收发消息"那一层——把"从 OneBot 收消息"换成"从官方收消息"。

**整体流程（照着走）**：
```
① 平台注册+实名  →  ② 创建机器人，拿 AppID/AppSecret  →  ③ 配沙箱测试群
        ↓
④ 写代码 qq_official.py（连官方 WebSocket + 调官方接口发消息）
        ↓
⑤ 沙箱群里测通所有命令  →  ⑥ 提交审核  →  ⑦ 审核通过，上线  →  ⑧ 群主把机器人拉进正式群
```

---

## 1. 先认识几个名词（后面就不绕了）

| 名词 | 白话解释 |
|---|---|
| **AppID** | 机器人的"账号"。开放平台给你的数字 ID |
| **AppSecret** | 机器人的"密码"。**保密，别外传** |
| **AccessToken** | 用 AppID+AppSecret 临时换来的"通行证"，**2 小时过期**，程序要自动续期 |
| **WebSocket** | 一条常驻"电话线"。官方通过它把群里的消息实时推给你 |
| **openid** | 官方**不给真实 QQ 号**，只给这个字符串代表"某个用户"。你的老用户数据要靠它重新绑定 |
| **group_openid** | 同上，代表"某个群"（相当于原来的群号） |
| **沙箱** | 官方给的"测试服"。只能拉进 ≤20 人的测试群，不影响正式发布、不限频 |
| **intents** | "我要订阅哪些事件"。比如"群里有人 @我"就是一个事件 |

---

## 2. 平台侧操作（照着点，共 8 步）

### 步骤 1：注册开放平台账号
1. 打开 https://q.qq.com ，用 **QQ 扫码** 或 **邮箱注册**。
2. 完成账号信息（邮箱+密码）。
3. ⚠️ 一个邮箱只能注册一个开放平台账号。

### 步骤 2：实名认证（必须做，否则机器人只能自己用）
1. 进入后按向导填 **超级管理员** 信息：姓名 + 身份证号 + 手机号 + QQ 号。
2. **三者实名必须一致**（姓名/身份证/手机号要在同一人实名下）。
3. 个人主体即可（人脸/实名验证，快）；企业主体需营业执照 + 对公打款（1~3 天）。
4. 认证通过后：个人主体机器人最多可进 **500 个群**，单个账号最多创建 **6 个机器人**。

### 步骤 3：创建机器人
1. 开放平台 → **机器人** → **我的机器人** → **创建机器人**。
2. 填头像、昵称（例：`地下城冒险`）、简介（写上支持的命令）。
3. "设置 AI 服务"那步选 **稍后连接 / 跳过**（我们是自己开发，不用 AI 托管）。
4. 创建完成，进入机器人管理页。

### 步骤 4：配置服务范围（在哪里能用）
1. 左侧 **服务范围**：
   - **群聊开关 → 开启**（"允许被添加到任意群聊"）——**这是核心，不开别人加不了**。
   - 私聊/频道：本系统只用群聊，可不关也可以关。
2. **隐私协议 → 必填并提交**（填联系邮箱，平台自动生成协议）。**不提交无法上线**。

### 步骤 5：拿到接入票据（AppID / AppSecret）
进入 **开发设置**，复制两样东西存好：

| 配置项 | 用途 |
|---|---|
| **AppID** | 机器人 ID，连接和请求都要用 |
| **AppSecret** | 机器人密钥，用来换 AccessToken |

> 说明：平台**已废弃旧的 Token 直传鉴权**，现在统一用「AppID+AppSecret 换 AccessToken」。

### 步骤 6：配沙箱测试群（不用等审核就能测）
1. 管理端 → **沙箱配置** → **QQ 群** → 选一个测试群（**必须 ≤20 人**）。
2. 手机 QQ：打开该群 → **群设置 → 群机器人 → 添加测试机器人**。
3. 沙箱里可以跑通所有功能，**不受频控限制**。

### 步骤 7：提交发布审核（上线前）
1. 管理端 → **发布设置** → 填：
   - **指令列表**（把 `/命令` 和简介填进去，见 §6）；
   - **机器人自测报告**（官方模板 xlsx）；
   - **隐私保护指引**。
2. 提交 → 官方人工审核（个人主体通常 **1~3 个工作日**）。
3. 审核通过后，点 **上线机器人**。

### 步骤 8：群主把机器人拉进正式群
1. 手机 QQ → 目标群 → **群设置 → 群机器人** → 搜索机器人昵称 → **添加**。
2. 加完后建议在群设置里把：
   - **"机器人可获取的群聊消息范围" → 选「获取群内全部消息」**
     → 这样群友直接发 `/签到`、`/地下城 进入` 就能响应，和以前一样；
   - 若不开这个，就必须 **@机器人** 才能触发命令（例：`@地下城冒险 /签到`）。

---

## 3. 代码侧要做什么（本仓库）

### 3.1 新增 `qq_official.py`（核心，就干 3 件事）

**第 1 件：换 AccessToken（每 2 小时自动续）**
```
POST https://api.bot.qq.com/app/getAppAccessToken
请求体(JSON): { "appId": "你的AppID", "clientSecret": "你的AppSecret" }
返回: { "access_token": "...", "expires_in": "7200" }   # 7200秒=2小时
```
- 程序里**定时刷新**（建议每 1.5 小时刷一次），别等过期。
- 之后所有请求都带请求头：`Authorization: QQBot {access_token}`

**第 2 件：连官方 WebSocket 收消息**
1. `GET https://api.bot.qq.com/gateway/bot`（带上面 Authorization）→ 拿到 `wss://api.bot.qq.com/websocket/`。
2. 连上后收到 `op=10`（Hello，含心跳间隔，例如 45000 毫秒）。
3. 发 `op=2`（Identify）鉴权：
   ```json
   { "op": 2, "d": { "token": "QQBot {AccessToken}",
                     "intents": 33554432, "shard": [0, 1] } }
   ```
   - `intents = 1<<25 = 33554432` = 订阅群聊/单聊事件（含"群里@我"）。
   - `shard` 分片：小机器人填 `[0, 1]` 即可。
4. 鉴权成功收到 `t=READY`（含 `session_id`）。
5. 之后按心跳间隔发 `op=1`（心跳，`d` = 收到的最新 `s` 序号），会收到 `op=11` 回应。
6. 断线重连：连上后发 `op=6`（Resume，带 `token`+`session_id`+`seq`），官方会自动补发漏掉的消息。

**第 3 件：收→转→回**
- **收**：监听事件 `GROUP_AT_MESSAGE_CREATE`（群里 @机器人 的消息）→ 拿到 `group_openid`、发送者 `openid`、文本、`msg_id`。
- **转**：把内容里的 `@机器人` 部分去掉，然后调用**现有**入口 `commands.dispatch_command(...)`（和老代码一模一样的调用）。
- **回**：把回复文本发回去：
  ```
  POST https://api.bot.qq.com/v2/groups/{group_openid}/messages
  请求体: { "content": "要发的文本", "msg_type": 0,
            "msg_id": "刚才收到的那条消息id", "msg_seq": 1 }
  ```
  - 带 `msg_id` 叫"被动回复"，最省额度（推荐都用它）。
  - `msg_seq` 是"同一条消息的第几个回复"，从 1 开始递增。

### 3.2 数据库加一个字段（关键）
官方不给 QQ 号，只给 `openid`，所以要能按 openid 找用户：

```sql
ALTER TABLE user ADD COLUMN openid TEXT;
ALTER TABLE group_whitelist ADD COLUMN group_openid TEXT;
CREATE INDEX idx_user_openid ON user(openid);
CREATE INDEX idx_gw_openid ON group_whitelist(group_openid);
```

- **新用户**：第一次发言时按 `openid` 自动建号。
- **老用户**：加一个 `/绑定 QQ号` 命令，把 openid 和已有账号对上，**历史进度不丢**（强烈建议）。

### 3.3 各文件改动一览

| 文件 | 改什么 | 量 |
|---|---|---|
| `qq_official.py` | **新增**：拿 Token + 连网关 + 收事件 + 调接口发消息 | 大（核心） |
| `models.py` | `User.openid`、`GroupWhitelist.group_openid` 字段+索引 | 小 |
| `commands.py` | 建号时支持 openid；加 `/绑定` 命令 | 小 |
| `app.py` | 启动时选择用 OneBot 还是官方（或两个一起跑）；管理页填 AppID/AppSecret | 中 |
| `bot.py` | 抽出一个通用发消息函数 `send_group_message(group, text)` | 小 |
| `ratelimit.py` | 限流的用户标识从 QQ 号改成 openid | 小 |
| 头像合成图（`kick.py` 等） | 图片改成"先上传拿 file_info，再按富媒体发" | 中 |

### 3.4 怎么验证接入成功
1. 本地写个小脚本：能换到 AccessToken、能连上网关收到 `READY` → 说明连接 OK。
2. 沙箱群里 @机器人 发 `/帮助` → 能收到回复 → 说明收发通了。
3. 沙箱群里跑一遍 `/地下城 进入` → `/签到` → `/背包` → `/抽奖 铜` → 挑战对战（3 条消息）→ 全通即达标。

---

## 4. 上线后，群里怎么用

- **群主开了"获取群内全部消息"**：直接发 `/签到`、`/地下城 进入`（跟现在一样）。
- **没开**：发 `@机器人 /签到`（代码会自动去掉 @ 部分）。

---

## 5. 必看的限制与坑

1. **默认只能收到"@机器人"的消息**。想免 @ 就用上面说的"获取群内全部消息"开关。
2. **被动回复有次数限制**：一条消息最多回 **5 次**、有效期 **5 分钟**。
   - 本系统单命令 = 1 条回复；挑战对战 = 3 条，都在限制内。
   - ⚠️ 别把一条消息的回复拆成 6 段以上。
3. **主动推送极少**：群聊**每月每群 4 条**（需群主单独开启）。本系统全是被动回复，不依赖它。
4. **图片要"富媒体"发送**：`beat.jpg` 这类合成图，得先上传拿 `file_info`，不能当普通文本发。
5. **频控（以官方文档为准）**：机器人整体约 **60 次/分钟**、**单群约 20 次/分钟**；沙箱不限。
6. **官方不给真实 QQ 号**（只有 openid）→ 老数据必须 `/绑定`，否则玩家进度会从零开始。
7. **审核注意**：抽奖/乞讨这类玩法，简介建议写中性点（"趣味抽奖""装备互助"），避免被驳回。
8. **可以双通道过渡**：OneBot 和官方机器人**同时在线共用一个数据库**，等官方稳定了再停掉 OneBot，最稳妥。

---

## 6. 指令列表（提交审核时填这个）

| 指令 | 简介 |
|---|---|
| `/地下城 进入/状态/退出/排名/列表` | 地下城挂机冒险、穿戴、Boss 挑战、本群战力排名 |
| `/签到` `/余额` `/背包` `/武器库` | 日常与装备管理 |
| `/锻造 装备名` `/分解` | 铁匠铺锻造与分解 |
| `/炼金 配方` `/使用 药水名` | 炼金药水与道具 |
| `/抽奖 铜/银/金` | 三档趣味抽奖 |
| `/挑战 @对方 或 Boss 名` | 玩家对战 / 命名 Boss 挑战 |
| `/转转 捐赠/乞讨` | 装备互助 |
| `/帮助` | 完整命令说明 |

---

## 7. 附录：技术细节（给开发看的）

### 7.1 接口域名汇总（v2.0 核对）
| 用途 | 地址 |
|---|---|
| 换 AccessToken | `POST https://api.bot.qq.com/app/getAppAccessToken` |
| 取网关地址 | `GET https://api.bot.qq.com/gateway/bot` |
| 网关 WebSocket | `wss://api.bot.qq.com/websocket/` |
| 发群消息 | `POST https://api.bot.qq.com/v2/groups/{group_openid}/messages` |
| 鉴权请求头 | `Authorization: QQBot {access_token}` |

> 接口基址是 `https://api.bot.qq.com`；沙箱环境的域名请以官方文档为准。

### 7.2 WebSocket 的 opcode 表
| op | 含义 |
|---|---|
| 0 | Dispatch（事件下发，真正的消息在这里） |
| 1 | Heartbeat（心跳） |
| 2 | Identify（首次鉴权） |
| 6 | Resume（断线恢复） |
| 7 | Reconnect（要求重连） |
| 9 | Invalid Session（鉴权无效） |
| 10 | Hello（连接成功，下发心跳间隔） |
| 11 | Heartbeat ACK（心跳回应） |

### 7.3 常用事件
| 事件 t | 含义 |
|---|---|
| `GROUP_AT_MESSAGE_CREATE` | 群里 @机器人 的消息（核心） |
| `GROUP_ADD_ROBOT` | 机器人被拉进群 |
| `GROUP_DEL_ROBOT` | 机器人被移出群 |
| `C2C_MESSAGE_CREATE` | 单聊消息（可选） |

---

## 附：官方资料链接

- QQ 开放平台：https://q.qq.com
- 起始接入（AppID/AppSecret）：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/getting-started.html
- 获取 AccessToken：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html
- WebSocket 接入（鉴权/心跳/重连）：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/reference.html
- 事件与 Intents：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html
- Python SDK（botpy，可选）：https://github.com/tencent-connect/botpy
