# H1 Memory v0 — Lifecycle

## Session 的定义

一个 session = **一次 lelamp agent 进程的活跃寿命**，而不是"一次对话"。

- session 开始：`smooth_animation.py` 的 `entrypoint` **进入后的第一步**，在任何硬件 / arbiter 初始化**之前**；此时 writer 立刻做 "index 自检"（见下文）并写 `sessions/*.meta.json` **骨架**（`flags.motor_bus_enabled = null`，表示"尚未确定"）
- arbiter 裁定：`MotorBusServer.start()` 返回后（无论成功 / 失败 / 放弃），同一个 writer **第二次** tmp+rename 更新同一份 `meta.json`，把 `flags.motor_bus_enabled` 设为 `true` / `false`
- session 结束：进程收到 `SIGTERM` / `SIGINT` / 正常退出时的 `atexit` 钩子
- session 之间的间隔（lelamp 没在跑）**不算** agent session

> **重要（与 H0 的解耦）**：session 开始 **不** 依赖 MotorBusServer 是否成功 `start()`。
> 即使 arbiter bind retry 失败、串口被占、`AnimationService.start()` 抛错，session 仍然正常开始；
> 这些硬件状态只**延后**由同一 writer 做第二次 `meta.json` tmp+rename 写入 `flags.motor_bus_enabled`，不影响 memory 层是否记录事件。
> 这保证了 "记忆层与硬件仲裁层独立" 的合同（见 `README.md` §"与 H0 的关系"）；具体两阶段写入语义见 §"`*.meta.json`"。

### 为什么是"一次进程"而不是"一次对话"

- lelamp 在 Pi 上是 systemd 常驻；一次物理坐到台灯前和下次坐，之间进程没重启 → 属于同一个 session
- "一次对话"的切分需要 VAD 语义 / 心跳超时；v0 不做这个判断，**物理进程边界就是最好的自然切分**
- 答辩场景：一次 demo 从开机到关机 = 一个 session，定义清晰好讲

### session_id 生成

```
sess_<YYYY-MM-DD>_<HH-MM-SS>          # agent 进程产出
sess_manual_<YYYY-MM-DD>_<HH-MM-SS>   # dashboard / remote_control 独立产出
例：sess_2026-04-17_23-11-15
例：sess_manual_2026-04-17_09-32-08
```

- 用本地时区；因为答辩现场演示时，用户的语义时间就是本地时间
- 精度到秒即可；同一秒启动两个进程是病态场景（systemd 不会这么干）
- `sess_manual_` 前缀专门用于区分"没有 agent 进程在跑"时由 dashboard / remote_control 产生的事件

---

## Agent session vs manual session —— dashboard / remote_control 的 session_id 归属

v0 的 session 概念是"**一次进程的活跃寿命**"；但 dashboard 和 `remote_control` 是**独立进程**，它们产生的 `playback` 事件也必须有 `session_id`（schema 强约束）。分三种场景，**规则显式**：

### 场景 A：agent 正在跑，dashboard / CLI 随附

最常见场景：`lelamp.service` 在跑，用户同时在 dashboard 或 terminal 里触发一个 `play`。

- dashboard / remote_control writer **依附**到 agent 当前 session：
  1. 扫 `sessions/*.meta.json` 按 `start_ts_ms` 降序
  2. 取最新那条，读 `pid` 字段，用 `os.kill(pid, 0)` 判活
  3. 进程存活 → 用它的 `session_id`（**不新建 manual session**）
- 写入时和 agent writer 走**同一把 flock**，不会冲突

这样 dashboard 点播的 playback 和 agent 本身记录的事件会并排出现在 `events.jsonl` 里，时间戳自然排序，答辩时一目了然。

### 场景 B：agent 没在跑，只有 dashboard / CLI

例如 `sudo systemctl stop lelamp`，然后 `python -m lelamp.remote_control play curious` 做单独硬件调试。

