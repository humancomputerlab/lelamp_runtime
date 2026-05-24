# H1 Memory v0 — Event Schema

## 总则

- 每个事件 = 一行 **JSONL**（UTF-8，无 BOM，`\n` 结尾）
- 所有时间戳用 **毫秒精度 epoch**（`int`），字段名 `ts_ms`；时区信息由 writer 端写入 `session` 头，不进单事件
- 所有事件必须带 **公共字段**；类型特有字段放在 `payload` 下，**不污染顶层**
- 未知字段**保留**，reader 端用 "ignore unknown" 策略，便于日后 v0.x 小版本升级

## 公共字段（所有事件都必须有）

```json
{
  "schema": "lelamp.memory.v0",
  "event_id": "01JR5K8...",            
  "ts_ms": 1776438861533,
  "user_id": "default",
  "session_id": "sess_2026-04-17_23-11-15",
  "kind": "conversation | function_tool | fallback_expression | playback",
  "source": "voice_agent | dashboard | remote_control | auto_expression",
  "payload": { ... }
}
```

字段约束：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | 固定字符串 | ✅ | 版本 tag，**永远**是 `lelamp.memory.v0`；v0.x 演进不改这一行，改 `payload` 里的 `payload_version` |
| `event_id` | ULID / UUID 字符串 | ✅ | 单机生成，用于幂等 / 去重 |
| `ts_ms` | int64 epoch ms | ✅ | 毫秒精度；writer 必须用 `time.time_ns()//1_000_000` 这类单调合规来源 |
| `user_id` | 字符串 | ✅ | v0 固定 `"default"`；非法值写入器直接 reject |
| `session_id` | 字符串 | ✅ | 见 `LIFECYCLE.md`；agent 进程用 `sess_<ts>`，dashboard / remote_control 独立运行时用 `sess_manual_<ts>`。由 `attach_or_create_session()` 统一分配，**不得**自造 |
| `kind` | 枚举 | ✅ | 仅 4 个值 |
| `source` | 枚举 | ✅ | 事件由哪个进程产生 |
| `payload` | object | ✅ | 按 `kind` 不同分支；不允许空对象，必须至少有 `payload_version` |

---

## kind=`conversation`

语音交互里一个**完整的说话轮次**。

### 产生条件

- voice agent 端完成一次 user turn → assistant response 的闭环
- 必须同时具备 **user 文本** 和 **assistant 文本** 才写入；未完成的 turn（ASR commit 但 LLM 未回 / LLM 回了但被打断）**不写**
- 生产点候选（由实现阶段选其一）：
  - `lelamp/local_voice/*` 的回合结束 hook
  - `AgentSession` 的 `on_user_speech_committed` + `on_agent_speech_committed` 配对
  - **不要**在 ASR 每个 interim 都写，磁盘会爆

### payload

```json
{
  "payload_version": 1,
  "user_text": "你刚才为什么看起来很累",
  "assistant_text": "我没有累啦，只是光线暗了一点",
  "user_text_lang": "zh",
  "assistant_style": "caring",         
  "turn_duration_ms": 4812,            
  "model_provider": "qwen|glm|gemma",
  "model_name": "qwen-omni-3.5"
}
```

约束：

- `user_text` / `assistant_text`：单轮最大 **2048 字符**，超出截断到 2048 并在末尾追加 `"…[truncated]"`
- `assistant_style`：对齐 `voice_profile.py` 里的四种风格（`excited / caring / worried / sad` 或当前的等价集合）；如果本轮 assistant 没带 style tool call，写 `null`
- `turn_duration_ms`：从 user turn commit 到 assistant turn commit 的墙钟时间；失败则写 `null`
- **不写音频**；不写 ASR 中间结果；不写 LLM 的 raw tokens / tool-call JSON（工具调用另立 `function_tool`）

---

## kind=`function_tool`

agent LLM 调用的 function tool（`express`、`play_recording`、`set_rgb_solid`、`paint_rgb_pattern`、`set_volume` 等）。

### 产生条件

