# LeLamp Runtime API 与二次开发参考

面向对象：

- 想接入 LeLamp 的二次开发者
- 需要通过 HTTP / CLI / Python 模块控制硬件或语音链路的人
- 需要理解运行时状态文件、代理层、memory 层的人

本文基于当前仓库代码整理，不以旧设计稿或 README 为准。

如果你是第一次读这个仓库，建议先看：

- [TECHNICAL_OVERVIEW_CN.md](./TECHNICAL_OVERVIEW_CN.md)
- [SECONDARY_DEVELOPMENT_GUIDE_CN.md](./SECONDARY_DEVELOPMENT_GUIDE_CN.md)

本文更适合做“接口字典”和“模块索引”，不是第一份总览文档。

---

## 1. 仓库结构与真正入口

这个项目分两层：

- 顶层仓库 `Lelamp/`
  - 负责 Pi 5 bring-up、OpenClaw 集成、部署脚本、Pages 站点
  - 运行时代码实际在 `lelamp_runtime/`
- 运行时子项目 `Lelamp/lelamp_runtime/`
  - 负责语音 agent、动作播放、RGB、dashboard、motor bus、memory

如果你是做二次开发，默认应该从 `lelamp_runtime/` 开始。

核心入口文件：

- `smooth_animation.py`
  - 当前推荐主入口
  - 启动语音 agent、动画服务、RGB 服务、memory runtime、motor bus server
- `main.py`
  - 兼容入口
  - 已弃用，内部转发到 `smooth_animation.py`
- `lelamp/dashboard/api.py`
  - 本地 dashboard FastAPI 入口
- `lelamp/remote_control.py`
  - 高层 CLI / OpenClaw 调用入口

---

## 2. 运行时组件图

### 2.1 主链路

1. `smooth_animation.py` 读取 `.env`
2. `lelamp/runtime_config.py` 解析为 `RuntimeSettings`
3. 根据 `MODEL_PROVIDER` 构建 realtime model
4. `LeLamp(Agent)` 初始化：
   - `AnimationService`
   - `RGBService`
   - 开机动作 + 开机灯光 + 音量
5. `entrypoint()` 再继续启动：
   - `AutoExpressionController`
   - `MotorBusServer`
   - `AgentMemoryRuntime`
   - `AgentSession`
6. 本地 dashboard / CLI 如果发现 motor bus sentinel 存在，就不直接抢硬件，而是走代理层转发到 agent 内部服务

### 2.2 控制平面分层

- `HTTP（局域网/浏览器）`
  - `lelamp/dashboard/api.py`
- `HTTP（本机进程内硬件代理）`
  - `lelamp/motor_bus/server.py`
- `CLI（脚本/OpenClaw）`
  - `lelamp/remote_control.py`
- `Python API`
  - `AnimationService`
  - `RGBService`
  - `DashboardRuntimeBridge`
  - `MotorBus client factories`
  - `memory` 包

---

## 3. 配置 API

配置统一由 `lelamp/runtime_config.py` 提供。

### 3.1 主要类型

- `RuntimeSettings`
  - 运行时所有关键参数的不可变 dataclass
- `load_runtime_settings()`
  - 从环境变量解析 `RuntimeSettings`
- `build_realtime_model(settings)`
  - 根据 provider 返回对应 realtime model 实例
- `build_realtime_model_config(settings)`
  - 给 OpenAI 兼容 realtime model 生成基础 kwargs

### 3.2 关键环境变量

#### 模型与语音

- `MODEL_PROVIDER`
  - `qwen` / `glm` / `openai` / 其他 OpenAI 兼容 provider
- `MODEL_API_KEY`
- `MODEL_BASE_URL`
- `MODEL_NAME`
- `MODEL_VOICE`

#### agent 行为

- `LELAMP_AGENT_LANGUAGE`
- `LELAMP_AGENT_OPENING_LINE`
- `LELAMP_QWEN_USE_SERVER_VAD`
- `LELAMP_GLM_USE_SERVER_VAD`

#### 硬件

- `LELAMP_PORT`
- `LELAMP_ID`
- `LELAMP_FPS`
- `LELAMP_ENABLE_RGB`
- `LELAMP_LED_COUNT`
- `LELAMP_LED_PIN`
- `LELAMP_LED_FREQ_HZ`
- `LELAMP_LED_DMA`
- `LELAMP_LED_BRIGHTNESS`
- `LELAMP_LED_INVERT`
- `LELAMP_LED_CHANNEL`