- dashboard / remote_control writer 检测不到存活 agent session → **新建一个 manual session**：
  - `session_id = sess_manual_<YYYY-MM-DD>_<HH-MM-SS>`
  - 写 `sessions/<session_id>.meta.json`：

    ```json
    {
      "schema": "lelamp.memory.v0.session_meta",
      "session_id": "sess_manual_2026-04-17_09-32-08",
      "user_id": "default",
      "start_ts_ms": 1776432728000,
      "start_ts_iso": "2026-04-17T09:32:08+08:00",
      "timezone": "Asia/Shanghai",
      "pid": null,
      "git_ref": null,
      "model_providers": [],
      "flags": {
        "motor_bus_enabled": null,
        "fluxchi_enabled": false,
        "source": "standalone_writer"
      }
    }
    ```

  - `pid=null` 和 `flags.source="standalone_writer"` 是 manual session 的**区分标记**；`flags.motor_bus_enabled = null` 表示"manual session 不对 arbiter 做裁定"。summary 仍可生成，但 `recent_index` / prompt 读路径会按 `sess_manual_*` 一律过滤
- manual session 的 `summary.json` 在**下一个任意 writer 启动自检**里补写（writer 自检发现"有 meta 无 summary"→ 扫事件补 summary，对 manual session 同样适用）

### 场景 C：dashboard 和 CLI 同时并发，都没有 agent

极少见。两个独立进程都尝试新建 manual session。**v0 明确：不做 manual-to-manual attach**——两个并发 standalone writer **各自建独立** `sess_manual_<ts>`：

- 秒级时间戳天然错开（`sess_manual_YYYY-MM-DD_HH-MM-SS`）
- flock 只保证"写 meta.json 的 tmp+rename 不交错"，不作为 session 依附依据
- 极端同秒冲突：按 OQ-2 定的规则给第二个 manual 加 `-1 / -2` 后缀

**为什么不做 manual-to-manual attach**：manual session 的 owner 进程通常是短命的（`remote_control play curious` 跑几秒就退出），引入"manual 也要判活"等于要给每个 manual meta 加 pid 字段 + liveness 心跳，复杂度远超带来的价值。两条独立 `sess_manual_` 并不会污染 prompt 读路径（见 §"Recent Window" 的过滤规则），且事件仍然全部保留在 `events.jsonl`。

### 归属判活的小工具函数（规格）

writer 模块必须提供一个纯函数，仅对 **agent session** 做依附判定：

```
attach_or_create_session() -> (session_id, is_manual)
  1. 扫 sessions/*.meta.json，按 start_ts_ms 降序
  2. 逐条只看 agent session（flags.source != "standalone_writer" 且 pid 非空）：
       pid 存活？→ 返回 (session_id, False)   # 场景 A
  3. 扫完没找到存活 agent → 分配新的 sess_manual_<ts>，写 meta（pid=null），
     返回 (session_id, True)                   # 场景 B / C
  操作全程在 flock 保护下；步骤 2 **不**把 manual session 当作依附目标
```

所有 dashboard / remote_control 的事件写入**必须**走这个函数拿到的 `session_id`，不得自造。**不存在** "manual 依附到 manual" 路径。

---

## Session 生命周期中的三种文件

```
sessions/sess_2026-04-17_23-11-15.meta.json         # session 开始时写
sessions/sess_2026-04-17_23-11-15.summary.json      # session 结束时写
events.jsonl                                        # 整个 session 的事件 inline
```

### `*.meta.json`

v0 的 agent session 下 `meta.json` 允许被 writer **两阶段**写入；每一阶段都是独立的 **原子** tmp+rename。manual session 只有一次写入（第一阶段终态）。

#### 阶段 1（session 启动瞬间，flock 下）

内容 = **不依赖硬件**的字段 + `flags.motor_bus_enabled: null`：

```json
{
  "schema": "lelamp.memory.v0.session_meta",
  "session_id": "sess_2026-04-17_23-11-15",
  "user_id": "default",
  "start_ts_ms": 1776438675000,
  "start_ts_iso": "2026-04-17T23:11:15+08:00",
  "timezone": "Asia/Shanghai",
  "pid": 9551,
  "git_ref": "cf5a692",
  "model_providers": ["qwen"],
  "flags": {
    "motor_bus_enabled": null,
    "fluxchi_enabled": false
  }
}
```

`null` 是**显式**语义："arbiter 还没裁定" —— reader 要能和 `true` / `false` 一视同仁处理（见下面的 reader 容忍契约）。

#### 阶段 2（`MotorBusServer.start()` 返回后，同 writer，flock 下）

writer 做一次 read → in-memory patch → tmp+rename：