- function tool **开始调用** 时写一条，`payload.phase = "invoke"`
- function tool **结束调用**（成功或抛错）时写一条，`payload.phase = "result"`，用同一个 `invoke_id` 关联
- **失败**（抛异常、参数校验不过）也要写 `result`，带 `error` 字段
- 生产点：`smooth_animation.py` 里 `LeLamp` class 的每个 tool 方法入口 + 出口

### payload

```json
{
  "payload_version": 1,
  "invoke_id": "inv_01JR5K...",        
  "phase": "invoke | result",
  "tool_name": "express",
  "args": { "style": "excited" },      
  "caller": "llm | auto_expression",   
  "duration_ms": 142,                  
  "ok": true,                          
  "error": null                        
}
```

约束：

- `args`：JSON-serializable；长度 > 1024 字节要截断并在末尾加 `"_truncated": true`
- `invoke_id`：`invoke` 和对应的 `result` 共享，方便后续对齐
- `caller`：**区分**是 LLM 主动调用还是 `AutoExpressionController` 的 350ms 兜底派发；这个区别在答辩里有用（"这些是模型决策，这些是兜底")
- 如果 tool 抛了 `MotorBusClientError` / 503 fallback，照写 `ok=False` + `error=...`

---

## kind=`fallback_expression`

`AutoExpressionController` 在 350ms 等不到 LLM 的 style 调用时，派发的默认表情。
**和 `function_tool(caller=auto_expression)` 有重合**；v0 的处理是：

- `function_tool` 那条仍然写（机器执行层）
- 本 `fallback_expression` 也写（决策层），但 payload 里显式标这是 **fallback 决策本身**，不是 tool 调用

这样在 prompt 注入时可以只读 `fallback_expression` 就看到"这个用户最近被兜底派发了多少次某风格"。

### 产生条件

- `AutoExpressionController` 的超时 timer fire、即将调用 `express` 时写入
- 若 LLM 在最后一刻抢先调用了 style，**不写** `fallback_expression`，只写 `function_tool(caller=llm)`

### payload

```json
{
  "payload_version": 1,
  "style": "curious",                  
  "trigger": "voice_silence_timeout",  
  "linked_conversation_event_id": "01JR5K..."
}
```

约束：

- `style`：必须是 `AutoExpressionController` 实际支持的枚举；非法值 → 写入器 reject，记一条 runtime warning
- `linked_conversation_event_id`：最近一条 `conversation` 事件的 `event_id`；如果当前 session 还没有 conversation，写 `null`

---

## kind=`playback`

**dashboard 或 remote_control** 触发的硬件播放行为。**voice-agent 调 `play_recording` 等工具产生的硬件动作不写入本类事件**，由 `function_tool` 唯一承载（见下"去重契约"）。

### 产生条件

- 在 `runtime_bridge.play / startup / shutdown_pose / set_light_solid / clear_light` 的**完成回调**里写入（成功 / 失败都写）
- **不在 motor_bus 的 HTTP 层写**——motor_bus 是硬件 arbiter，写日志是上层的事
- **不在 voice agent 的 function tool 路径写**——那条路径已经有 `function_tool` 的 invoke/result 对，重复记会让 `fallback_rate` 等统计失真

### payload

```json
{
  "payload_version": 1,
  "action": "play | startup | shutdown_pose | light_solid | light_clear",
  "recording_name": "curious",         
  "rgb": [255, 170, 70],               
  "initiator": "dashboard | remote_control",
  "duration_ms": 2034,
  "ok": true,
  "error": null
}
```

约束：

- 根据 `action` 不同，`recording_name` / `rgb` 可选其一或都为 null
- `initiator` 枚举**仅** `dashboard | remote_control`。**不接受** `voice_agent_tool`
- `duration_ms`：对 `play / startup` 是 `wait_until_playback_complete` 的真实耗时（H0.2 之后这个值是真的，不是 14ms race 假值）

### 去重契约（voice agent 路径）

