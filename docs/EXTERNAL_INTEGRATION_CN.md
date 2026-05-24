# LeLamp Runtime 外部集成指南

这份文档面向**把 LeLamp 当成一个 embodied output 终端来消费的外部系统**，而不是改 LeLamp 内部的人。典型读者：

- FluxChi backend（fatigue fusion / context broadcaster 的作者）
- 未来接入的第二个 agent（例如接手术间、会议室等场景的 Pi）
- 任何想从 Pi 外部驱动台灯动作 / 灯光 / 语音响应的进程

如果你要改 LeLamp 本体（新增动作、扩 dashboard、改硬件服务层），看 [SECONDARY_DEVELOPMENT_GUIDE_CN.md](./SECONDARY_DEVELOPMENT_GUIDE_CN.md) 而不是这份。

> **文档状态**：v0.1（2026-04-21）。本文描述的 dashboard / motor bus / FluxChi harness consumer 已落地；但“context feed 反向消息”“manual intervene 路由”“session summary memory event”仍是 **v0 proposed**，需要吴嘉俊审阅 + codex / cursor-opus 在各自 territory 内确认后再落地。

---

## 1. 心智模型

**LeLamp runtime 是一个持有硬件 owner 的单进程 agent**，所有到灯 / 电机 / LED 的写操作在这个进程里收口。外部进程不直接碰硬件，而是通过下面三个面之一跟它交互：

| 面 | 协议 | 权限 | 用途 |
|----|------|------|------|
| Dashboard HTTP | `127.0.0.1:8765` + 可选远程 | 触发动作、查状态、看日志 | 人类操作 / 外部服务触发 |
| Motor bus loopback | `127.0.0.1:8770` | proxy 硬件服务，**仅同机** | CLI / 其它本机进程复用 agent 已持有的服务 |
| 共享状态文件 | `/tmp/lelamp-voice-state.json` 等 | 只读 | 外部消费者在动作前做 gate |

外部系统最常用的搭配是：**Dashboard HTTP 写 + 共享状态文件读**。motor bus loopback 主要给同机 CLI / openclaw 用，不建议跨机使用。

**关键不变量**：外部系统**永远不应该**直接 import `lelamp.service.motors` / `lelamp.service.rgb` 去 new 一份服务，哪怕看起来"只是并行读"。详见 §8。

---

## 2. 典型消费形态

按"外部系统跟 LeLamp 的耦合紧密度"排序：

### 2.1 一次性触发（最松）

外部系统只在特定事件发生时命令 LeLamp 做一个动作，比如 webhook、定时器、按钮。

- 用 dashboard 的 action route（见 §3.2）
- 不需要持续连接
- 不需要看状态文件
- 例子：家庭自动化触发"到点关灯 + 道晚安"

### 2.2 周期性状态消费（中等）

外部系统拿 LeLamp 的状态快照，做自己的决策后不一定触发动作。

- 轮询 dashboard 的 `GET /api/state`（见 §3.1）
- 或者订阅新增的 WebSocket（v0 proposed，§4.2）
- 例子：答辩监控面、远程家庭成员查看"奶奶今天的状态"

### 2.3 持续上下文注入（最紧，harness 模式）⭐

外部系统持续把用户 / 环境状态推给 LeLamp，由 LeLamp 的消费者（场景 profile 或 LLM tool）决定怎么响应。

- **这是 FluxChi 的使用模式**，也是本轮 demo 的核心
- 需要 §4 的 context feed
- Pi 侧当前已有 `lelamp.integrations.fluxchi_listener.FluxChiStateListener`
  - Scene A：动作 + 暖光，经 dashboard HTTP
  - Scene B：连续呼吸灯，经 motor bus `ProxyRGBService`
- 例子：fatigue 监测、情绪共情、远程陪伴

三种形态可以共存，但同一时刻触发硬件的路径只能有一条（见 §8 single-owner）。

---

## 3. 已存在接口（4-20 commits 已落地）

以下接口**今天就可以用**，外部系统接入不需要 LeLamp 改代码。详见 [API_REFERENCE_CN.md](./API_REFERENCE_CN.md)，这里只给外部集成最常用的子集。

### 3.1 Dashboard 状态读取

```
GET http://127.0.0.1:8765/api/state
```

返回统一 state tree：