```
1. flock(global .lock, LOCK_EX)
2. read sessions/<session_id>.meta.json  (阶段 1 落盘结果)
3. patch["flags"]["motor_bus_enabled"] = True  # 或 False
4. atomic tmp+rename 覆盖
5. release flock
```

**写入触发**：

| arbiter 返回情况 | `flags.motor_bus_enabled` 阶段 2 的值 |
|---|---|
| `MotorBusServer.start()` 正常返回且 `is_ready()` = True | `true` |
| bind retry 最终失败 / `server not started` | `false` |
| `AnimationService.start()` 抛错 → 整个 arbiter 启动被跳过 | `false` |
| 进程在阶段 1 之后、阶段 2 之前被 SIGKILL | 阶段 2 永远不执行，`flags.motor_bus_enabled` 停留在 `null`（下次 writer 启动自检时**不回填**，因为这是历史事实） |

#### 单 writer 保证

meta.json 的所有写入 **仅由拥有 session 的那个 writer 发出**：
- agent session → `smooth_animation.py` 进程
- manual session → 创建它的 dashboard / remote_control 进程
没有任何"另一个进程补写别人家 meta.json"的路径（writer 启动自检只**补 summary**，不**改 meta**；见 §"容错与恢复"）。因此阶段 1 ↔ 阶段 2 之间不存在跨进程 read-modify-write 竞争，flock 仅防"同一个 writer 的两次 tmp+rename 和其它 writer 的 append 交错"。

#### Reader 容忍契约

任何消费 `meta.json` 的 reader 必须同时接受三种 `flags.motor_bus_enabled` 取值：

- `true` — arbiter ready；硬件动作事件（function_tool / playback）可期望 ok=true
- `false` — arbiter 明确不可用；事件层可能出现 `function_tool.result.ok=false` 为常态，不视为异常
- `null` — arbiter 裁定前崩溃；按"未记录"处理，**不**触发任何重建 / 回填

#### Manual session 的差异

dashboard / remote_control 独立运行时产生的 `sess_manual_*` **不走 arbiter**，因此阶段 1 写完就是终态。约束：

- `pid = null` + `flags.source = "standalone_writer"`
- `flags.motor_bus_enabled` **恒为 null**；这反映"manual session 不对 arbiter 的健康做判定"，而不是"arbiter 挂了"
- 阶段 2 对 manual session **不存在**

`timezone`：写 IANA 名字，供日后跨时区 replay 用。

### `*.summary.json`

Session 结束时写入（`atexit` 触发），**原子**：

```json
{
  "schema": "lelamp.memory.v0.session_summary",
  "session_id": "sess_2026-04-17_23-11-15",
  "start_ts_ms": 1776438675000,
  "end_ts_ms": 1776450123000,
  "duration_s": 11448,
  "event_counts": {
    "conversation": 42,
    "function_tool": 57,
    "fallback_expression": 19,
    "playback": 34
  },
  "style_histogram": {
    "excited": 12,
    "caring": 18,
    "worried": 6,
    "sad": 3
  },
  "fallback_rate": 0.33,
  "top_recordings": ["curious", "happy_wiggle", "shy"],
  "narrative": "本次会话持续 3 小时 10 分，用户多以 caring 风格被回应；…"
}
```

字段说明：

- `event_counts`、`style_histogram`、`fallback_rate`、`top_recordings` 都是**纯统计**，由 writer 在进程退出前扫本 session 的 events 计算
- `fallback_rate` = `fallback_expression` 事件数 / `conversation` 事件数；= 0.33 意味着每 3 轮对话就有 1 次被 AutoExpressionController 兜底；**当 `event_counts.conversation == 0` 时（任何 playback-only manual session 都如此）取 `null`**，避免除零和"0/0 = 0 fallback" 的误导
- `style_histogram` 的 key 来自 `conversation.assistant_style`；**没有 conversation 时为空字典 `{}`**，不是省略也不是 `null`
- `top_recordings` 仅从 `kind=playback` 和 `kind=function_tool{tool_name=play_recording}` 的事件统计；manual session 通常只有 playback，`top_recordings` 可以是长度 0–3 的数组
- `narrative` 是**可选**的自然语言摘要；v0 的生产策略：
  - **默认不生成**，值为 `null`
  - 可选生成，由一个独立的 summarization 步骤（本地 Gemma-3-4B-GGUF 或者 qwen 短 prompt）产出
  - 生成失败 / 超时 → 依然写 summary，`narrative = null`，不阻塞 session 正常退出