| 触发路径 | 记录事件 |
|---|---|
| LLM 调 `play_recording("curious")` | `function_tool(invoke) + function_tool(result)`，**不**写 playback |
| LLM 调 `set_rgb_solid(...)` | `function_tool(invoke) + function_tool(result)`，**不**写 playback |
| AutoExpression 兜底调 `express("curious")` | `fallback_expression + function_tool(invoke) + function_tool(result)`，**不**写 playback |
| Dashboard 按钮点"play curious" | `playback(initiator=dashboard)` |
| `python -m lelamp.remote_control play curious` | `playback(initiator=remote_control)` |

**原因**：voice_agent 的硬件动作一定伴随 LLM / AutoExpression 的 `function_tool` 事件；写两条是重复。dashboard / remote_control 的硬件动作**没有** function_tool 伴随，必须用 playback 保留审计。

---

## 不允许的字段

v0 明确**不**接受下列字段，任何 PR 加入它们都应被拒：

- `emotion_score` / `fatigue_level` / 任何 **主观打分**
- `intervene_*` / `fluxchi_*` 任何干预相关字段
- `embedding` / `vector` 任何向量
- `audio_path` / `image_path` 任何媒体指针
- `speaker_id` / `voice_print` / 任何身份识别
- `reward` / `policy_action` 任何 RL 风味字段

理由都在 `README.md` 的"硬边界"里。

---

## 示例 A：voice-agent 路径（3 条事件，无 playback）

用户说"逗我笑一下"，模型回了个笑话并调用 `play_recording("happy_wiggle")`：

```jsonl
{"schema":"lelamp.memory.v0","event_id":"01JR5K001","ts_ms":1776438861533,"user_id":"default","session_id":"sess_2026-04-17_23-11-15","kind":"conversation","source":"voice_agent","payload":{"payload_version":1,"user_text":"逗我笑一下","assistant_text":"这盏台灯走进酒吧…","user_text_lang":"zh","assistant_style":"excited","turn_duration_ms":2100,"model_provider":"qwen","model_name":"qwen-omni-3.5"}}
{"schema":"lelamp.memory.v0","event_id":"01JR5K002","ts_ms":1776438861620,"user_id":"default","session_id":"sess_2026-04-17_23-11-15","kind":"function_tool","source":"voice_agent","payload":{"payload_version":1,"invoke_id":"inv_42","phase":"invoke","tool_name":"play_recording","args":{"recording_name":"happy_wiggle"},"caller":"llm"}}
{"schema":"lelamp.memory.v0","event_id":"01JR5K003","ts_ms":1776438863905,"user_id":"default","session_id":"sess_2026-04-17_23-11-15","kind":"function_tool","source":"voice_agent","payload":{"payload_version":1,"invoke_id":"inv_42","phase":"result","tool_name":"play_recording","args":{"recording_name":"happy_wiggle"},"caller":"llm","duration_ms":2285,"ok":true,"error":null}}
```

这 3 条是完整链：**说话 → 模型决策调工具 → 工具返回成功**。硬件是否真的动了、播放耗时多少，**由 `function_tool.result.duration_ms` 承载**（result 是在 `wait_until_playback_complete` 返回后写的，见 `LIFECYCLE.md`）。答辩时 `tail -n 3 events.jsonl | jq` 即可复现。

## 示例 B：dashboard 路径（1 条 playback）

用户在 dashboard 点了"play curious"按钮：

```jsonl
{"schema":"lelamp.memory.v0","event_id":"01JR5K010","ts_ms":1776438920100,"user_id":"default","session_id":"sess_2026-04-17_23-11-15","kind":"playback","source":"dashboard","payload":{"payload_version":1,"action":"play","recording_name":"curious","rgb":null,"initiator":"dashboard","duration_ms":2034,"ok":true,"error":null}}
```

单条 playback。**不伴随** conversation / function_tool —— dashboard 不走 LLM。

这两类示例共同体现去重契约：**voice-agent 路径用 `function_tool`，dashboard / CLI 路径用 `playback`，不重叠。**
