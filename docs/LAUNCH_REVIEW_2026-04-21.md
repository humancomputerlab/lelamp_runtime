# LeLamp Launch Review 2026-04-21

这份文档是针对当前仓库状态做的上线前审查摘要。

本轮按下面几条工作流整理：

- `document-release`：把二次开发入口和文档状态收口
- `review`：按上线前代码审查方式列出真实风险
- `cso`：补安全、隐私、凭据、网络暴露、供应链视角
- `qa-only`：补验证命令、测试状态、可复现实证
- `dispatching-parallel-agents`：并行拆 runtime / deploy / security 三条审查线

关联文档：

- 二次开发 API 总结：`docs/API_REFERENCE_CN.md`

---

## 结论

当前状态不建议直接上线。

原因不是“主干代码整体崩了”，而是存在几类更危险的上线问题：

1. **控制面存在 fail-open 和 split-brain 风险**
2. **默认 LAN 暴露 dashboard，且没有鉴权**
3. **bring-up / post-boot 自动化与文档承诺不一致**
4. **secret / telemetry 的落盘位置和权限不够安全**
5. **顶层仓库缺少真正覆盖上线路径的 CI gate**

好消息是，runtime 主体测试在正确命令下是健康的。

---

## 验证结果

本地验证命令：

```bash
cd lelamp_runtime
uv sync --dev
uv run --with pytest python -m pytest -q lelamp/test
uv build
```

结果：

- `uv run --with pytest python -m pytest -q lelamp/test`
  - 通过：`369 passed, 3 skipped, 7 subtests passed`
- `uv build`
  - 失败：`Multiple top-level packages discovered in a flat-layout: ['lelamp', 'assets', 'openclaw']`

注意：

- 直接跑 `uv run pytest -q` 在当前仓库里并不能代表真实测试入口
- 当前可复现、可通过的测试命令是 workflow 里那种 `PYTHONPATH=. uv run --with pytest python -m pytest ...`
- 这说明代码主体比表面健康，但**环境指令、打包方式、上线脚本**还没有完全收口

---

## Critical

### 1. Motor bus 仲裁是 fail-open 的

如果 in-process proxy 缺失，或者 `/health` 在 1 秒探测窗口内没响应，dashboard / CLI 会直接回退到“本地直接碰硬件”路径。

这会导致一个危险场景：

- voice agent 其实还持有 `/dev/ttyACM0` 或 `/dev/leds0`
- 但 proxy 因为短暂启动超时或探测超时没有被识别
- 另一个进程于是直接打开同一份硬件

结果就是硬件单 owner 边界失效，出现争用。

关键位置：

- `smooth_animation.py:292-309`
- `lelamp/motor_bus/client.py:213-260`
- `lelamp/dashboard/runtime_bridge.py:407`

建议：

- 只要检测到 live sentinel 存在但健康状态不确定，就**硬失败**，不要自动回退到直接硬件
- 只有明确判断 agent 没有占用硬件时，才允许 fallback

### 2. `MotorBusServer.start()` 可能留下“活着但没公告”的 server

`MotorBusServer.start()` 先起 uvicorn 线程，再等 3 秒 ready；如果 server 晚于这个窗口才真正 ready，函数会直接返回，既不写 sentinel，也不关闭线程。

这会造成：

- 代理服务实际上可能已经活着
- 但外部世界看不到 sentinel
- 客户端继续走 fallback，本地直接碰硬件

这是一个典型的 split-brain ownership 问题。

关键位置：

- `lelamp/motor_bus/server.py:255-297`

建议：

- 超时后主动 `stop()` 并回收线程
- 或者把 sentinel 写入与 ready 状态做成更稳的单调状态机

### 3. 本地 console 路径的 boot service 开启条件和文档矛盾

文档明确说本地 `console` 模式不需要 `LIVEKIT_*`，但 `pi5_all_in_one.sh` 在决定是否启用 boot service 时，仍然强制要求三个 `LIVEKIT_*` 变量全部存在。

