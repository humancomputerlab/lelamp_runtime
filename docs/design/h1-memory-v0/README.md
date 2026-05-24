# H1 Memory v0 — Design Only

> **状态**：DESIGN / NOT-IMPLEMENTED
> **分支**：`design/h1-memory-v0`（仅设计稿，不含实现）
> **前置**：H0 / H0.1 / H0.2 已完成并通过 Raspberry Pi 5 实机验收（见 `feature/motor-bus-arbiter` 上的 `cf5a692` + `2f51247`）
> **作者角色**：毕业设计；以自洽叙事 + 可答辩性为第一目标，不追求通用框架

---

## 这份设计稿解决什么

H0 系列让 lelamp 的 **硬件侧** 不再抢占（`/dev/ttyACM0` + `/dev/leds0` 单 owner）。
硬件侧稳住之后，lelamp 的 **行为侧** 仍然是"无记忆的机器人"：

- voice agent prompt 是 **静态** 的配比（`voice_profile.py` 里 15/35/25/25）
- fallback expression 映射 **硬编码**（`caring → shy`、`worried → headshake`、`sad → sad`）
- 每次会话从零开始，不知道"上次聊到哪"，不知道"这个用户喜欢 excited 不喜欢 headshake"
- dashboard 里点过的 play/light 行为，下次启动 agent 再也不知道

这份设计稿定义 **H1 Memory v0**：一个**可检查、可覆盖、可归档**的，file-first 的、每用户独立的记忆层。

H1 v0 **只写设计**，不写代码；实现等这份设计稿被自己和 codex 各自看过一轮再开。

---

## 硬边界（非目标，v0 不做）

以下全部在 v0 **显式排除**。任何后续 PR 如果触碰这些，必须单独立项，不能夹带。

| 排除项 | 理由 |
|---|---|
| **intervene 事件** | FluxChi 的 `POST /api/intervene` 在当前 dashboard `api.py` 里并不存在（只有 `/api/state` / `/api/actions/*` / `/api/lights/*`）。在代码里还没落地的信号，不能进 schema，否则 v0 就是一堆空字段。 |
| **多用户** | v0 固定 `user_id = "default"`。"识别说话人"需要额外的 speaker ID / face recognition，和 memory 正交。留到 v1 再谈。 |
| **学习策略 / policy / ACT** | "用记忆训练策略"是 H2 的事。v0 只做**读出来人能看懂、prompt 注进去模型能用**的层。 |
| **共享呼吸 / 色温 / 相位** | 这是 H1b 的 scope，`LELAMP_DASHBOARD` 的 RGBService 目前只支持 `solid / paint` + 全局 brightness，连色温都不是一等能力。H1b 单独分支。 |
| **主动打断 / 情绪触发语音** | v0 不从记忆层反向驱动任何动作。记忆是**被读**的，不是**主动发**的。 |
| **跨设备同步** | 单机文件即可。云同步 / 多台 lelamp 共享记忆不在 v0。 |
| **加密 / 合规** | v0 明文 JSON，放在用户自己的 Pi 上。隐私合规到产品化阶段再谈。 |

---

## v0 做什么（4 类事件 + 注入层）

### 事件模型（见 `SCHEMA.md`）

v0 只记录 4 类事件，全部是**已经在运行时真实发生、有明确生产点**的信号：

1. **`conversation`** — voice agent 的一轮 user/assistant turn
   - 生产点：`lelamp/local_voice/*` 的 ASR commit + LLM response
2. **`function_tool`** — agent 调用 `express / play_recording / set_rgb_solid / paint_rgb_pattern / set_volume` 的记录
   - 生产点：`smooth_animation.py` 里的 `LeLamp` 方法
3. **`fallback_expression`** — `AutoExpressionController` 350ms 超时后派发的默认表情
   - 生产点：`lelamp/auto_expression.py`
4. **`playback`** — **仅** dashboard 或 CLI 发起的 `play / startup / shutdown_pose / light solid / light clear`
  - 生产点：`lelamp/dashboard/runtime_bridge.py` + `lelamp/remote_control.py`
  - **不包含** voice-agent 调 `play_recording` / `set_rgb_solid` 等工具产生的硬件动作——那些由 `function_tool` 唯一承载，避免双写失真。详见 `SCHEMA.md` §"去重契约"。

