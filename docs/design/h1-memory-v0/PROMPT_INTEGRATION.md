# H1 Memory v0 — Prompt Integration

## 目标

把 memory 从"磁盘上的 JSONL"变成"每轮 agent 启动时注入到 system prompt 的一段文本"，而且：

1. **不改 voice_profile.py 的现有 style 配比**（answers the "这不是学习策略"）
2. **有明确 token 预算**，不膨胀 context
3. **用户可见、可覆盖**——不是黑盒
4. **读取失败可降级**——记忆挂了，lelamp 还是跑

---

## 注入点

`voice_profile.py` 当前产出 system prompt 的地方（实现阶段再精确到函数名）。
H1 reader 在那之前加一个 `build_memory_header()`，把结果**前置**到 system prompt：

```
<memory user_id="default" schema="lelamp.memory.v0" generated_at="2026-04-17T23:11:15+08:00">
  ... 下面详述的 sections ...
</memory>

<原来的 voice_profile system prompt>
```

为什么前置不后置：

- LLM 对开头 tokens 的 recency 更弱，但对 system prompt 开头的 **框架性陈述** 敏感度高
- `<memory>` 标签让模型明确"这是历史语境，不是当前指令"，降低记忆干扰当前 turn 的概率
- 和 voice_profile 的 style 配比是**正交**关系：memory 给上下文，voice_profile 给风格

---

## Token 预算

**硬预算：512 tokens**（默认值，env 可调：`LELAMP_MEMORY_PROMPT_BUDGET`）。
裁剪按以下优先级，**预算不够时从下往上砍**：

| 优先级 | Section | 典型大小 | 裁剪策略 |
|---|---|---|---|
| P0（保留） | `profile_hint` | ~40 tokens | 带 `display_name` + `banned_styles`，永不砍 |
| P1 | `session_summary_recent` | ~150 tokens | 最近 1 个 session 的 narrative；超出按句截断，保留前 N 个句号 |
| P2 | `style_tendency` | ~40 tokens | 汇总最近 3 session 的 style_histogram，给模型看"这个用户过去偏好/不偏好什么" |
| P3 | `recent_conversation` | ~180 tokens | 最近 5 条 conversation 的 `user_text` + `assistant_text`，每条压到 1 行 |
| P4 | `function_tool_digest` | ~50 tokens | 最近 10 次 function_tool 调用的 name + success rate；超限直接整节砍 |
| P5 | `playback_digest` | ~50 tokens | 最近 10 次 playback 的 top3 recording / rgb；超限整节砍 |

裁剪算法（伪代码）：

```python
def build_memory_header(budget: int = 512) -> str:
    sections = [
        profile_hint(),           # P0 always
        session_summary_recent(), # P1
        style_tendency(),         # P2
        recent_conversation(),    # P3
        function_tool_digest(),   # P4
        playback_digest(),        # P5
    ]
    estimated = [estimate_tokens(s) for s in sections]
    while sum(estimated) > budget and len(sections) > 1:
        sections.pop()
        estimated.pop()
    if sum(estimated) > budget:
        sections[1] = truncate_by_sentence(sections[1], budget - estimated[0])
    return "\n".join(sections)
```

`estimate_tokens` 在 v0 用 "字符数 / 3" 的 **快估** 就够了；v0.x 可以换 `tiktoken`。

---

## 各 section 的文本形态

下面用"给模型的纯文本"形式示例，而不是 JSON。
因为 LLM 对叙事性文本的感知比 JSON 好，尤其在 Gemma-3-4B-GGUF 这种小模型上。

### P0 — profile_hint

```
USER CONTEXT
- 用户昵称：小吴（profile 里手工填的，可改）
- 明确不喜欢的风格：headshake
```

规则：

- `banned_styles` 为空 → 整条省略
- `nickname` 为 `null` → 省略那一行
- 这是**唯一**允许"用户意愿凌驾模型决策"的地方；后面的 style tendency 是**统计**，不是 hint

### P1 — session_summary_recent