```json
{
  "system": {...},
  "motion": {"last_action": "shy", "last_completed_at": ...},
  "light": {"mode": "solid", "rgb": [255, 200, 80]},
  "audio": {...},
  "voice": {
    "last_asr_status": "idle",
    "last_asr_text": null,
    "last_reply_text": null,
    "local_state": "listening"
  },
  "errors": []
}
```

**注意**：
- `voice.last_asr_text` / `voice.last_reply_text` 默认是 **null**（隐私），只有 `LELAMP_DASHBOARD_EXPOSE_TRANSCRIPT=1` 时才返回真实文本。外部消费者不能假设这个字段一定非空。
- 默认只监听 `127.0.0.1`。要远程读，需要 `LELAMP_DASHBOARD_HOST=0.0.0.0` + 自己解决网络可达。不要默认开全网。

### 3.2 Dashboard 动作触发

现有 dashboard 动作路由：

```
POST /api/actions/play              {"name": "shy"}
POST /api/actions/startup
POST /api/actions/stop
POST /api/actions/shutdown_pose
POST /api/lights/solid              {"red": 255, "green": 200, "blue": 80}
POST /api/lights/clear
```

这些经过 `DashboardActionExecutor` 串行化，一次只跑一个动作。如果 agent 自己在跑动作，executor 会排队或拒绝（行为看 `actions/executor.py`）。

**推荐姿势**：外部 one-shot 触发都走这一层，而不是自己 new motor bus client。

**例外**：Scene B 的连续呼吸共振是 10Hz 连续 RGB 调制，不走 dashboard。它当前通过：

- `lelamp.integrations.fluxchi_listener` 的 breath 路径
- 或 `python -m lelamp.breath_run`

直接拿 motor bus `ProxyRGBService` 写 `/rgb/solid`，以避开 dashboard busy-lock。

### 3.3 Voice state gate

读 `/tmp/lelamp-voice-state.json`：

```json
{
  "last_asr_status": "speaking",
  "last_asr_text": null,
  "last_reply_text": null,
  "local_state": "user_speaking",
  "updated_at_ms": 1745259600123
}
```

外部消费者**在触发动作 / 灯光前应该读这个**，确认：

- `local_state` 不是 `user_speaking` / `agent_speaking`
- `updated_at_ms` 距今不超过 5 秒（否则认为 voice 通路挂了，按策略决定 gate 与否）

文件权限是 `0600`，和 Pi 上跑 runtime 的同一个 user 才能读。跨机消费需要先 SSH 过来 cat 一份、或者通过 dashboard 的 `voice` 字段读。

### 3.4 状态文件列表（快照一览）

| 文件 | 用途 | 谁写 | 谁读 |
|------|------|------|------|
| `/tmp/lelamp-voice-state.json` | 语音状态 | runtime voice telemetry | dashboard / 外部消费者 |
| `/tmp/lelamp-motor-bus.json` | motor bus sentinel | motor_bus/server | motor_bus/client / 外部 ownership 判定 |
| `$HOME/.lelamp/memory/default/` | memory events | memory/writer | memory/reader / LLM prompt |

外部消费者只应该**读**这些文件，不写。Motor bus sentinel 尤其不要伪造——fail-closed 逻辑依赖它的真实性。

---

## 4. Context Feed（已落地 v0）

这是 harness 形态下“外部持续注入上下文”的核心接口。当前 producer / consumer 都已经有可运行实现：

- FluxChi backend 暴露 `/ws/harness`
- LeLamp 侧 consumer 在 [`lelamp/integrations/fluxchi_listener.py`](../lelamp/integrations/fluxchi_listener.py)
- profile 目前有两套：
  - [`lelamp/integrations/profiles/scene_a.yaml`](../lelamp/integrations/profiles/scene_a.yaml)
  - [`lelamp/integrations/profiles/scene_b.yaml`](../lelamp/integrations/profiles/scene_b.yaml)

其中：

- Scene A = 传统离散提醒（`motion`）
- Scene B = 呼吸共振（`breath`）

### 4.1 协议选型理由

为什么是 WebSocket 而不是 long-poll / HTTP callback：

- 频率是秒级（建议 1–5 秒一帧），long-poll 的 RTT overhead 对体感影响大；
- 需要双向可选（Pi 偶尔要反馈 "debounce tripped" / "voice busy, dropping frame"）；
- FluxChi backend 本来就用 websocket 给前端推，复用同一个 server；
- 断线重连简单，对 demo 场景足够。

