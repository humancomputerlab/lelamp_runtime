# LeLamp Runtime 技术总览

这份文档回答两个问题：

1. 这个 runtime 实际上由哪些进程内组件和控制面组成。
2. 你要改一个行为时，应该去哪个模块下手。

如果你是第一次接手仓库，建议阅读顺序：

1. 本文
2. [SECONDARY_DEVELOPMENT_GUIDE_CN.md](./SECONDARY_DEVELOPMENT_GUIDE_CN.md)
3. [API_REFERENCE_CN.md](./API_REFERENCE_CN.md)

## 1. 边界与仓库关系

真正的运行时代码在 `lelamp_runtime/`，顶层 `Lelamp/` 负责：

- Pi 5 bring-up
- zero-touch / bootfs 种子
- OpenClaw 安装与外层集成
- Pages/站点和外围文档

如果你要理解动作、灯光、语音、dashboard、memory 或 motor bus，默认都应该从 `lelamp_runtime/` 看起。

## 2. 启动链路

当前推荐主入口是 [`smooth_animation.py`](../smooth_animation.py)。

启动顺序可以概括为：

1. `load_dotenv()` 加载 `.env`
2. [`lelamp/runtime_config.py`](../lelamp/runtime_config.py) 解析出 `RuntimeSettings`
3. `LeLamp` 初始化硬件服务
   - `AnimationService`
   - `RGBService`
   - 开机动作 / 开机灯光 / 系统音量
4. `entrypoint(ctx)` 继续装配运行时附属层
   - `AutoExpressionController`
   - `MotorBusServer`
   - `AgentMemoryRuntime`
   - `AgentSession`
5. 控制面再通过 dashboard / CLI / motor bus 访问这些长生命周期服务

兼容入口 [`main.py`](../main.py) 仍然存在，但只是向 `smooth_animation.py` 转发，不应该再作为新功能主入口。

## 3. 运行时分层

### 3.1 Agent 层

[`smooth_animation.py`](../smooth_animation.py) 中的 `LeLamp` 继承自 `livekit.agents.Agent`，它负责：

- 持有 `AnimationService` 和 `RGBService`
- 暴露 LLM 可调用的 `@function_tool`
- 安装语音、auto-expression、memory、motor bus

这是“角色人格 + 工具表面”的核心层。

### 3.2 硬件服务层

动作服务和灯光服务分别在：

- `lelamp/service/motors/`
- `lelamp/service/rgb/`

这些模块是直接碰硬件的地方。正常情况下，同一时刻只能有一个 owner。

### 3.3 代理层

[`lelamp/motor_bus/server.py`](../lelamp/motor_bus/server.py) 在 agent 进程内起一个 loopback FastAPI server，把 agent 已经持有的服务再暴露给本机其它进程。

[`lelamp/motor_bus/client.py`](../lelamp/motor_bus/client.py) 负责：

- 读取 sentinel
- 探测 `/health`
- 返回 `ProxyAnimationService` / `ProxyRGBService`
- 在 ownership 不确定时 fail closed，而不是偷偷回退到直连硬件

这是整个 runtime 避免“多进程同时抢 `/dev/ttyACM0` / `/dev/leds0`”的关键层。

### 3.4 控制面层

系统当前有三套主要控制面：

- LLM tool surface
  - 定义在 [`smooth_animation.py`](../smooth_animation.py)
- 本地 dashboard
  - 入口在 [`lelamp/dashboard/api.py`](../lelamp/dashboard/api.py)
- CLI / OpenClaw 调用入口
  - 定义在 [`lelamp/remote_control.py`](../lelamp/remote_control.py)

这三套控制面最终都应该尽量落到同一套 runtime 服务，而不是各自复制一份硬件逻辑。

## 4. Dashboard 架构

dashboard 由四层构成：

1. API 入口
   - [`lelamp/dashboard/api.py`](../lelamp/dashboard/api.py)
2. 串行执行器
   - [`lelamp/dashboard/actions/executor.py`](../lelamp/dashboard/actions/executor.py)
3. 运行时桥接层
   - [`lelamp/dashboard/runtime_bridge.py`](../lelamp/dashboard/runtime_bridge.py)
4. 状态与采样
   - [`lelamp/dashboard/state_store.py`](../lelamp/dashboard/state_store.py)
   - [`lelamp/dashboard/samplers/`](../lelamp/dashboard/samplers)

关键设计点：

- `DashboardActionExecutor` 保证一次只跑一个动作
- `DashboardRuntimeBridge` 负责决定“走代理”还是“走直连 fallback”
- `DashboardStateStore` 保存前端消费的统一状态快照
- `DashboardSamplerLoop` 定期采 system / motion / audio / voice

## 5. 状态面与数据面

### 5.1 DashboardStateStore

[`lelamp/dashboard/state_store.py`](../lelamp/dashboard/state_store.py) 维护了 dashboard 的统一状态树，核心 section 有：

- `system`
- `motion`
- `light`
- `audio`
- `voice`
- `errors`

如果你要给 dashboard 加字段，通常要同时改：

1. `state_store.py` 默认结构
2. 对应 sampler 或 action patch
3. `dashboard.js` / 前端展示

### 5.2 VoiceTelemetryStore

[`lelamp/voice_telemetry.py`](../lelamp/voice_telemetry.py) 是语音状态的轻量文件化快照层。