这会直接破坏仓库宣称的“一条命令 bring-up 本地 console 模式”。

关键位置：

- `scripts/pi5_all_in_one.sh:411-425`
- `scripts/pi5_all_in_one.sh:514-518`
- `.env.example:17-21`
- `README.md` 中关于 local console 的描述

建议：

- 把“本地 console 模式”与“LiveKit room 模式”分开判断
- `MODE_SCRIPT + console` 路径不应被 `LIVEKIT_*` 阻断

### 4. post-boot 自动下载步骤会在 fresh Pi 上被静默跳过

`pi_setup_max.sh` 把 `uv` 安装到普通用户的 `~/.local/bin`，但 post-boot service 以 `root` 身份运行，`$HOME` 会变成 `/root`。`pi5_post_reboot_finalize.sh` 只有在 `command -v uv` 成功时才跑 `download-files`。

结果是：

- 文档承诺“重启后自动跑 download-files 并生成报告”
- fresh Pi 上这个最关键步骤可能根本没跑
- 但服务仍然看起来执行成功

关键位置：

- `scripts/pi_setup_max.sh:185-189`
- `scripts/pi5_all_in_one.sh:427-452`
- `scripts/pi5_post_reboot_finalize.sh:18-21`
- `scripts/pi5_post_reboot_finalize.sh:51-52`

建议：

- 在 post-boot env 里显式写入 `UV_BIN`
- 或以原始普通用户执行 post-boot 下载步骤
- 并让 `download-files` 失败时带出非零退出码

---

## High

### 5. Dashboard 默认对整个局域网暴露控制面，而且没有鉴权

`LELAMP_DASHBOARD_HOST` 默认是 `0.0.0.0`，API 又直接暴露了未鉴权的动作和灯光 POST 接口。

这意味着同一局域网内任意主机，只要能访问这个端口，就可以：

- 触发动作
- 触发灯光变化
- 间接控制实体设备行为

关键位置：

- `lelamp/runtime_config.py:206`
- `.env.example:61`
- `lelamp/dashboard/api.py:188-255`
- `lelamp/dashboard/api.py:264-268`

建议：

- 默认改成 `127.0.0.1`
- LAN 暴露必须显式 opt-in
- 如果要保留 LAN 控制，至少加一个简单 token / basic auth / reverse proxy auth

### 6. 同一个 dashboard 还会把最近语音内容暴露到网络上

`/api/state` 返回完整 snapshot，其中 `voice.last_asr_text` 和 `voice.last_reply_text` 会被带出来。

如果 dashboard 默认 LAN 可访问，那么局域网内的设备还能轮询到最近的用户语音和回复文本。

关键位置：

- `lelamp/dashboard/state_store.py:48-61`
- `lelamp/dashboard/api.py:188`

建议：

- 默认从网络 API 去掉 transcript 字段
- 只在 debug 模式返回
- 或单独放到仅本机可见的调试接口

### 7. zero-touch bootfs 会把 Wi‑Fi 和模型密钥留在可拆卸启动分区

`host_tools/pi5_zero_touch_seed.sh` 会把：

- `BOOTSTRAP_WIFI_PASSWORD`
- `MODEL_API_KEY`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

写进 bootfs 上的 `lelamp-bootstrap.env`。脚本会在首启时复制一份到 `/etc/lelamp/lelamp-bootstrap.env` 并设成 `0600`，但当前没有看到对 bootfs 原文件的清理。

这意味着：

- SD 卡一旦被拿走
- 启动分区上仍可能残留敏感信息

关键位置：

- `host_tools/pi5_zero_touch_seed.sh:278-312`
- `host_tools/pi5_zero_touch_seed.sh:325-343`

建议：

- 首启成功后删除 bootfs 上的 `lelamp-bootstrap.env`
- 同时删除 staged `authorized_keys`
- 更稳的方案是改成一次性 bootstrap token

### 8. 当前仓库并不能作为 Python package 正常 build

