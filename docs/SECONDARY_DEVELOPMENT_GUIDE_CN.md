# LeLamp Runtime 二次开发指南

这份文档面向“想改行为的人”，重点不是介绍架构，而是告诉你：

- 想接入控制，要从哪里进
- 想加动作、灯光、API，要改哪些文件
- 改完后最少要验证什么

建议搭配 [TECHNICAL_OVERVIEW_CN.md](./TECHNICAL_OVERVIEW_CN.md) 一起看。

## 1. 先选入口，不要乱改

这个项目有好几层入口，但用途不同：

- 想改 LLM 可调用工具
  - 看 [`smooth_animation.py`](../smooth_animation.py)
- 想加 CLI / OpenClaw 命令
  - 看 [`lelamp/remote_control.py`](../lelamp/remote_control.py)
- 想加 dashboard 按钮或 HTTP 动作
  - 看 [`lelamp/dashboard/`](../lelamp/dashboard)
- 想加动作录制
  - 看 [`lelamp/recordings/`](../lelamp/recordings)
- 想改情绪到动作/灯光的映射
  - 看 [`lelamp/expression_engine.py`](../lelamp/expression_engine.py)
- 想扩 memory / conversation logging
  - 看 [`lelamp/memory/runtime.py`](../lelamp/memory/runtime.py)

先确定你改的是“控制面”还是“底层硬件服务”，不要一上来就直接碰 `service/`。

## 2. 本地开发的最小命令集

常用的最小验证命令：

```bash
uv run -m lelamp.remote_control show-config
uv run -m lelamp.remote_control list-recordings
uv run -m lelamp.dashboard.api
uv run smooth_animation.py console
```

建议习惯：

- 改配置解析前，先跑 `show-config`
- 改动作/灯光前，先用 `remote_control` 做单点验证
- 改 dashboard 前，先本地起 `lelamp.dashboard.api`
- 改 agent 行为前，最后再跑 `smooth_animation.py console`

## 3. 改配置的正确位置

统一配置入口只有一个：

- [`lelamp/runtime_config.py`](../lelamp/runtime_config.py)

如果你要加新环境变量，通常要一起改：

1. `RuntimeSettings`
2. `load_runtime_settings()`
3. 相关测试
4. README / 本文档 / API_REFERENCE

不要在别的模块里随手 `os.getenv()` 再来一套局部协议，后面很容易漂。

## 4. 新增一个动作录制

如果你只是想让系统“多一个可播放动作”，成本最低的方式是新增 recording。

### 4.1 文件位置

把 csv 放到：

- [`lelamp/recordings/`](../lelamp/recordings)

### 4.2 自动获得的能力

只要 recording 文件存在，它通常会自然出现在：

- `remote_control list-recordings`
- dashboard 可播放动作列表
- `AnimationService.get_available_recordings()`

### 4.3 如果还想让 LLM 主动用它

要继续改：

- [`lelamp/expression_engine.py`](../lelamp/expression_engine.py)
  - 如果是既有情绪的更好映射
- 或 [`smooth_animation.py`](../smooth_animation.py)
  - 如果你想新增一个独立 tool

## 5. 新增一个表达风格

如果你希望角色层支持新的“高层表达”，改这里：

- [`lelamp/expression_engine.py`](../lelamp/expression_engine.py)

通常步骤是：

1. 在 `ExpressionStyle` 里加新 literal
2. 更新 `EXPRESSION_STYLE_CHOICES`
3. 在 `build_expression_plan()` 里给出：
   - `recording_name`
   - `solid_rgb`
   - 或 `pattern_rgb`
4. 视情况补测试

优先在这一层扩，不要让 prompt 直接背 recording 名。

## 6. 新增一个 LLM tool

如果你需要给语音 agent 一个新工具，改：

- [`smooth_animation.py`](../smooth_animation.py)

做法通常是：

1. 在 `LeLamp` 里新增一个 `@function_tool`
2. 复用已有服务对象
   - `self.animation_service`
   - `self.rgb_service`
3. 在工具 docstring 里写清楚：
   - 什么时候该用
   - 参数含义
   - 不该怎么用
4. 如果这会影响 auto-expression 节奏，视情况调用 `self._note_expression_tool_dispatch()`

如果工具还会改变角色默认话术，继续看：

- [`lelamp/voice_profile.py`](../lelamp/voice_profile.py)

## 7. 新增一个 CLI / OpenClaw 命令

入口在：

- [`lelamp/remote_control.py`](../lelamp/remote_control.py)

当前已有命令包括：

- `show-config`
- `list-recordings`
- `play`
- `solid`
- `clear`
- `capture-pose`
- `sync-pose-recordings`
- `startup`
- `shutdown`

新增命令时，一般要改：

1. 一个 `_handle_xxx(args)` 函数
2. `build_parser()` 里的 `add_parser(...)`
3. 如果需要复用 dashboard/agent 已持有的硬件 owner，优先经由：
   - `_build_animation_service_with_proxy(...)`
   - `_build_rgb_service_with_proxy(...)`

不要绕开 motor bus 直接去 new 一份服务，除非你非常确定当前没有 live sentinel。