#### 动作编排

- `LELAMP_STARTUP_RECORDING`
- `LELAMP_IDLE_RECORDING`
- `LELAMP_HOME_RECORDING`
- `LELAMP_USE_HOME_POSE_RELATIVE`
- `LELAMP_INTERPOLATION_DURATION`

#### 音频

- `LELAMP_STARTUP_VOLUME`
- `LELAMP_AUDIO_USER`
- `LELAMP_AUDIO_CARD_INDEX`
- `LELAMP_CONSOLE_ENABLE_APM`
- `LELAMP_CONSOLE_SPEECH_THRESHOLD_DB`
- `LELAMP_CONSOLE_SILENCE_DURATION_S`
- `LELAMP_CONSOLE_MIN_SPEECH_DURATION_S`
- `LELAMP_CONSOLE_COMMIT_COOLDOWN_S`
- `LELAMP_CONSOLE_OUTPUT_SUPPRESSION_S`
- `LELAMP_CONSOLE_AUTO_CALIBRATE`
- `LELAMP_CONSOLE_CALIBRATION_DURATION_S`
- `LELAMP_CONSOLE_CALIBRATION_MARGIN_DB`
- `LELAMP_CONSOLE_START_TRIGGER_S`
- `LELAMP_VOICE_STATE_PATH`

#### dashboard

- `LELAMP_DASHBOARD_HOST`
- `LELAMP_DASHBOARD_PORT`
- `LELAMP_DASHBOARD_POLL_MS`

#### memory

- `LELAMP_MEMORY_DISABLE`
- `LELAMP_MEMORY_ROOT`
- `LELAMP_MEMORY_PROMPT_BUDGET`

---

## 4. 进程入口 API

### 4.1 `smooth_animation.py`

对外最重要的两个符号：

- `LeLamp`
  - 继承自 `livekit.agents.Agent`
  - 负责暴露 function tools，并持有动作/灯光服务
- `entrypoint(ctx)`
  - LiveKit worker 入口

#### `LeLamp` 暴露的 function tools

- `express(style)`
  - 高层情绪表达动作
  - 走 `dispatch_expression(...)`
- `get_available_recordings()`
  - 返回当前动作录制名列表
- `play_recording(recording_name)`
  - 直接播放指定动作
- `set_rgb_solid(red, green, blue)`
  - 设置纯色灯光
- `paint_rgb_pattern(colors)`
  - 设置逐像素 RGB 图案
- `set_volume(volume_percent)`
  - 调整系统音量

这些方法既是 LLM tool surface，也是你做二开时最直观的高层动作 API。

### 4.2 `main.py`

仅用于兼容旧部署：

- 会发出 `DeprecationWarning`
- 再 re-export：
  - `LeLamp`
  - `STARTUP_WARM_RGB`
  - `entrypoint`

新代码不要再把 `main.py` 当主入口。

---

## 5. HTTP API

### 5.1 Dashboard API

文件：`lelamp/dashboard/api.py`

默认监听：

- `http://127.0.0.1:8765`
- 如果显式设置 `LELAMP_DASHBOARD_HOST=0.0.0.0`，则也可以从局域网访问

#### 页面接口

- `GET /`
  - 返回 dashboard 前端页面

#### 状态接口

- `GET /api/state`
  - 返回 `DashboardStateStore.snapshot()`
  - 顶层结构：
    - `system`
    - `motion`
    - `light`
    - `audio`
    - `voice`
    - `errors`

- `GET /api/actions`
  - 返回动作目录与当前 busy 状态
  - 主要字段：
    - `busy`
    - `active_action`
    - `recordings`
    - `poll_ms`
    - `config`
    - `actions`

#### 动作接口

- `POST /api/actions/startup`
  - 触发开机动作编排

- `POST /api/actions/play`
  - 请求体：

```json
{"name": "curious"}
```

- `POST /api/actions/shutdown_pose`
  - 进入关机动作姿态

- `POST /api/actions/stop`
  - 回待机姿态

#### 灯光接口

- `POST /api/lights/solid`

```json
{"red":255,"green":160,"blue":32}
```