### 4.2 Endpoint

由 FluxChi backend 暴露，**Pi 主动连出去**：

```
ws://<fluxchi-host>:8000/ws/harness
```

`<fluxchi-host>` 部署拓扑由吴嘉俊决定。demo 阶段当前有两种可用方式：

- FluxChi backend 跑在你 Mac 上；
- Pi 直接连 Mac 的 `:8000`；
- 如果 Pi → Mac HTTP / WS 不稳定，可改用 Mac 反向 SSH 隧道，在 Pi 本地消费：
  - `ws://127.0.0.1:18000/ws/harness`

### 4.3 Frame schema v0

每帧 JSON：

```json
{
  "ts": 1745259600.123,
  "version": "1.0",
  "subject": {
    "stamina": 72,
    "stamina_trend_10min": -0.08,
    "perclos_ewma": 0.18,
    "perclos_trend_10min": 0.03,
    "mdf_trend_10min": -0.05,
    "blink_rate": 14.2,
    "head_yaw_abs": 12.0,
    "session_minutes": 47
  },
  "events": [
    {"kind": "microsleep_detected", "ts_rel_ms": -1200, "confidence": 0.72}
  ],
  "context": {
    "user_present_conf": 0.92,
    "emg_available": false,
    "vision_quality": 0.68,
    "relationship_token": "uid_epwujiajun_n7_d2"
  },
  "suggested_action": {
    "urgency": "light_nudge",
    "recommended_style_hint": "shy",
    "debounce_ok": true
  }
}
```

字段语义：

- **`ts`**：FluxChi 后端系统时间 Unix 秒，Pi 侧用它判 stale（默认 > 5 秒视为陈旧）。
- **`subject`**：归一化后的用户状态。`stamina` 0–100 整数（100 = 清醒）、trend 是过去 10 分钟的归一斜率。Pi 侧消费者看这些聚合量，不接触原始 84 维 EMG / BlendShapes。
- **`events`**：level-3 强信号（短时、离散），不是持续流。列表每帧可能为空。`ts_rel_ms` 是相对 `ts` 的毫秒偏移（负数 = 历史）。
- **`context.emg_available` / `vision_quality`**：告诉消费者哪些模态当前可信，如果都不行 Pi 侧应该 fallback 到"用户不在或状态不明"策略。
- **`context.relationship_token`**：不透明字符串，对应 FluxChi 后端的某个 user identity 记录。Pi 侧 memory layer 拿它去查"这是第几次见面、上次多久之前"等（Pi 本地存储，FluxChi 不负责）。demo 阶段可以 hardcode 成常量（见 DEMO_PLAN §7 风险 6）。
- **`suggested_action`**：FluxChi 的意见，但不是命令。Pi 侧 scenario profile 可以覆盖或忽略。`urgency ∈ {none, light_nudge, strong_nudge, urgent}`；`recommended_style_hint` 是 expression_engine 里的现有 style 名或 `null`。

### 4.4 帧频率 & 触发规则

FluxChi backend 推帧策略（建议，v0）：

- **定时帧**：每 5 秒一帧；
- **状态变化帧**：`stamina` 穿阈值、level-3 event 发生、`user_present_conf` 跨 0.5 这些情况**立即追发**一帧，`ts` 是事件时间，后面 5 秒的定时帧不被跳；
- **静默期**：`user_present_conf < 0.3` 持续 30 秒后，降到每 30 秒一帧。Pi 侧消费者用这个信号进 idle。

### 4.5 Pi 侧消费者契约

Pi 侧 `FluxChiStateListener` 的义务：

1. **建连**：WebSocket 连不上时指数退避重连（1s → 2s → 4s → … 上限 30s）。
2. **去抖**：按 profile 做同 level 去抖。当前基线：
   - Scene A：30 秒
   - Scene B：120 秒
   level 上跳（mild→moderate）立即触发覆盖去抖。
3. **Voice gate**：触发前读 `/tmp/lelamp-voice-state.json`，`user_speaking` / `agent_speaking` 状态下跳过本帧（不 drop 连接，只跳过触发）。
4. **Stale 保护**：`ts` 距今 > 5 秒时当前帧仅用于状态显示，不用于触发。
5. **单 owner 尊重**：
   - `motion` 决策走 dashboard HTTP（`/api/actions/*` + `/api/lights/*`）
   - `breath` 决策走 motor bus `ProxyRGBService`
   - 两条路径都**不能**直接 new `AnimationService` / `RGBService`