- **narrative 预算上限：512 tokens / 1024 字符**；超出截断
- **manual session 不生成 `narrative`**（没有对话脉络可总结），可写统计字段；这些统计只用于审计 / 归档，不代表它们会进入 prompt 读路径（见 `PROMPT_INTEGRATION.md` §"Reader 的副作用契约"）

#### Manual session summary 的合法 shape（playback-only 示例）

一个 `remote_control play curious` 单次调用产生的 `sess_manual_*`，被下次 writer 启动自检补写的 summary **必须**严格符合以下 shape：

```json
{
  "schema": "lelamp.memory.v0.session_summary",
  "session_id": "sess_manual_2026-04-17_09-32-10",
  "start_ts_ms": 1776432730000,
  "end_ts_ms": 1776432732034,
  "duration_s": 2,
  "event_counts": {
    "conversation": 0,
    "function_tool": 0,
    "fallback_expression": 0,
    "playback": 1
  },
  "style_histogram": {},
  "fallback_rate": null,
  "top_recordings": ["curious"],
  "narrative": null
}
```

关键约束（必须由 writer 实现，reader / prompt_builder 必须容忍）：

- **`event_counts` 的 4 个 kind 字段全部必填**，缺失的 kind 写 0 而非省略（方便下游直接 `d["conversation"]` 读）
- **`fallback_rate` 是 `null`**，而不是 `0.0` / `0` / `NaN`
- **`style_histogram` 是空对象 `{}`**，不是 `null`、不是省略
- **`narrative` 恒为 `null`**（v0 强约束，见上）
- `top_recordings` 的阶数来自 playback，没有时是空数组 `[]`

这条"**统计字段可存在，但 prompt 读路径一律过滤 `sess_manual_*`**"的约束在 `PROMPT_INTEGRATION.md` §"Reader 的副作用契约" 里落到具体 section 上（`recent_conversation` / `playback_digest` / `style_tendency` 三处都一刀切过滤 `sess_manual_`）。

### 为什么 summary 不是事件

因为它是**派生**的。events 是 ground truth，summary 是 cache；任何时候都能从 events.jsonl 重建 summary。所以 summary 丢了不是 corruption，只是需要重新扫一遍事件。

---

## Recent Window（最近窗口）

prompt 注入要"带最近一点上下文"。v0 的窗口定义：

```
最近 3 个 agent session，且 |events_considered| ≤ 200
取两个条件交集里更严格的那个
```

- **manual session 默认不进 recent window**（它们是调试 / 硬件测试产物，不代表用户语境）
- 但 manual session 的事件仍会在 `events.jsonl` 里、在归档里；只是 `recent_index.json` 只索引 agent session 的 summary

落地文件：`recent_index.json`（每个 session 结束时重算）：

```json
{
  "schema": "lelamp.memory.v0.recent_index",
  "built_at_ms": 1776450123500,
  "sessions": [
    { "session_id": "sess_2026-04-17_23-11-15", "summary_ref": "sessions/sess_2026-04-17_23-11-15.summary.json" },
    { "session_id": "sess_2026-04-16_20-02-41", "summary_ref": "sessions/sess_2026-04-16_20-02-41.summary.json" },
    { "session_id": "sess_2026-04-15_19-51-03", "summary_ref": "sessions/sess_2026-04-15_19-51-03.summary.json" }
  ],
  "event_tail_refs": [
    { "event_id": "01JR5K...", "kind": "conversation", "ts_ms": 1776449001000 }
  ]
}
```

- `sessions`：列出最近 3 个 **agent** session 的 summary 指针（**过滤掉 `sess_manual_` 前缀**）
- `event_tail_refs`：最近 200 个事件的**索引**（仅 `event_id` + `kind` + `ts_ms`）；**同样过滤掉属于 `sess_manual_*` 的事件**（`event_tail_refs` 构建时按事件所在 session 的 id 判断）；reader 按需回读全文