- `POST /api/lights/clear`

#### 响应模式

dashboard action 接口统一返回 receipt 风格响应：

- 成功：`202`
- busy：`409`
- 其他失败：`500`

---

### 5.2 Motor Bus Loopback API

文件：`lelamp/motor_bus/server.py`

用途：

- 运行在 voice agent 进程内部
- 只绑定 `127.0.0.1`
- 给 dashboard / CLI 提供“代理到长期占有硬件的服务”的本机接口

默认端口：

- `127.0.0.1:8770`

#### 健康接口

- `GET /health`

返回字段：

- `ok`
- `motor_ok`
- `rgb_ok`
- `animation_error`
- `rgb_available`
- `led_count`
- `pid`

#### 动作接口

- `GET /motor/recordings`
- `POST /motor/play`

```json
{"recording_name":"curious"}
```

- `POST /motor/startup`

```json
{"recording_name":"wake_up"}
```

- `POST /motor/wait_complete`

```json
{"timeout":120.0}
```

#### RGB 接口

- `POST /rgb/solid`

```json
{"red":255,"green":160,"blue":32}
```

- `POST /rgb/paint`

```json
{"colors":[[255,0,0],[0,255,0],[0,0,255]]}
```

- `POST /rgb/clear`

#### 设计语义

- 这是“同机代理协议”，不是公网 API
- 没有 auth
- 设计假设是：同机进程本来就能直接打开 `/dev/ttyACM0` 和 `/dev/leds0`

---

## 6. CLI API

文件：`lelamp/remote_control.py`

启动方式：

```bash
uv run -m lelamp.remote_control <subcommand>
```

### 6.1 全局参数

- `--id`
- `--port`
- `--fps`
- `--audio-user`
- `--model-provider`
- `--model-base-url`
- `--model-name`
- `--model-voice`
- `--led-count`
- `--led-pin`
- `--led-freq-hz`
- `--led-dma`
- `--led-brightness`
- `--led-invert`
- `--led-channel`
- `--enable-rgb` / `--disable-rgb`

### 6.2 子命令

- `show-config`
  - 打印当前解析出来的控制配置

- `list-recordings`
  - 列出动作录制名

- `play <name> [--timeout 120.0]`
  - 播放指定动作

- `solid <red> <green> <blue>`
  - 设纯色灯光

- `clear`
  - 清灯

- `capture-pose <name>`
  - 抓当前舵机姿态，写成静态 recording
  - 额外参数：
    - `--frame-count`
    - `--env-file`
    - `--set-defaults`

- `sync-pose-recordings`
  - 根据内置 pose preset 重建静态 recording
  - 额外参数：
    - `--frame-count`
    - `--env-file`
    - `--set-defaults`

- `startup`
  - 运行正式开机 choreography
  - 额外参数：
    - `--recording`
    - `--home-recording`
    - `--settle-frames`
    - `--settle-hold-frames`
    - `--settle-fps`
    - `--wake-fps`
    - `--post-wake-hold`

- `shutdown`
  - 运行正式关机 choreography 并释放 torque
  - 额外参数：
    - `--recording`
    - `--prepare-fraction`
    - `--prepare-frames`
    - `--settle-frames`
    - `--hold-frames`
    - `--final-hold`
    - `--release-pause`
    - `--keep-led-on`

### 6.3 CLI 的代理逻辑

`remote_control` 不是总直接碰硬件。

它会先看 motor bus sentinel：

- 如果 voice agent 正在运行且 motor/rgb 可用
  - 就走代理层
- 如果没有 sentinel 或代理不可用
  - 才回落到直接硬件路径

这点对二次开发非常重要：

- 想和正在运行的 voice agent 共存
  - 用 `remote_control` 或 `DashboardRuntimeBridge`
- 想自己独占硬件
  - 先停掉 voice agent

---

## 7. Python API

### 7.1 Dashboard 相关

文件：`lelamp/dashboard/runtime_bridge.py`

关键类型：

- `DashboardActionResult`
  - `ok`
  - `message`
  - `detail`

- `DashboardRuntimeBridge(settings, ...)`
  - `list_recordings()`
  - `startup()`
  - `play(recording_name, playback_action="play")`
  - `shutdown_pose()`
  - `stop()`
  - `set_light_solid(rgb)`
  - `clear_light()`