6. **降级**：连续 30 秒没收到任何帧 → 进"最后帧 hold 模式"，不再主动触发动作，保留 LED 当前状态不变。

### 4.6 Profile schema（已落地）

当前 `action` 支持两类：

```yaml
action:
  type: motion
  express: headshake
  followup: nod
  rgb: [255, 170, 50]
```

```yaml
action:
  type: breath
  breath:
    duration_sec: 90.0
    bpm_start: 12.0
    bpm_end: 4.0
    kelvin_start: 3000.0
    kelvin_end: 2400.0
    intensity_base: 0.35
    intensity_peak: 1.0
    tick_hz: 10.0
    name: moderate_breath
```

语义：

- `motion`：Scene A 风格，离散动作，可选前置暖光
- `breath`：Scene B 风格，连续 RGB 呼吸，不播 recording

### 4.6 反向消息（可选）

如果你希望 FluxChi 后端知道 Pi 是否采纳了 suggested_action，Pi 可以偶尔发：

```json
{"kind": "dispatch_report", "ts": ..., "decided": "shy", "gated_by": null}
```

或

```json
{"kind": "dispatch_report", "ts": ..., "decided": null, "gated_by": "voice_speaking"}
```

v0 阶段这个反向通道不是必需。

---

## 5. Manual Intervene Route（v0 proposed）

为答辩和调试场景准备的人类按钮，语义上和 FluxChi 的 context feed 并列，但**来源是人**。

### 5.1 Endpoint

在 dashboard 新增（需要 Claude 在 D6 里落）：

```
POST /api/actions/intervene
{
  "style": "shy" | "headshake" | "sad" | "happy_wiggle" | "breath",
  "reason": "demo_manual",
  "payload": { ... style-specific ... }
}
```

返回：

```json
{"ok": true, "dispatched_at": ..., "executor_queue_depth": 0}
```

### 5.2 语义

- **Style 层抽象**：不是"直接 play 某个 recording"，而是走 `expression_engine.build_expression_plan(style, ...)` 拿到 `(recording_or_frames, rgb_plan)` 后 dispatch。这样 manual 按钮和 FluxChi 自动触发、LLM 工具触发走同一条 expression 路径，不会因为人机不同路而出现行为分歧。
- **不绕过 voice gate**：按钮也会经过 `FluxChiStateListener` 类似的 gate 逻辑，默认 voice_speaking 时拒绝（返回 `{"ok": false, "gated_by": "voice_speaking"}`）。调试时可以加 `?force=1` 显式绕过。
- **不写 memory**：手动触发不计入 `session_summary.interventions.total`，避免污染关系记忆。如果需要计入，用 `reason=demo_auto_simulation` 之类的显式标记。

### 5.3 前端按钮

Dashboard 前端加一排按钮：`Shy / Headshake / Sad+Nod / Happy / Breath 90s`。每个按钮就是上面的 POST。按钮默认显示 executor 队列深度，队列非空时置灰。

---

## 6. Memory 扩展（v0 proposed）

### 6.1 Session summary event

**提案**：新增 event type `session_summary`，在 session 结束或用户显式结束时由 `AgentMemoryRuntime` 写入：

```json
{
  "kind": "session_summary",
  "session_id": "...",
  "ended_at_ms": 1745259600123,
  "duration_min": 94,
  "peak_stamina": 88,
  "min_stamina": 34,
  "interventions": {
    "total": 3,
    "by_style": {"shy": 2, "headshake": 1},
    "heeded_count": 1
  },
  "peak_fatigue_window": {
    "start_min": 72,
    "stamina": 34
  }
}
```

`heeded_count` 定义：动作触发后 2 分钟内 `stamina_trend_10min` 由负转正 视为 heeded。

### 6.2 build_memory_header 扩展

`build_memory_header(budget=512)` 已经在 `lelamp/memory/runtime.py` 里落地。**提案**扩展行为：

- 取最近 N=3 条 `session_summary`；
- 压缩成 snippet：
  ```
  <relationship>
  你和这个人累计 7 次见面。
  上次是 2 天前，那次持续 94 分钟，峰值疲劳在 72 分后，你用 shy 提示过 2 次，他都继续了。
  </relationship>
  ```