> **为什么 manual session 的事件也要过滤掉**：prompt_builder 只消费 `recent_index.json` 和 `sessions/*.summary.json`；如果 `event_tail_refs` 或 summary sessions 里混入 manual session，`recent_conversation` / `playback_digest` 等 section 就会把硬件调试行为当成"用户上下文"塞进 prompt，和"debug 不污染用户语境"的目标冲突。manual session 的事件仍然保留在 `events.jsonl` 和归档里，只是**不进 prompt 读路径**。

选择 `session_count_cap = 3` 和 `event_cap = 200` 的理由：

- 3 session = 大约最近 1 周的日常使用（答辩视角足够）
- 200 事件 ≈ 50 轮对话（每轮 4 事件），塞进 512 tokens 预算里裁剪得动
- 取 min 是为了**双向兜底**：短 session × 很多次 和 长 session × 少数次，都不会爆预算

**重建时机（只由 writer 负责）**：

- session 结束写 `summary.json` 的同一步骤后立即重建 `recent_index.json`
- 下次 session 启动时 **writer** 先做一次 "index 自检"（在写 `meta.json` **之前**，flock 保护下）：
  - `recent_index.json` 缺失 / `built_at_ms` 早于 `events.jsonl` 的 mtime → writer 同步重建一次
  - 存在 `sessions/*.meta.json` 但缺对应 `summary.json`（说明上次崩了）→ writer 扫残余 events 补一份 summary（对 agent session 和 manual session 都适用）
- 重建原子：tmp+rename

> **Reader 永不参与重建**。这和 `PROMPT_INTEGRATION.md` §"Reader 的副作用契约" 一致：
> prompt_builder 在读取时若发现 `recent_index.json` 缺失 / 过期，直接走**降级读**（扫最多 3 个最近的 agent `sessions/*.summary.json`），**不写盘**。
> 任何 "修盘" 路径只出现在 **writer 进程的启动自检** 里，且在写 `meta.json` 之前完成。

---

## 归档（Archive）

### 触发条件

下列**任一**成立就触发 rotate：

- `events.jsonl` 体积 > **10 MiB**
- `events.jsonl` 的 `ts_ms` 跨度 > **3 天**
- 手工：`python -m lelamp.memory.rotate`（未来实现留口子）

### 归档流程

```
1. flock 锁住全局锁
2. 按月聚合：把本 events.jsonl 里所有事件按 ts_ms 的 YYYY-MM append 到 archive/YYYY-MM.jsonl
3. 原子清空 events.jsonl（tmp/empty → rename）
4. 在 archive/ 里写 _MANIFEST.json：记录每个月份文件的事件计数、起止时间、MD5
5. 释放锁
```

### 不可丢

- **不删除**任何事件。v0 的立场是"硬盘便宜、记忆珍贵"
- 真要删 → 用户自己 `rm`；程序不提供删除 API

### archive 的 reader 契约

- prompt 注入层**不读**归档，只读 `events.jsonl` 的尾部 + `recent_index.json`
- 归档文件是**审计存档**，给用户 `grep / jq` 用，不是在线路径
- 后续如果做"长程记忆"（v1+），会在读路径加一个 retrieval 层；v0 没有

---

## 老化规则（Decay）

v0 **不主动衰减**（不 downweight、不 merge 旧 session 的 summary）。

理由：

- 衰减意味着有"权重模型"，进入策略层，违背 v0 的硬边界
- `recent_index.json` 的 cap = 3 session 自然实现了"只看近期"
- 更老的 session 依然在 `sessions/*.summary.json` 里，想看的时候可以 `ls -lt sessions/` 翻

---

## 容错与恢复

### events.jsonl 末行损坏

- reader 在解析时 `json.JSONDecodeError` on last line → 跳过、继续
- writer 下次 append 时前置一个 `\n`（若上一行没以 `\n` 结尾）

### summary / recent_index 缺失

- session 结束写 summary 失败 → 下次 **writer** 启动自检时扫残留 events **回填**
- recent_index 缺 / 过期 → 下次 **writer** 启动自检时重建
- 所有恢复路径**可重入**、**幂等**
- **Reader 不参与任何修复**；遇到缺失 / 过期 → 降级读（扫最多 3 个最近 agent `sessions/*.summary.json`），不写盘