适用场景：

- 你要自己写一个控制面板
- 你不想重复写“先判断 sentinel，再走代理或本地”这套逻辑

### 7.2 Motor Bus Client

文件：`lelamp/motor_bus/client.py`

关键常量：

- `REQUIRE_ANY`
- `REQUIRE_MOTOR`
- `REQUIRE_RGB`

关键函数：

- `current_sentinel(require="any")`
  - 返回 `SentinelInfo` 或 `None`
- `build_animation_service(fallback_factory)`
  - 如果代理可用，返回 `ProxyAnimationService`
  - 否则返回 `fallback_factory()`
- `build_rgb_service(fallback_factory)`
  - 同上，返回 `ProxyRGBService` 或 fallback

关键异常：

- `MotorBusClientError`

关键代理类：

- `ProxyAnimationService`
  - `start()`
  - `stop()`
  - `dispatch(event_type, payload)`
  - `get_available_recordings()`
  - `wait_until_playback_complete(timeout)`

- `ProxyRGBService`
  - `start()`
  - `stop()`
  - `handle_event(event_type, payload)`
  - `dispatch(event_type, payload)`
  - `clear()`

### 7.3 动作服务

文件：`lelamp/service/motors/animation_service.py`

关键方法：

- `start()`
- `stop(timeout=5.0)`
- `dispatch(event_type, payload)`
- `handle_event(event_type, payload)`
- `wait_until_playback_complete(timeout=None)`
- `get_available_recordings()`

支持的 `event_type`：

- `play`
- `startup`

关键行为：

- 内部有 `_playback_done` 事件，可用于等待播放完成
- recording 默认来自 `lelamp/recordings/*.csv`
- 支持从当前姿态插值进入目标 recording
- 普通 recording 结束后会自动回 `idle_recording`

### 7.4 RGB 服务

文件：`lelamp/service/rgb/rgb_service.py`

关键方法：

- `start()`
- `stop(timeout=5.0)`
- `dispatch(event_type, payload, priority=...)`
- `handle_event(event_type, payload)`
- `clear()`

支持的 `event_type`：

- `solid`
- `paint`

后端选择：

- 如果存在 `/dev/leds0`
  - 走设备文件后端
- 否则
  - 回退到 `rpi_ws281x`

### 7.5 通用服务基类

文件：`lelamp/service/base.py`

关键类型：

- `Priority`
  - `LOW`
  - `NORMAL`
  - `HIGH`
  - `CRITICAL`
- `ServiceEvent`
- `ServiceBase`

`ServiceBase` 提供：

- `dispatch(...)`
- `start()`
- `stop()`
- `wait_until_idle(timeout=None)`
- `handle_event(...)` 抽象方法

RGB 服务就是基于它实现的；动画服务没有直接继承，而是自带一套事件队列。

---

## 8. Realtime Provider API

### 8.1 Qwen

文件：`lelamp/qwen_realtime.py`

关键符号：

- `QwenRealtimeModel`
- `QwenRealtimeSession`
- `build_qwen_turn_detection()`
- `build_qwen_input_audio_transcription()`
- `build_qwen_session_payload(...)`
- `normalize_qwen_tool_schema(...)`

特点：

- 输入/输出音频格式都是 `pcm`
- 默认 transcription 模型是 `qwen3-asr-flash-realtime`
- 支持工具 schema 归一化
- 会对 spoken reply 走 `sanitize_spoken_reply(...)`
- 会写入 voice telemetry

### 8.2 GLM

文件：`lelamp/glm_realtime.py`

关键符号：

- `GLMRealtimeModel`
- `GLMRealtimeSession`
- `build_glm_beta_fields(...)`
- `build_glm_session_payload(...)`
- `normalize_glm_tool_schema(...)`

特点：

- 输入格式是 `wav`
- 输出格式是 `pcm`
- 有额外 `beta_fields`
- 工具数量上限 `_GLM_TOOL_LIMIT = 10`
- 同样走 `sanitize_spoken_reply(...)`

如果你要加新 provider，最现实的入口仍然是：

1. 在 `runtime_config.py` 补 provider 识别与默认值
2. 新建 `<provider>_realtime.py`
3. 在 `build_realtime_model(settings)` 里分发

---

## 9. Memory API