- 总预算 budget=512 token，超出时按"最近 > 摘要长度"裁。

### 6.3 跨 Claude / cursor-opus 的裁决需求

根据 DEMO_PLAN §5.3，session_summary 的写入时机涉及 cursor-opus 的 H1a（IT/FE 状态跨会话持久化）territory。Claude **不单方面**动 `lelamp/memory/writer.py`，需要吴嘉俊裁决：

- 方案甲：Claude 直接扩 writer + reader；
- 方案乙：Claude 只写 spec（本节就是），cursor-opus 落代码，走 dual-agent-sync 协议。

---

## 7. 引用实现：FluxChi 作为 reference consumer

这一章对 FluxChi backend 作者（也是 Claude 自己的 D4/D5）有用，展示一个"标准"的集成架子。

### 7.1 Producer 侧（FluxChi backend）

```python
# reference/harward-gesture/web/harness_broadcaster.py（建议新文件）

import asyncio
import json
import time
from fastapi import FastAPI, WebSocket
from harward_gesture.fusion import FusionEngine

class HarnessBroadcaster:
    def __init__(self, fusion: FusionEngine):
        self.fusion = fusion
        self._subscribers: set[WebSocket] = set()

    async def subscribe(self, ws: WebSocket):
        await ws.accept()
        self._subscribers.add(ws)
        try:
            while True:
                # keep alive; 真实状态由 broadcast_loop 推
                msg = await ws.receive_text()
                # 收 pong / dispatch_report
        finally:
            self._subscribers.discard(ws)

    async def broadcast_loop(self):
        last_level = "none"
        while True:
            frame = self._build_frame()
            if self._should_push(frame, last_level):
                for ws in list(self._subscribers):
                    try:
                        await ws.send_text(json.dumps(frame))
                    except Exception:
                        self._subscribers.discard(ws)
                last_level = frame["suggested_action"]["urgency"]
            await asyncio.sleep(5)

    def _build_frame(self) -> dict:
        snapshot = self.fusion.snapshot()
        return {
            "ts": time.time(),
            "version": "1.0",
            "subject": snapshot.subject_dict(),
            "events": snapshot.recent_level3_events(),
            "context": {
                "user_present_conf": snapshot.user_present,
                "emg_available": snapshot.emg_ok,
                "vision_quality": snapshot.vision_q,
                "relationship_token": "uid_epwujiajun_n7_d2",  # demo hardcode
            },
            "suggested_action": self._advise(snapshot),
        }

    def _should_push(self, frame, last_level) -> bool:
        # 定时帧 or 状态跨阈值
        ...

    def _advise(self, snapshot) -> dict:
        # 非命令性建议；具体策略可调
        ...
```

### 7.2 Consumer 侧（Pi 上的 FluxChiStateListener 实装）

当前不再是骨架，而是实装文件：

- [`lelamp/integrations/fluxchi_listener.py`](../lelamp/integrations/fluxchi_listener.py)

它已经包含：

- WebSocket 重连
- stale frame 丢弃
- voice gate
- Scene A `motion` dispatch
- Scene B `breath` dispatch
- `motion` / `breath` 双向 preempt
- shutdown 时停止正在跑的 breath

直接运行：

```bash
uv run python -m lelamp.integrations.fluxchi_listener \
  --ws ws://127.0.0.1:18000/ws/harness \
  --profile lelamp/integrations/profiles/scene_b.yaml \
  --dashboard http://127.0.0.1:8765
```

只测 profile / 不碰硬件：

```bash
uv run python -m lelamp.integrations.fluxchi_listener \
  --ws ws://127.0.0.1:18000/ws/harness \
  --profile lelamp/integrations/profiles/scene_b.yaml \
  --dry-run --no-voice-gate
```

单独测试 Scene B 呼吸，不等 listener：

```bash
uv run python -m lelamp.breath_run --dry-run --duration 10
```

这两个骨架对 FluxChi 当前代码的依赖只有一条：需要 `FusionEngine.snapshot()` 返回一个能映射到 §4.3 schema 的对象。如果现在的 FusionEngine 接口不完全对得上，Claude 在 D4 里会补 adapter，而不是直接改 FusionEngine。

---

## 8. 禁止事项（外部消费者常见误区）

### 8.1 不要直接 new `AnimationService` / `RGBService`

错：