**不要**超出这 4 类。如果日后想加，走 v0.x 小版本升级，不叫 v1。

### 注入层（见 `PROMPT_INTEGRATION.md`）

记忆不是存进去就完了，要**写得进 prompt，而且模型读得过来**。
v0 明确：

- 一次 session 启动时从 memory 抽出一个**有预算的、有裁剪规则的** `memory_header`
- 插到现有 `voice_profile.py` 的 system prompt **之前**，用 ``<memory>`` / ``</memory>`` 标签包起来
- **用户可见、可覆盖**：memory 文件是明文 JSON，可以手工编辑；可以整个清空；可以置为"只读"

---

## 文件清单

| 文件 | 内容 |
|---|---|
| `README.md` | 本文：scope / 非目标 / 全局约束 |
| `SCHEMA.md` | 4 类事件的字段定义、示例、约束 |
| `STORAGE.md` | 目录布局、原子写入、user_id 策略 |
| `LIFECYCLE.md` | session 划分、summary、recent window、归档、老化 |
| `PROMPT_INTEGRATION.md` | 注入预算、裁剪策略、如何嵌进现有 prompt |
| `OPEN_QUESTIONS.md` | 明确列出暂未决的分歧点，供自评 / codex review |

---

## 六条必须被钉死的约束（答辩叙事用）

> 这六条是本设计稿的 **合同**。任何实现 PR 违反其中任何一条，都要在 PR 描述里显式说明 "打破 v0 约束，原因是 ..."。

1. **事件模型封闭**：只有 `conversation / function_tool / fallback_expression / playback` 四类；新增走 v0.x。
2. **存储位置固定**：`$HOME/.lelamp/memory/<user_id>/`，`user_id=default` 为 v0 唯一合法值。
3. **写入必须原子**：任何单事件写入 = `append` to JSONL + `fsync`；任何聚合态（session summary、recent index）= `tmp+rename`。
4. **prompt 注入有预算**：默认 **≤ 512 tokens**；超出按 `session_summary > recent_conversation > function_tool 高频 > playback 高频` 顺序裁剪。
5. **老化规则显式**：`recent window` 默认 **最近 3 个 session 或 200 事件取小**；其余滚动归档到 `archive/YYYY-MM.jsonl`，**不删除**。
6. **非目标成文**：intervene / 学习策略 / 多用户 / 呼吸 schema / 加密，v0 **一律不做**；任何 review 意见要求加入这些的，**拒绝**，指向对应的未来版本。

---

## 与 H0 的关系

H1 memory **不碰** MotorBusArbiter。

- 写入端：memory writer 是 **纯旁路**，不经过 8770 motor_bus，不参与硬件仲裁
- 读取端：voice agent 启动时从文件读取，不走任何 HTTP
- 失败降级：memory 目录缺失 / JSONL 损坏 → **静默忽略**，lelamp 行为退化成现在的无记忆版本（即当前 runtime-main 行为）
- **反向独立**：session 开始不依赖 MotorBusServer 成功启动；arbiter bind retry 最终失败、`AnimationService.start()` 抛错时，memory 层照样记录事件。`meta.json.flags.motor_bus_enabled` 由 writer 做**两阶段** tmp+rename 写入：阶段 1（session 启动瞬间）写 `null`，阶段 2（`MotorBusServer.start()` 返回后）根据 arbiter 裁定结果写 `true` / `false`。阶段 1 ↔ 阶段 2 之间崩溃会让 `flags.motor_bus_enabled` 停留在 `null`，reader 必须容忍 `null` / `true` / `false` 三种取值。见 `LIFECYCLE.md` §"Session 的定义" 和 §"`*.meta.json`"

换句话说，**H1 不能把 H0 变不稳，H0 也不能把 H1 变不记**。这是底线。

---

## 与"你拒绝碰 runtime-main"的关系

H1 设计稿放在 `design/h1-memory-v0` 分支。实现阶段也不会进 `runtime-main`，会先进 `feature/h1-memory-v0-impl`（命名暂定），通过实机验收后再由你手工 cherry-pick 到你自己的主线（不是 `humancomputerlab/lelamp_runtime`）。

本仓库的 `origin`（`humancomputerlab/lelamp_runtime`）在本分支的生命周期内**不会被 push**。