```
LAST SESSION RECAP (2026-04-16, 3h10m)
本次会话持续 3 小时 10 分。用户多以 caring 风格被回应；38% 的表情是兜底而非模型主动选择。高频动作：curious, happy_wiggle。
```

规则：

- 取最近 1 个 session 的 `narrative`；若 `narrative = null`，fallback 成 "系统统计回放"：基于 `event_counts` + `style_histogram` 拼一行中文
- **时间**用人类友好的"YYYY-MM-DD + 时长"，不丢毫秒

### P2 — style_tendency

```
STYLE PATTERNS (last 3 sessions)
- 被回应最多：caring (42%), excited (31%)
- fallback 比例：33%（模型有三分之一的轮次没主动选风格）
```

规则：

- **只统计，不裁决**；不要写"模型应该多用 X 少用 Y"——那是策略，v0 不做
- fallback 比例高（> 40%）时加一句："注意：最近 fallback 比例偏高"——但仍然不规定模型动作
- 这段的意义：给模型**自我认知**，而不是给 prompt 下命令

### P3 — recent_conversation

```
RECENT TURNS (最近 5 轮)
1. [23:09] user: 你刚才为什么看起来很累  → assistant(caring): 我没有累啦，只是光线暗了一点
2. [23:11] user: 那你能不能亮一点        → assistant(excited): 这样如何？
3. ...
```

规则：

- 最多 5 条；每条单行
- 每段 `user_text` / `assistant_text` 截到 60 字符（中文 60 ≈ 60 tokens）
- **如果预算不够**，优先丢弃最老的，保留最新的 1-2 条
- **绝不**包含 `turn_duration_ms` / `model_name` 这类元数据——给模型看的是**语境**，不是日志

### P4 — function_tool_digest

```
TOOL USAGE (recent 10)
- express × 6 (ok: 6)
- play_recording × 3 (ok: 3)
- set_rgb_solid × 1 (ok: 1)
```

规则：

- 按 tool 分组；只展示 top 3
- `ok / total` 给 reliability 信号；如果有 fail，顺手提示最后一次 error（截 60 字符）
- 预算紧张时整节砍

### P5 — playback_digest

```
HARDWARE USAGE (recent 10)
- 最常播放：curious, happy_wiggle, shy
- 最近一次灯光：rgb(255, 170, 70) @ 23:10
```

规则：

- 预算紧张时整节砍
- 若最近 10 个 playback 里 `ok=False` 比例 > 20% → 加一行 "注意：硬件回放最近有失败"（维护者提示，不是模型指令）

---

## Reader 的副作用契约

`build_memory_header()` 必须：

- **纯读**：不写任何文件、不改 profile / recent_index / summary / events.jsonl
  - 即使 `recent_index.json` 缺失、过期、损坏——**不修复、不重建**
  - 即使发现孤儿 `function_tool.invoke`（无 result）——**不补 result**
  - 任何"修盘"需求都落在 writer 侧（见 `LIFECYCLE.md` §"Session 的定义" 和 §"summary / recent_index 缺失"），reader 只观察
- **同步**：启动时一次性构建，注入 prompt；不在每轮 turn 里重建
- **幂等**：给同一组磁盘状态，多次调用产生**字节级相同**的字符串（便于 diff / test）
- **可降级（三级）**：
  1. **normal**：`recent_index.json` 存在且新鲜 → 走索引路径，O(1) 打开最多 3 个 summary 文件（`recent_index.sessions` 构建时已过滤 manual）
  2. **degraded**：`recent_index.json` 缺失 / 过期（`built_at_ms < events.jsonl.mtime`）→ `sorted([f for f in ls sessions/*.summary.json if not basename(f).startswith("sess_manual_")], reverse=True)[:3]` 取最近 3 个 **agent** summary 读，**不写索引**
  3. **fallback**：连 `sessions/` 都不存在 / 全是坏 JSON / 过滤后为空 → 返回空字符串或 `<memory status="unavailable"/>`，**不抛异常**