```python
# 外部进程里
from lelamp.service.motors import AnimationService
svc = AnimationService(port="/dev/ttyACM0")  # ← 会抢串口
svc.play("shy")
```

对（离散动作）：

```python
# 外部进程里
import requests
requests.post("http://127.0.0.1:8765/api/actions/play",
              json={"name": "shy"})
```

对（连续 breath）：

```bash
uv run python -m lelamp.breath_run --duration 90
```

硬件只有一个物理 owner。agent 进程跑起来后，直 new 服务会撞串口或 LED 驱动。motor bus sentinel 存在时，连 motor_bus/client 都会 fail-closed，但 bypass motor_bus 直连硬件的代码没保护。

### 8.2 不要信任过期的 voice state

```python
# 错：不检查时间戳
if voice["local_state"] != "user_speaking":
    trigger()
```

```python
# 对：时间戳 > 5s 当"不可知"
if (now_ms - voice["updated_at_ms"]) > 5000:
    # 按策略决定是否 gate
    ...
```

voice telemetry 进程挂了时，文件会"冻在"某个状态。外部消费者需要自己判 staleness。

### 8.3 不要在 context feed 里塞原始特征

错：把 84 维 EMG 原始通道、blendshape 原始向量塞进 `subject`。

对：在 FluxChi 后端融合 + 归一化后再发出。

理由：Pi 侧 consumer 的 policy 和 scenario profile 应该稳定，不应该因为 FluxChi 换了 EMG 传感器或者从 MediaPipe 切到其它 face model 就要重写。归一化层把模态变化吸收在 FluxChi backend 内部。

### 8.4 不要直接改 dashboard state

dashboard state 是"runtime 自己采样出来的快照"，只读不写。如果你想让 dashboard 显示新字段（例如 FluxChi 的 stamina），走正规扩展路径：

1. Pi 侧在 `FluxChiStateListener` 里把最近一帧存到一个新的采样器 / state store section；
2. 按 SECONDARY_DEVELOPMENT_GUIDE §8 / §10.2 扩 state_store + 前端。

不要从外部 POST 伪造 state。

### 8.5 不要把 manual intervene 按钮做成"直接 play 某个 recording"

错：按钮后端是 `POST /api/actions/motion/play {"recording_name": "shy"}`。

对：按钮后端是 `POST /api/actions/intervene {"style": "shy"}`（§5），内部经 expression_engine 层。

理由：未来 expression_engine 如果把 "shy" 换成 shy + 光 + breath 的组合，所有 consumer（手动 / 自动 / LLM 工具）一致变化；直 play recording 会错过 rgb plan，行为漂。

---

## 9. 版本 & 兼容

- 本文档版本：**v0（2026-04-21）**
- 目标稳定版本：v1（2026-04-28，DEMO_PLAN v0 冻结同时冻结）
- v0 → v1 允许 breaking，v1 之后 schema 字段**只增不减**，语义变化走新 `version` 字段。
- `subject` / `context` 的 required 字段一经 v1 定版不能删；新增字段消费者未识别时必须忽略而不是报错。

---

## 10. 与其他文档的关系

- [TECHNICAL_OVERVIEW_CN.md](./TECHNICAL_OVERVIEW_CN.md)：runtime 内部分层；要知道"你驱动的那些服务背后是什么"时看这个。
- [SECONDARY_DEVELOPMENT_GUIDE_CN.md](./SECONDARY_DEVELOPMENT_GUIDE_CN.md)：要改 LeLamp 本体时看这个。
- [API_REFERENCE_CN.md](./API_REFERENCE_CN.md)：字典式接口参考；本文引用的所有现有 endpoint 在那里有权威签名。
- `FluxChi/DEMO_PLAN_2026-04-21.md`：这轮 demo 的时间线和决策，本文的 §4 / §5 / §6 是那份计划的 D1 产出。
- `FluxChi/dual-agent-sync/PROTOCOL.md`：本文的 §6.3 涉及到的 memory writer 扩展是 dual-agent-sync 的 H1a territory，**不要**绕过协议直接在 cursor-opus 作业的文件上改。

---

**一句话总结**：外部系统不是 LeLamp 的插件，是 LeLamp 的**上下文源**和**触发源**；所有交互经过 dashboard / motor bus / 共享状态文件这三条明路，任何绕过单 owner 的"捷径"都会在下一次重启后付账。