当前约束：

- 默认路径是 `/tmp/lelamp-voice-state.json`
- `configure_voice_telemetry()` 可以切到自定义路径
- flush 后文件权限会收成 `0600`
- `get_voice_telemetry()` 会保留已经配置过的全局 store，而不是重置回默认路径

### 5.3 Memory Runtime

[`lelamp/memory/runtime.py`](../lelamp/memory/runtime.py) 是 runtime 与 memory 子系统之间的唯一热路径接缝。

它的设计目标不是“功能最多”，而是：

- 对主 runtime 尽量 no-throw
- session listener 出错不能把 agent 带崩
- memory 可以显式禁用

如果你要扩 memory，不要直接在语音主热路径到处写文件，优先通过这里收口。

## 6. 行为编排层

### 6.1 录制动作

动作录制文件放在 [`lelamp/recordings/`](../lelamp/recordings)。

这些 recording 会被：

- `AnimationService`
- `remote_control list-recordings/play`
- dashboard 可用动作列表
- expression planner

共同消费。

### 6.2 情绪表达

[`lelamp/expression_engine.py`](../lelamp/expression_engine.py) 把高层情绪风格映射成：

- 一个 recording
- 或一个 solid RGB
- 或一个 pattern RGB

如果你希望“角色表达层”更丰富，优先在这里抽象，不要在 prompt 里硬编码具体 recording 名。

### 6.3 Auto-expression

[`lelamp/auto_expression.py`](../lelamp/auto_expression.py) 负责在合适时机自动触发表达，并把 fallback 写回 memory。

这层更像“运行时策略”，不是基础硬件 API。

## 7. 配置模型

统一配置入口在 [`lelamp/runtime_config.py`](../lelamp/runtime_config.py)。

几个重要事实：

- 模型 provider 统一用 `MODEL_PROVIDER`
- `MODEL_API_KEY` 是主配置键，但保留旧 key 兼容回退
- dashboard 默认监听 `127.0.0.1`
- dashboard 默认不暴露 `last_asr_text` / `last_reply_text`
- `LELAMP_QWEN_USE_SERVER_VAD` 当前默认是 `false`

当 README、`.env.example` 和代码冲突时，以这里和对应测试为准。

## 8. 关键不变量

维护这个项目时，最好把下面几条当成硬约束：

### 8.1 单一硬件 owner

动作串口和 LED 设备不能被多个进程同时占用。

因此：

- live sentinel 存在且 `/health` 正常时，应该走 motor bus proxy
- live sentinel 存在但健康状态不确定时，应该 fail closed
- 只有明确没有 live sentinel 时，才允许 fallback 到直接硬件

### 8.2 Dashboard 默认本地优先

dashboard 不是默认公网/局域网暴露面。

因此：

- 默认 `LELAMP_DASHBOARD_HOST=127.0.0.1`
- transcript 默认隐藏
- 要显式开放才允许跨设备访问

### 8.3 Memory 是附属层，不是主生命线

memory 出问题时：

- 不能影响 Pi uptime
- 不能让语音主环或动作主环崩掉

### 8.4 文档要跟测试保持一致

这个仓库最近很多真实问题都不是代码逻辑本身，而是“README 说的默认值”和“代码默认值”不一致。

最容易漂移的面有：

- dashboard host / transcript exposure
- Qwen/GLM server VAD 默认值
- model 默认 SKU
- bring-up 路径和 submodule 约定

## 9. 模块导航

如果你准备修改某类行为，通常从这里进入：

- 语音模型 / provider
  - [`lelamp/runtime_config.py`](../lelamp/runtime_config.py)
  - [`lelamp/qwen_realtime.py`](../lelamp/qwen_realtime.py)
  - [`lelamp/glm_realtime.py`](../lelamp/glm_realtime.py)
- LLM 工具表面
  - [`smooth_animation.py`](../smooth_animation.py)
- 角色 prompt / 回复风格
  - [`lelamp/voice_profile.py`](../lelamp/voice_profile.py)
- 情绪动作映射
  - [`lelamp/expression_engine.py`](../lelamp/expression_engine.py)
- CLI / OpenClaw 控制
  - [`lelamp/remote_control.py`](../lelamp/remote_control.py)
- Dashboard
  - [`lelamp/dashboard/`](../lelamp/dashboard)
- Motor bus
  - [`lelamp/motor_bus/`](../lelamp/motor_bus)
- Memory
  - [`lelamp/memory/`](../lelamp/memory)
- Pi bring-up / systemd / post-boot
  - [`scripts/`](../scripts)

## 10. 最小维护检查

每次动完核心控制链，至少做这几步：

```bash
uv run --with pytest python -m pytest -q lelamp/test
uv build
```

如果你只改某一层，优先先跑对应目标测试，例如：

- dashboard: `test_dashboard_api.py`, `test_dashboard_runtime_bridge.py`
- motor bus: `test_motor_bus_client.py`, `test_motor_bus_server.py`
- remote control: `test_remote_control.py`
- config / telemetry: `test_runtime_config.py`, `test_voice_telemetry.py`

---

一句话总结：这个 runtime 的本质不是“一个会说话的脚本”，而是“一个持有硬件 owner 的 agent 进程 + 若干安全控制面 + 若干可替换的策略层”。