`uv build` 直接失败，因为 flat-layout 下发现了多个 top-level package：

- `lelamp`
- `assets`
- `openclaw`

这说明当前 runtime 项目没有完成明确的 package/build 边界定义。

关键位置：

- `pyproject.toml:1-40`

建议：

- 明确 `[build-system]`
- 加 `tool.setuptools.packages.find`
- 或把非 Python 目录从 package discovery 里显式排除

### 9. 顶层仓库没有覆盖 runtime / bring-up / submodule 指针的 CI gate

顶层 repo 目前只有 Pages workflow；runtime 的 pytest workflow 在 `lelamp_runtime` 子仓库里。

这意味着顶层仓库可以：

- 改 bring-up 脚本
- 改文档
- 改 submodule 指针

但这些改动不一定在顶层被 CI 拦住。

关键位置：

- `/.github/workflows/pages.yml`
- `lelamp_runtime/.github/workflows/pytest.yml`

建议：

- 顶层仓库至少加一个 workflow：
  - 校验 submodule 已初始化
  - 跑 bring-up 脚本的静态检查
  - 调用 runtime 的测试工作流或本地 smoke 命令

---

## Medium

### 10. `.env` 和 voice telemetry 的权限边界不够安全

问题一：

- runtime secrets 会被写进 repo-local `.env`
- 当前脚本没有强制 `0600`
- 常见 `umask 022` 下可能落成 `0644`

问题二：

- voice telemetry 默认落在 `/tmp/lelamp-voice-state.json`
- 写入时没有显式 `chmod(0600)`
- 最近 ASR / reply 文本可能被本机其他用户读到

关键位置：

- `scripts/pi_setup_max.sh:197-205`
- `scripts/pi5_all_in_one.sh:494-512`
- `scripts/pi_setup_max.sh:252`
- `lelamp/voice_telemetry.py:32`
- `lelamp/voice_telemetry.py:87-95`

建议：

- `.env` 创建和更新后立刻 `chmod 600`
- `voice telemetry` 移到 `$HOME/.local/state/lelamp/`
- flush 后强制文件权限为 `0600`

### 11. `LELAMP_VOICE_STATE_PATH` 配置不能稳定生效

`configure_voice_telemetry(path)` 支持自定义路径，但后续 runtime 又通过 `get_voice_telemetry()` 把全局 store 重置回默认 `/tmp/lelamp-voice-state.json`。

结果是：

- 你以为已经配置了自定义路径
- 但后续某些模块又开始往默认路径写
- dashboard、voice runtime、auto-expression 可能读写不同文件

关键位置：

- `lelamp/runtime_config.py:247`
- `lelamp/voice_telemetry.py:102-113`

建议：

- `get_voice_telemetry()` 不应重置路径
- 应只返回当前已配置 store；未配置时再初始化默认路径

### 12. RGB 没有降级路径，灯光驱动坏掉会拖垮 agent 启动

`RGBService` 初始化如果既没有 `/dev/leds0` 也没有 `rpi_ws281x`，会直接 raise。`LeLamp.__init__` 又在 try/except 外面 new 这个服务。

结果是：

- 灯光栈坏掉
- 语音 runtime 也起不来

关键位置：

- `smooth_animation.py:59-78`
- `lelamp/service/rgb/rgb_service.py:37`

建议：

- RGB 初始化失败时降级成 `rgb unavailable`
- 不应阻断 motion/audio 主链

### 13. `ServiceBase` 可能吞事件

当前 `dispatch()` 会直接覆盖 `_current_event`，而 `_event_loop()` 在 `finally` 无条件把 `_current_event = None`。

如果 `handle_event()` 执行期间又来了一个更高优先级事件，这个新事件有可能在 `finally` 被直接清掉。

关键位置：

- `lelamp/service/base.py:37-49`
- `lelamp/service/base.py:78-95`

建议：

- 换成真正的队列模型
- 或在 worker 取出 event 后立即清空当前槽位，再处理事件

