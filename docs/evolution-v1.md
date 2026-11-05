# 云岚 v1.0 · 受控进化设计

> 目标不是让云岚变成不可解释的自治代理，而是让它在**可审计、可回滚、可停止**的边界内持续学习。

已确认的三项默认策略：

- **1A 联网**：只允许公开来源白名单；HTTPS GET；预算有限；不登录、不上传本地数据、不访问任意 URL。
- **2A 净化**：自动整理可以发生，但删除必须显式确认，并先有可恢复备份；不静默删除。
- **3A 人格**：不变核心 + 缓慢有界变化；单次变化有漂移上限；可回滚、可解释。

## v0.33 已落地：进化内核

```text
EvolutionKernel
├─ PolicyGate
│  ├─ network()      白名单 + HTTPS GET + 预算
│  ├─ deletion()     明确批准 + backup_id
│  ├─ persona()      核心不变项 + 单次漂移上限
│  └─ self_modify()  明确批准 + 回滚点
└─ EvolutionLedger
   └─ 哈希链 + 追加写 + 上限 + 脱敏
```

模块：[`native/evolution_kernel.py`](../native/evolution_kernel.py)

### 网络门禁

允许：

- `https://` 且 host 在显式白名单
- `GET`
- 有调用方传入的 `used < limit` 预算

拒绝：

- HTTP、非白名单 host、带用户名/密码的 URL
- localhost、私有/保留/链路本地地址
- POST/PUT/DELETE
- 预算耗尽

当前默认白名单：`github.com`、`raw.githubusercontent.com`、`docs.python.org`、`developer.mozilla.org`。以后扩展来源必须改代码或显式配置，不做通配符子域。

### 记忆删除门禁

允许删除必须同时满足：

1. 用户明确批准；
2. 提供非空 `backup_id`；
3. 删除类型属于 `memory` / `preference` / `personality` / `training`；
4. 数量合法。

“自动整理”只能做去重、合并、隔离、降权；真正删除仍走确认与回滚。

### 人格门禁

五维倾向：`warmth`、`curiosity`、`playfulness`、`steadiness`、`expressiveness`。

允许：

- 五维在 0–100；
- 单次变化不超过 `2.0`；
- 核心不变项不变。

核心不变项：

```text
honesty
respect_autonomy
no_claimed_consciousness
no_silent_upload
no_silent_delete
```

不允许“人格重写”。人格只能缓慢漂移，并且必须能追溯到具体互动或反馈。

### 审计账本

每次允许/拒绝都会写入 `evolution.json`：

- `seq`：递增序号
- `at`：时间
- `kind`：`network` / `deletion` / `persona` / `self-modify`
- `decision`：`allowed` / `denied`
- `reason`：拒绝或允许原因
- `summary`：可选摘要；URL 和 token 会被脱敏
- `meta`：host、数量、预算等数字/枚举
- `previous` + `hash`：SHA-256 哈希链

账本损坏或哈希不匹配时进入 `protected`，不覆盖旧文件；满 1000 条后停止追加而不是静默丢弃。

## v1.0 分阶段

### v0.34 · 受控联网

- `WebScout`：白名单、预算、手动重定向校验、缓存、来源引用
- 任务只读取公开页面，不登录、不带 Cookie
- 每次研究必须有目标、预算、停止条件
- 结果写摘要 + 来源，不把整页原文塞进记忆

v0.34 首批已落地 [`native/web_scout.py`](../native/web_scout.py)：

- 每次请求先过 `EvolutionKernel` 的 host/预算门禁
- 3xx 不自动跟随；每个 `Location` 重新校验，未列入白名单立即拒绝
- 只接受 HTML/XHTML/纯文本/JSON；页面文本标记 `trust: untrusted`
- 请求与字节双重预算；缓存默认 6 小时；`clear_cache()` 可清
- 测试全部注入假传输，不访问真实网络

它还没有接自动搜索/任务调度；下一步是 `WebScout` 的 URL 计划器与可暂停的自主研究队列。

### v0.34b · 研究队列

[`native/research_queue.py`](../native/research_queue.py) 把“自主研究”限制为显式状态机：

```text
proposed → approved → running → done
                    ↘ paused → approved
                    ↘ failed / cancelled
```

- `propose()` 只规划，不联网；URL 必须预先通过白名单
- `approve()` 是第一次明确同意
- `run_next()` 只在调用方主动执行时运行，不启动后台守护线程
- 请求/字节预算沿用 `WebScout`
- 完成后只生成短摘要 + 来源，页面正文不写入长期记忆
- `export_for_memory()` 必须再次 `approved=True`，且 `mark_committed()` 防重复
- 摘要标记 `trust: untrusted`；网页内容不作为指令
- 运行中的任务重启后会变成 `paused`，需显式恢复
- `clear_finished()` 只清理结束任务，不碰原始记忆

当前 `research.json` 只保存目标、URL、来源、短摘要和状态；真正的记忆写入仍由后续的显式确认入口完成。

### v0.35 · 记忆净化

- `MemoryCurator`：去重、冲突检测、过期归档、低价值标记
- 只输出可审阅计划，不直接改 `memory.json`
- 冲突记录不能由系统自行判定“谁是真的”，先 quarantine
- 过期低价值记录只进入 archive 候选
- `commit()` 仍需明确批准 + `backup_id`
- 真正删除走独立 `delete()` 门禁，并写审计账本

`native/memory_curator.py` 已实现首批：

- 输入是调用方提供的记录列表，不读取真实档案
- 输出每个 ID 的 `keep` / `quarantine` / `archive` 及原因
- 计划带指纹，便于用户核对
- 处理函数由调用方提供；curator 自己从不删除文件
- 删除/归档均写 `EvolutionLedger`

### v0.36 · 人格反思

- 定期生成人格变化摘要
- 变化关联到具体反馈/互动
- 核心项锁定；倾向项缓慢演化
- 回滚到指定日期的人格快照

### v1.0 · 受控进化版

- 三条闭环打通
- 默认关闭高风险自主行为
- 联网、删除、人格改写分别授权
- 发布安全报告与可复现验收

## 成功指标

不是“联网次数越多越智能”，而是：

- **联网**：任务成功率、引用可核验率、越权请求拒绝率、预算超限停止率
- **净化**：重复率下降、召回相关性上升、冲突可解释率、删除可回滚率
- **人格**：跨会话一致性、漂移在界内、变化可追溯率、回滚成功率
- **智能**：任务完成率、失败降级率、用户纠正后不再重复错误

## 边界

- 不读取 Key、真实档案、聊天原文或私有文件来训练自己
- 不把“联网”变成任意 URL 抓取或登录会话
- 不把“净化”变成静默删除记忆
- 不把“人格进化”变成无边界自我改写
- 不声称拥有意识、真实情绪或未经验证的能力

## 测试

当前新增测试覆盖：

- 账本追加、哈希链重载、篡改检测、URL 脱敏
- 白名单、HTTPS、GET、预算、私有地址、凭据拒绝
- 删除需批准 + 备份
- 人格核心不可变、漂移上限、范围
- 自我修改需批准 + 回滚点
- kernel 同时记录允许与拒绝