文件入口：`lelamp/memory/__init__.py`

对外公开的主要接口：

- `build_memory_header()`
- `bootstrap_agent_runtime(settings, user_id=None)`
- `AgentMemoryRuntime`
- `memory_root()`
- `user_memory_root()`
- `ensure_user_memory_root()`
- `generate_session_id()`
- `generate_event_id()`
- `generate_invoke_id()`

### 9.1 Runtime 集成面

文件：`lelamp/memory/runtime.py`

关键接口：

- `bootstrap_agent_runtime(settings)`
  - 返回 `AgentMemoryRuntime`
  - 失败时自动降级成 no-op runtime

- `AgentMemoryRuntime.install_session_listeners(session, ...)`
  - 监听：
    - `user_input_transcribed`
    - `conversation_item_added`
    - `function_tools_executed`

- `AgentMemoryRuntime.note_auto_expression_fallback(...)`
  - 记录 auto-expression fallback

- `record_standalone_playback(...)`
  - 记录 dashboard / remote_control 发起的 playback 事件

### 9.2 Writer API

文件：`lelamp/memory/writer.py`

关键类：

- `MemoryWriter`

关键方法：

- `write_conversation(...)`
- `write_function_tool(...)`
- `write_fallback_expression(...)`
- `write_playback(...)`

事件类型：

- `conversation`
- `function_tool`
- `fallback_expression`
- `playback`

### 9.3 Reader API

文件：`lelamp/memory/reader.py`

关键函数：

- `build_memory_header(...)`
  - 纯读接口
  - 默认预算 512 token
  - 失败时降级成 `<memory status="unavailable"/>`

### 9.4 生命周期 API

文件：`lelamp/memory/session.py`

关键接口：

- `start_agent_session(...)`
- `attach_or_create_session(...)`
- `SessionHandle`

### 9.5 存储位置

默认根目录：

```text
$HOME/.lelamp/memory/default/
```

典型子结构：

```text
sessions/
archive/
events.jsonl
profile.json
recent_index.json
```

---

## 10. 状态文件 API

### 10.1 Voice telemetry

文件：`lelamp/voice_telemetry.py`

默认路径：

```text
/tmp/lelamp-voice-state.json
```

关键接口：

- `default_voice_telemetry()`
- `configure_voice_telemetry(path)`
- `get_voice_telemetry()`
- `read_voice_telemetry(path)`
- `VoiceTelemetryStore.update(...)`
- `VoiceTelemetryStore.snapshot()`

典型字段：

- `status`
- `local_state`
- `speech_threshold_db`
- `noise_floor_db`
- `last_level_db`
- `calibration_enabled`
- `calibration_progress`
- `last_asr_status`
- `last_asr_error_code`
- `last_asr_text`
- `last_reply_text`
- `last_response_id`
- `last_result`
- `updated_at_ms`

### 10.2 Motor bus sentinel

文件：`lelamp/motor_bus/sentinel.py`

默认路径：

```text
/tmp/lelamp-motor-bus.json
```

关键类型：

- `SentinelInfo`
  - `pid`
  - `port`
  - `base_url`
  - `started_at_ms`
  - `version`

关键接口：

- `sentinel_path()`
- `write_sentinel(info)`
- `read_sentinel()`
- `read_live_sentinel()`
- `remove_sentinel()`

用途：

- 让别的本机进程知道 voice agent 的代理总线是不是活着

---

## 11. Dashboard 状态结构

`GET /api/state` 返回的顶层结构固定为：

```json
{
  "system": {},
  "motion": {},
  "light": {},
  "audio": {},
  "voice": {},
  "errors": []
}
```

各 section 含义：

- `system`
  - 运行状态、当前 active_action、可访问 URL、服务启动时间
- `motion`
  - 当前 recording、home/startup recording、recording 列表、连接状态
- `light`
  - 当前颜色、effect、brightness、最近结果
- `audio`
  - `amixer` 采样结果、音量百分比
- `voice`
  - 语音阈值、噪声底、ASR 文本、最近回复文本
- `errors`
  - 按 code/source 聚合的运行错误

---

## 12. 二次开发最常见扩展点

### 12.1 新增一个 dashboard 动作

你通常要同时改：

1. `lelamp/dashboard/api.py`
   - 新增路由