### 14. `sync_pi_runtime.sh` 不是可复现部署流程

它依赖远端已有 `./.venv/bin/python`，然后直接 `pip install fastapi uvicorn`，绕过 lockfile，只跑两个 dashboard smoke test。

这更像临时开发脚本，不像上线同步脚本。

关键位置：

- `scripts/sync_pi_runtime.sh:48-57`

建议：

- 用 `uv sync` 或固定 lockfile 安装
- 把 smoke test 扩到 runtime 关键路径

### 15. 文档和默认值有明显漂移

已经确认的几个例子：

1. 顶层 `README.md` 写的是 `qwen3.5-omni-plus-realtime`
2. `runtime_config.py` 默认值是 `qwen3.5-omni-flash-realtime`
3. `lelamp_runtime/README.md` 说 qwen 默认 `server_vad`
4. 代码默认 `LELAMP_QWEN_USE_SERVER_VAD=false`
5. `docs/design/h1-memory-v0/README.md` 仍写着 `DESIGN / NOT-IMPLEMENTED`
6. 但 `lelamp/memory/*` 已经有真实实现

关键位置：

- `../README.md:80-86`
- `README.md:77-104`
- `lelamp/runtime_config.py:133-215`
- `docs/design/h1-memory-v0/README.md:1-23`
- `lelamp/memory/__init__.py:1-15`

建议：

- 把“当前代码真实默认值”和“设计稿/历史文档”彻底分开
- 顶层 README 增加一段“二次开发入口在 `lelamp_runtime/`”

### 16. CI / dev 依赖表达不够一致

`pyproject.toml` 把 `httpx/js2py` 放在 `optional-dependencies.dev`，但 workflow 用的是 `uv sync --dev`，这两者语义并不一致。

当前 workflow 还能跑通，是因为下一步又用 `uv run --with pytest` 临时补了 pytest。

这不是立即阻塞上线的问题，但会让后续开发者误判“正确的本地测试入口”。

关键位置：

- `pyproject.toml:26-40`
- `.github/workflows/pytest.yml:29-33`

建议：

- 统一成真正的 dependency group
- 或统一使用 `uv sync --extra dev`
- 并把 README / 开发文档里的测试命令只保留一种

---

## Residual Risk

即便把上面的问题都修掉，这个项目仍有一个无法回避的系统级风险：

- 语音模型被明确授权直接调用动作 / 灯光 tool
- 没有确认环节
- 没有 wake-word / user-presence / policy gate 之类的硬约束

如果你的上线场景是：

- 不受信任的音频环境
- 公共空间
- 人会故意逗它触发动作

那么还需要额外的“模型到硬件”的安全层。

相关位置：

- `lelamp/voice_profile.py`
- `smooth_animation.py:103-236`

---

## 建议修复顺序

### Launch blockers

1. 修 motor bus fail-open / split-brain
2. 修 console boot service 对 `LIVEKIT_*` 的错误前置要求
3. 修 post-boot `uv` 路径和失败退出码
4. 把 dashboard 默认 host 改成 `127.0.0.1`
5. 去掉 `/api/state` 默认返回 transcript
6. 修 `.env` / telemetry 文件权限
7. 修 bootfs secret 清理

### Shipping hygiene

1. 修 `uv build`
2. 顶层加 CI gate
3. 收口 README / design doc 漂移
4. 重写 `sync_pi_runtime.sh` 的依赖安装和 smoke 流程

---

## 当前可用资产

尽管上面问题不少，当前仓库也不是“不能用”：

- runtime 测试主干是健康的
- bring-up 脚本对 Pi 5 / ReSpeaker V1 这些高风险硬件路径是偏保守的
- 已经有 `lelamp_doctor.sh`
- 二次开发 API 总结已经单独整理在 `docs/API_REFERENCE_CN.md`

如果你要继续，我建议下一步直接进入“按 blocker 顺序修复并复测”的模式。