## 8. 新增一个 dashboard 动作

dashboard 不是只改一个按钮就结束，通常要过 4 层：

1. HTTP 路由
   - [`lelamp/dashboard/api.py`](../lelamp/dashboard/api.py)
2. action registry
   - [`lelamp/dashboard/actions/motion.py`](../lelamp/dashboard/actions/motion.py)
   - [`lelamp/dashboard/actions/lights.py`](../lelamp/dashboard/actions/lights.py)
3. bridge 逻辑
   - [`lelamp/dashboard/runtime_bridge.py`](../lelamp/dashboard/runtime_bridge.py)
4. 前端展示
   - `lelamp/dashboard/web/`

如果新动作需要显示状态，还要继续改：

- [`lelamp/dashboard/state_store.py`](../lelamp/dashboard/state_store.py)
- 对应 sampler

经验规则：

- “动作能不能做”放在 bridge 判定
- “一次只允许一个动作”交给 executor
- “按钮怎么显示”放在 API catalog + 前端

## 9. 新增一个 motor bus 能力

如果你要让 dashboard / CLI 共享 agent 进程内部已有的服务，就要改 motor bus。

涉及文件通常是：

1. 服务端定义
   - [`lelamp/motor_bus/server.py`](../lelamp/motor_bus/server.py)
2. 客户端代理
   - [`lelamp/motor_bus/client.py`](../lelamp/motor_bus/client.py)
3. 上层消费方
   - [`lelamp/dashboard/runtime_bridge.py`](../lelamp/dashboard/runtime_bridge.py)
   - [`lelamp/remote_control.py`](../lelamp/remote_control.py)

关键约束：

- loopback only
- sentinel 只是 ownership 广告，不是安全认证
- live sentinel 存在但 `/health` 不确定时要 fail closed

如果你新增 endpoint，却没有同步 client / bridge / tests，后面很容易只在一层可用。

## 10. 扩 memory / telemetry

### 10.1 Memory

如果你要记录新的 conversation / tool / fallback 事件，优先从：

- [`lelamp/memory/runtime.py`](../lelamp/memory/runtime.py)

开始，再看：

- `writer.py`
- `reader.py`
- `session.py`

原则：

- runtime 侧保持 no-throw
- session listener 不能把主流程带崩
- 新事件类型要考虑 reader/schema 兼容

### 10.2 Voice telemetry

如果你想让 dashboard 显示新的语音字段，通常要一起改：

1. [`lelamp/voice_telemetry.py`](../lelamp/voice_telemetry.py)
2. `dashboard/samplers/voice.py`
3. [`lelamp/dashboard/state_store.py`](../lelamp/dashboard/state_store.py)
4. `dashboard/web/dashboard.js`

注意：

- 默认 transcript 是隐藏的
- 文件权限需要保持私有

## 11. 改 prompt / 角色风格

角色文案与工具使用倾向主要在：

- [`lelamp/voice_profile.py`](../lelamp/voice_profile.py)

如果你只想让角色更活泼、有梗、更像第一人称，而不是第三者插话，优先改这里和 tool docstring，不要先去改硬件服务层。

## 12. 测试建议

最低建议是按改动范围跑目标测试，再跑全套：

```bash
uv run --with pytest python -m pytest -q lelamp/test/test_runtime_config.py
uv run --with pytest python -m pytest -q lelamp/test/test_dashboard_api.py
uv run --with pytest python -m pytest -q lelamp/test/test_dashboard_runtime_bridge.py
uv run --with pytest python -m pytest -q lelamp/test/test_motor_bus_client.py
uv run --with pytest python -m pytest -q lelamp/test/test_remote_control.py
uv run --with pytest python -m pytest -q lelamp/test
uv build
```

如果你动的是：

- config / 默认值：一定要跑 `test_runtime_config.py`
- dashboard / bridge：一定要跑 `test_dashboard_api.py` + `test_dashboard_runtime_bridge.py`
- motor bus ownership：一定要跑 `test_motor_bus_client.py`
- CLI / OpenClaw：一定要跑 `test_remote_control.py`

## 13. 常见误区

### 13.1 直接在多个入口各写一套逻辑

正确做法是：

- 把底层行为收进 runtime service / bridge / proxy
- 让 dashboard、CLI、agent tool 复用它

### 13.2 看到 sentinel 就默认安全

不对。真正要看的不是“有没有 sentinel”，而是：

- sentinel 是否 live
- `/health` 是否能通
- 对应 domain 是否健康

### 13.3 文档只改 README

当前仓库更适合把文档分层：

- README 讲入口和导航
- 技术总览讲架构
- 二开指南讲改法
- API_REFERENCE 讲字典式接口

## 14. 推荐改法

如果你准备做一个新功能，通常按这个顺序最稳：

1. 先决定它属于哪一层
2. 先补或改测试
3. 再改最小实现
4. 再补 README / 本文档 / API_REFERENCE
5. 最后跑全套测试和 `uv build`

---

一句话总结：二次开发最重要的不是“哪里能改”，而是“改完之后还能继续保持单 owner、单入口、单配置协议”。