2. `lelamp/dashboard/runtime_bridge.py`
   - 新增桥接方法
3. `lelamp/dashboard/actions/*`
   - 如有需要，补 action builder / executor
4. 前端：
   - `lelamp/dashboard/web/dashboard.js`
   - `lelamp/dashboard/web/index.html`

### 12.2 新增一个 CLI 命令

改 `lelamp/remote_control.py`：

1. 写 `_handle_<name>(args)`
2. 在 `build_parser()` 里 `subparsers.add_parser(...)`
3. 如果这个命令需要与 voice agent 共存，优先走 motor bus proxy，而不是直接碰硬件

### 12.3 新增一个动作 recording

把 CSV 放进：

```text
lelamp/recordings/<name>.csv
```

然后：

- `AnimationService.get_available_recordings()` 自动可见
- dashboard / remote_control / motor bus recording list 自动带出

### 12.4 新增一个 provider

改：

1. `lelamp/runtime_config.py`
2. 新 provider 文件，例如 `lelamp/foo_realtime.py`
3. `build_realtime_model(settings)`

### 12.5 新增一个 memory 事件

这个改动成本比较高，不建议“顺手加”。

至少要一起看：

- `lelamp/memory/writer.py`
- `lelamp/memory/reader.py`
- `lelamp/memory/session.py`
- `docs/design/h1-memory-v0/*`
- 相关测试

---

## 13. 二开建议

### 推荐做法

- 复用 `DashboardRuntimeBridge` 和 `motor_bus.client`
  - 避免和 voice agent 抢硬件
- 复用 `RuntimeSettings`
  - 避免自己重复解析环境变量
- 通过 `voice_telemetry` 和 dashboard state 读运行状态
  - 不要靠日志正则做主逻辑

### 不推荐做法

- 在 voice agent 运行时自己 new 一个 `AnimationService` 抢 `/dev/ttyACM0`
- 直接改 `main.py` 做新入口
- 把旧 README 当唯一事实来源

---

## 14. Review：当前文档与代码的差异

下面这些是我在整理时确认存在的差异，后续如果继续做文档治理，应该优先修：

1. 顶层 `README.md` 和 `lelamp_runtime/README.md` 的默认 `MODEL_NAME` 不一致
   - 顶层 README 写的是 `qwen3.5-omni-plus-realtime`
   - 代码默认值和 runtime README 写的是 `qwen3.5-omni-flash-realtime`

2. 顶层 bring-up 默认路径和 runtime README 的使用入口仍然容易让人混淆
   - 顶层默认部署路径是 `~/lelamp-dev/lelamp_runtime`
   - 如果后续还要做文档治理，建议把“顶层 bring-up 路径”和“独立 runtime checkout 路径”彻底拆开写

3. `docs/design/h1-memory-v0/README.md` 标记为 `DESIGN / NOT-IMPLEMENTED`
   - 但当前仓库里已经存在 `lelamp/memory/*` 的实现代码
   - 说明设计文档和当前主线实现状态已经脱节

4. 顶层 submodule 元数据和当前实际 runtime 工作分支仍然没有完全对齐
   - 顶层仓库记录的 `lelamp_runtime` 来源 / 分支策略，和当前实际开发中的 runtime 分支并不完全一致
   - 如果后续要做发布治理，建议补一个 submodule pointer / branch policy 的 CI 校验

---

## 15. 最短实践路径

如果你只是想二开一个“自定义控制端”，我建议按这个顺序开始：

1. 跑起主程序：

```bash
cd lelamp_runtime
uv run smooth_animation.py console
```

2. 开 dashboard：

```bash
uv run -m lelamp.dashboard.api
```

3. 试 CLI：

```bash
uv run -m lelamp.remote_control show-config
uv run -m lelamp.remote_control list-recordings
uv run -m lelamp.remote_control play curious
uv run -m lelamp.remote_control solid 255 160 32
```

4. 如果你要自己写新控制逻辑，优先复用：

- `RuntimeSettings`
- `DashboardRuntimeBridge`
- `motor_bus.client.build_animation_service`
- `motor_bus.client.build_rgb_service`

---

如果你后面要，我可以继续在这份文档基础上补第二份：

- “HTTP/CLI 示例大全”
- 或 “按模块的 class / method / state schema 逐文件索引版”
