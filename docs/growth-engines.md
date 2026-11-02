# 成长 / 记忆 / 人格 引擎接口设计

本文件定义数据边界与接口。**成长引擎已实现**（`native/growth_engine.py`），
**记忆引擎已实现**（`native/memory_engine.py`）：版本化 `memory.json`、本地回顾/习惯
汇总、吸收公开学习器已校验记录、检索与清除。人格引擎尚未实现；当前“本领”页通过
`native/ability_model.py` 提供只读六维练习指数。**人格引擎已实现**
（`native/personality_engine.py`）：版本化 `personality.json`、五维倾向、语气与主动性，
由陪伴/情绪/记忆驱动本地语气和主动搭话。引擎只写自己的快照，界面只读，不直接训练
或授予权限。

## 成长引擎（已实现）

- 存档：`growth.json`（`DSH_DAFEIYU_ENGINE_FILE` 可覆盖），`version:1`，原子写入并 fsync。
- 同步：`GrowthEngine.sync(profile_state)`；XP 与日活跃来自现有档案，**不新增经验来源**；
  同输入不重写、不涨 revision。
- 派生：等级/阶段/连续天数/里程碑由引擎持有；XP 归零且档案有效时记为 reset 并清空
  引擎日账，避免残留连续天数；里程碑上限 100，日账上限 400 天。
- 保护：文件损坏、版本未知或读取失败 → `protected`，不覆盖原文件；写入失败回滚内存快照。
- 界面：概览显示“成长引擎 v1 · 同步 #N · 里程碑 M”；保护时明确提示仅显示档案数据。

## 记忆引擎（已实现）

- 存档：`memory.json`（`DSH_DAFEIYU_MEMORY_FILE` 可覆盖），`version:1`；最多 600 条、
  每条 240 字，原子写入并 fsync。
- 本地：`ingest_state(profile_state)` 自动汇总回顾与已学习惯，按文本哈希去重，重复只
  增加命中/重要度，不重复写入。
- 公开：`ingest_knowledge(records, revision)` 只吸收现有公开学习调度器**已经抓取并
  校验**的 `knowledge.public_records()`；同一 revision 不重复吸收；引擎自身不发起
  网络请求、不抓知乎、不绕过开关。
- 检索：`recall(query)` 用字符 bigram 重叠 + 重要度排序；本地聊天在“你记得…”等问句会引用
  真实偏好/习惯/回顾，公开记忆只在知识型问题且相关度足够时引用；没有记忆会直说。
- 爬虫：`public_crawler.PoetryCrawler` 只读固定 GitHub API 文件（MIT 仓库、《诗经》公版文本），
  不接收任意 URL、不跟随跨站重定向；仅在公开学习开启时每 6 小时运行一次，失败静默。
- 删除：`clear()` 清空本地与公开摘要；界面按钮不会删除成长档案、知识索引或互动记忆。
- 隐私：本地存档含回顾/习惯/公开摘要明文；不含聊天原文、API Key 或原始网页。文件损坏
  或写入失败时不假装成功。

## 原则

1. **一个引擎一个存档**：`growth.json`、`memory.json`、`personality.json`，各自带
   `version`、`revision`；迁移失败保留原文件，不静默清空。
2. **界面被动**：`GrowthDialog.update_*()` 只读快照；切换页面、刷新数据不触发引擎、
   不联网、不写入。
3. **审计只存数字与固定枚举**：不把聊天原文、文件名、记忆内容写进训练日志。
4. **权限不随成长扩大**：任何引擎都不能自行授予电脑操作、麦克风或云端上传；这些
   仍由现有会话授权和逐项确认控制。
5. **能力是练习指数**：六维分数由现有计数推导，不是能力认证、心理诊断或模型准确率。

## 输入契约

`ability_model.derive_abilities(state, behavior, training, emotion)` 当前读取：

| 引擎 | 输入 | 现有来源 |
| --- | --- | --- |
| 成长 | `xp`、`level`、`streak`、`counters`、`days` | 成长档案 / `derive()` |
| 记忆 | `learned`、`diary`、`knowledge.count`、`emotion.lexicon` | 成长卡、知识索引、情感词典 |
| 人格 | `traits`、`companion`、`emotion.mood` | 陪伴状态、情感快照 |

`ENGINE_INPUTS` 固定了三个键名；引擎可以新增自己的字段，但不要改变已有字段语义。

## 输出契约

`derive_abilities()` 返回：

```json
{
  "dimensions": [{"key": "action", "label": "行动", "value": 42,
                  "rank": "熟练", "basis": "敏捷 60 · 跑跳 8 次"}],
  "overall": 38,
  "strongest": {"key": "learning", "value": 61},
  "focus": {"key": "balance", "value": 18},
  "engine_inputs": {"growth": ["xp", "level", "..."], "memory": ["..."], "personality": ["..."]},
  "kind": "practice_index_not_certification"
}
```

- 所有分数为 0–100 的整数，越界截断；空数据为 0，不抛异常。
- 结果必须确定：同一输入同一输出，不依赖时间、随机数或网络。
- 引擎写入失败时保留上一版快照；界面显示“只读/受保护”而不是假装成功。

## 后续接入顺序

1. **成长引擎**：先接管 XP/等级/连续天数与阶段，输出与现有 `derive()` 相同字段，保证
   旧界面兼容；再由能力模型读取。
2. **记忆引擎**：管理“记住的日常/回顾/知识索引”，默认只存摘要与数字；原文是否保留
   必须单独开关，且不进训练日志。
3. **人格引擎**：接管模拟心情/语气/兴趣倾向；标签仍标明“模拟”，不得声称真实意识，
   不得用依恋话术提高留存。
4. 三个引擎完成后，把 `update_state/update_memory/update_personality` 接到现有
   `GrowthDialog`；`ability_model` 不需要改页面，只增加输入来源。

## 验证要求

- 每个引擎都要有独立测试：空数据、损坏存档、迁移失败、越界数值、确定性。
- 不得新增联网/权限副作用；`test_growth_design` 的“切页不发信号”必须继续通过。
- 能力页测试：六维数量、0–100 边界、空数据为 0、雷达/卡片与模型输出一致。