> 为什么把修复收编到 writer：reader 并发度高（每次 session 启动的 prompt 构建）且常被打包进同步路径；让 reader 写盘 = 潜在多进程写冲突 + 把延迟引进 agent 启动。writer 自检是**单次、串行、在 flock 保护下**完成的，语义干净。

### Manual session 孤儿

- dashboard / remote_control 新建了 manual session 后崩溃 → `meta.json` 存在但 `summary.json` 缺失
- 下一个**任意** writer 启动时（agent writer 或另一个 manual writer）做自检，按"存在 meta 无 summary"路径补 summary
- Manual session 的 summary 不进 `recent_index`，所以即使短暂缺失也不影响 prompt 注入

### 时间倒流（用户手工改系统时钟）

- writer 端检测到 `ts_ms < last_ts_ms - 5min` → 打 WARN，但仍然按当前时钟写
- v0 不试图修正；答辩不会遇到

### profile.json 被手工编辑坏了

- reader 端 `json.JSONDecodeError` → 退化到"无 profile"模式，整个 memory 还能用
- `display_name` / `banned_styles` 等影响 prompt 层，但不影响 writer

---

## 时间线示例

### 场景 1：正常 agent 生命周期

```
T=0s     Pi 开机，systemd 起 lelamp.service
T=0.2s   smooth_animation entrypoint 进入
         writer 启动自检（flock 下）:
           - 若上次 agent 或 manual session 有 meta 无 summary → 补写
           - 若 recent_index.json 缺失/过期 → 扫最近 3 个 agent summary 重建
         writer 写 sessions/sess_...meta.json 阶段 1 骨架（tmp+rename）
           flags.motor_bus_enabled = null   # 显式"尚未裁定"
         writer 开启 events.jsonl append-only fd
T=0.3s   MotorBusServer.start() 尝试 bind（可能成功，可能 bind retry 最终失败，
         也可能 AnimationService.start() 先抛错，整个 arbiter 不启动）
T=0.4s   writer 阶段 2：read meta.json → patch flags.motor_bus_enabled = true/false
         → 同一份 meta.json 第二次 tmp+rename（flock 下）
         若进程在此之前被 SIGKILL，flags.motor_bus_enabled 停留在 null
T=2s     用户说"你好"；ASR commit → LLM response → 1 条 conversation 事件
         AutoExpression 350ms 到点前 LLM 调了 express("caring") → function_tool x2
         RGB 没变 → 0 条 playback
...
T=2h     systemctl restart lelamp
T=2h+0s  atexit:
           - writer 扫本 session events → 写 sessions/sess_..._summary.json
           - 重算 recent_index.json（manual session 不入）
           - 释放文件锁
T=2h+1s  新进程启动，重复 T=0.2 流程（writer 自检 → meta → arbiter）
```

### 场景 2：只有 dashboard / CLI，agent 不在

```
T=0s     sudo systemctl stop lelamp
T=10s    用户在 terminal 执行: python -m lelamp.remote_control play curious
T=10.0s  remote_control writer 调 attach_or_create_session():
           - 扫 sessions/*.meta.json 降序，发现最新 agent session 的 pid 不存活
           - 新建 sess_manual_2026-04-17_09-32-10
           - 写 manual meta.json（pid=null，flags.source=standalone_writer，
             flags.motor_bus_enabled=null 且恒为 null，不做阶段 2）
T=10.1s  remote_control 调 build_rgb_service_with_proxy / build_animation_service_with_proxy
         motor_bus sentinel 不存在 → 走 direct fallback（H0 语义）
T=12s    play 完成，remote_control writer 写一条 playback(initiator=remote_control,
         session_id=sess_manual_...)，进程退出
         （不在 atexit 立刻写 summary——remote_control 是短命进程，
          summary 由下次任意 writer 启动自检补）
T=1h     sudo systemctl start lelamp
T=1h+0.2s 新 agent writer 自检发现 sess_manual_... 有 meta 无 summary
         → 扫残余 events，按 §"*.summary.json" 的 manual shape 合约补 summary：
             event_counts = {conversation:0, function_tool:0, fallback_expression:0, playback:1}
             style_histogram = {}
             fallback_rate = null   # conversation=0，禁止 0/0
             top_recordings = ["curious"]
             narrative = null
         → recent_index.json 重建时**不包含**这条 manual session
```
