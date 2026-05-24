# H1 Memory v0 — Open Questions

> 这份是**设计稿作者留给自己和 reviewer（包括 codex）的争议点列表**。
> 每个条目都是 v0 里**故意没定**的地方，或者**有可能翻车**的地方。
> review 意见可以集中火力打这些条，而不是在已经锁死的 6 条合同上来回磨。

---

## OQ-1 · summary.narrative 的生产路径

**问题**：session summary 里的 `narrative` 字段，由谁生成？

- 选项 A：本地 Gemma-3-4B-GGUF 跑一次 512-token 总结（已经在 Pi 上可用）
- 选项 B：qwen-omni 外部 API（要等下一次联网）
- 选项 C：不生成（`narrative: null`），summary 只有统计字段

**当前倾向**：v0 默认 **C**；选项 A 作为 `LELAMP_MEMORY_SUMMARIZER=gemma_local` opt-in。
**悬而未决**：如果 Gemma 在 Pi 上跑一次 summary 要 > 15s，atexit 会被 systemd TERM 干掉。需要实测确认 deadline 友好行为。

---

## OQ-2 · session_id 冲突

**问题**：`sess_<YYYY-MM-DD>_<HH-MM-SS>` 到秒级。systemd 快速 restart 理论上同一秒启两次不可能，但万一呢？

- 选项 A：检测到同名 meta 存在则追加 `-1 / -2` 后缀
- 选项 B：id 加上 ms 精度：`sess_YYYYMMDD_HHMMSS_mmm`

**当前倾向**：**A**，因为 id 还要 human-readable。B 在答辩讲解时要多解释一层。
**悬而未决**：A 的 "同名检测 + 冲突重命名" 要不要走 `tmp+rename` 原子？如果两个进程都进到了 `A` 分支，第二个 rename 会覆盖第一个。

---

## OQ-3 · function_tool 的 invoke/result 配对丢失

**问题**：LLM 正在调用 `play_recording`，写了 `invoke`；进程被 kill 之前没走到 `result` → 孤儿 invoke。

- 选项 A：reader 在构建 header 时忽略孤儿 invoke（不进 prompt digest）
- 选项 B：**下一次 session 启动时 writer 自检阶段**回收，补一条 `result{ok=False, error="orphaned_by_session_exit"}`
- 选项 C：不处理，答辩不讲

**当前倾向**：**A + B 组合**——reader 按 A 的规则读（只读就忽略孤儿），writer 按 B 的规则在启动自检里补 result。这样 reader 保持纯读，writer 保持 audit 完整。
**与合同对齐**：此路径不违反 "reader 纯读"（见 `PROMPT_INTEGRATION.md` §"Reader 的副作用契约"），因为 B 是在 writer 自检阶段、flock 保护下完成。
**悬而未决**：v0 实现时要显式 test 这两条路径。

---

## OQ-4 · recent_index 的 cap = 3 session 是否够

**问题**：3 个 session 是"最近一周"还是"最近一天"取决于用户活跃度。
- 如果用户一天开关 lelamp 10 次，3 session 可能就是今天；prompt 里缺中期记忆
- 如果用户 2 周才用一次，3 session 跨度很长，summary narrative 可能已过时

**备选**：加一个时间上限（`max_age_days = 14`），任一命中即停止。
**当前倾向**：v0 先固定 3 session。如果演示里出现"这个对话模型像失忆了"，我马上改。不提前优化。

---

## OQ-5 · 全局文件锁的跨进程可靠性

**问题**：`fcntl.flock` 在 Pi ext4 上通常可靠，但若 `$HOME/.lelamp/memory/` 挂在 tmpfs / NFS（诡异配置）会退化。

- v0 文档假设 ext4
- writer 启动时要不要 `stat` 一下文件系统类型，遇到非本地 FS 打 WARN？

**当前倾向**：先信任 ext4。writer 做一个开机时自检（检测 `st_dev` 或用 `os.statvfs`），**只 WARN 不 abort**。

---

## OQ-6 · prompt budget 超限的健壮性

**问题**：512 tokens 是硬预算，但估算器是 "字符数/3"。如果某轮对话里用户打了长英文 + 表情 emoji + 代码块，真实 token 数会显著偏离估算。

- 选项 A：把估算改成 `len(s) / 2.5`（偏保守）
- 选项 B：接入 tiktoken，但会引入新依赖
- 选项 C：在 reader 末尾加一个 `assert len(header) < budget_char_hard = 2048`，超过直接砍到 2048 字符

**当前倾向**：v0 用 **C** 作为兜底（硬字符上限 2048）；A 作为估算策略。B 留到 v0.x。

---

## OQ-7 · 归档文件体积的真实增长率

**问题**：10 MiB `events.jsonl` 按 session 节奏能撑多久？做一个粗估：

- 每轮对话产出 4 事件，每个 ~800 bytes → 约 3.2 KiB/轮
- 1 session ≈ 50 轮 → 160 KiB
- 10 MiB ≈ 64 session ≈ 持续使用 2 个月

**悬而未决**：这个量级是否合理？如果实际生产出的事件**只有一半**是预期值（比如 fallback 没真正落地），需要反向校正 schema。
v0 发布后跑一周，看 `wc -l` 与预测的偏差。

---

## OQ-8 · 测试环境里如何隔离 memory 目录

**问题**：单元测试需要 isolated `$HOME`，否则跑 test 会污染真实 memory。

- 选项 A：所有 test 都 `tmp_path` + `monkeypatch.setenv("LELAMP_MEMORY_ROOT", ...)`
- 选项 B：writer / reader 暴露一个注入点 `root_path: Path`，测试时直接传

**当前倾向**：**B** 更干净。A 依赖 env 有魔法感。
本设计稿不锁死，在实现 PR 里决定。

---

## OQ-9 · "记忆是被读的，不是主动发的" 到底多严格

**问题**：v0 约定 memory 不反向驱动动作。但未来：

- 如果 reader 发现"上次 session 异常退出"，是不是可以在 startup 时让 lelamp 做一个"你回来了"的表达？
- 如果 style_tendency 里 caring 占 60%，是不是可以在 voice_profile 里稍微调低 caring 权重？

这些都是**有诱惑**的扩展。v0 **全部拒绝**，但需要在 README 里更明确地加一句 "拒绝动机"。
**悬而未决**：要不要把"memory 读出来后是否允许做推理决策"写进 README 的硬边界？
**当前倾向**：在 README 补一句："v0 的 header 是**描述性**的，不是**指示性**的。所有会改变 agent 决策链路的消费都必须走 v1。"

---

## OQ-10 · 与 codex 审稿的对齐点

如果 codex 来 review 本设计稿，最可能打的点（提前准备回应）：

| 预期质疑 | 我的站位 |
|---|---|
| "记录 function_tool 两条（invoke/result）是过度采样" | 不让步。因为答辩里展示"硬件真的动了没"需要 result.ok |
| "narrative 让 Gemma 跑会炸 atexit" | 接受，改 OQ-1 的 C 作为默认 |
| "用 fcntl.flock 而不是 SQLite 是怕复杂度" | 接受是这个动机，保持现状 |
| "512 tokens 预算太小 / 太大" | 先锁 512，发布后观察 |
| "应该加 intervene 事件预留字段" | **拒绝**。这是 v0 的硬边界。FluxChi 没落地，schema 里不给空字段 |
| "recent_index 设计得像索引，是否过早优化" | 不让步。session summary 加载是 I/O 热路径，索引省一次扫全事件 |

---

## 待补充（留给 review）

```
OQ-11: <可能由 reviewer 提出>
OQ-12: ...
```