**所有三级都 `sess_manual_*` 零参与 prompt 读路径**：
- `recent_conversation` section 不得包含 manual session 的 conversation 事件
- `playback_digest` section 不得包含任何 `session_id` 以 `sess_manual_` 开头的 playback（无论 initiator 是 `dashboard` 还是 `remote_control`）
- `style_tendency` 聚合 `style_histogram` 时**仅基于** agent summary；manual session 即使带统计字段，也一律不参与 prompt 聚合

这三级都**不写盘**。writer 进程下次启动时会在自检阶段把 index 补齐，reader 下一次启动就能回到 normal。

### 在哪一步注入

- 时机：`smooth_animation.py` 的 `entrypoint` 里，`LeLamp` 实例构造前
- `LeLamp` 会把这段 header 作为 ctor 参数传进去，voice_profile 的 `build_system_prompt(memory_header=...)` 拼接
- **注入一次，不 hot-reload**：session 运行中 memory 文件变化不会被重新读（避免中途改变模型行为）

---

## 用户可见 / 可覆盖

v0 提供三条 "用户手柄"：

1. **直接编辑 `profile.json`**：改 `nickname` / `banned_styles` / `notes`；重启 agent 生效
2. **`LELAMP_MEMORY_DISABLE=1`**：env var；reader 看到就直接返回空 header，等同于无记忆版本
3. **`python -m lelamp.memory.inspect`**（CLI，v0 仅设计，实现留给后续）：打印当前会被注入的完整 header，让用户 dry-run 看到模型会看到什么

第 3 条是答辩的关键工具：可以现场 demo "看，我的记忆系统不是黑盒"。

---

## 注入示例（完整）

```
<memory user_id="default" schema="lelamp.memory.v0" generated_at="2026-04-17T23:11:15+08:00">
USER CONTEXT
- 用户昵称：小吴
- 明确不喜欢的风格：headshake

LAST SESSION RECAP (2026-04-16, 3h10m)
本次会话持续 3 小时 10 分。用户多以 caring 风格被回应；38% 的表情是兜底而非模型主动选择。高频动作：curious, happy_wiggle。

STYLE PATTERNS (last 3 sessions)
- 被回应最多：caring (42%), excited (31%)
- fallback 比例：33%

RECENT TURNS (最近 5 轮)
1. [23:09] user: 你刚才为什么看起来很累  → assistant(caring): 我没有累啦，只是光线暗了一点
2. [23:11] user: 那你能不能亮一点        → assistant(excited): 这样如何？

TOOL USAGE (recent 10)
- express × 6 (ok: 6)
- play_recording × 3 (ok: 3)
</memory>

<下面是原来的 voice_profile system prompt ...>
```

大致 ~380 tokens，留 130 tokens 余量。

---

## 与 voice_profile.py 的关系

**正交、不替换、不修改**。

- voice_profile 定义"lelamp 是个什么角色、style 配比如何"——这是**人格**
- memory 定义"这次 session 启动时，它应该记得什么"——这是**记忆**
- 两者在 system prompt 里串联，memory 在前、voice_profile 在后
- 任何 style-ratio 的改动**都不走** H1；H1 只**观察**用了什么 style，不**规定**用什么 style

如果后续想让 style 配比**受记忆影响**（比如 fallback 率高时降低 shy 权重），那是 **H2** 的事，不是 v0。

---

## 测试契约（实现阶段要写的 test）

- `test_header_budget_respected`：给人造事件流，确认输出 header ≤ budget
- `test_header_deterministic`：同输入两次生成结果 byte-equal
- `test_header_graceful_missing_dir`：memory 目录不存在 → 不抛，返回空
- `test_header_graceful_corrupt_jsonl`：末行损坏 → 跳过，其他事件照常进入 header
- `test_header_respects_disable_env`：`LELAMP_MEMORY_DISABLE=1` → 返回空
- `test_banned_styles_in_profile_hint`：`profile.json` 里 `banned_styles=["headshake"]` → header 里必须出现那行

这些 test **在 v0 实现 PR 里写**；本设计稿只约定它们必须存在。
