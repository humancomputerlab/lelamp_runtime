# LeLamp Runtime for Pi 5

这个目录已经按 `Pi 5 + LeLamp + OpenClaw` 做过本地增强，不是上游原样。

仓库级说明、Pages 入口和开发文档在上一级目录：

- `../README.md`
- `../DEVELOPMENT_GUIDE_PI5.md`
- `../site/index.html`

## 一句话入口

在树莓派上执行：

```bash
cd ~/lelamp_runtime
chmod +x scripts/pi5_all_in_one.sh
./scripts/pi5_all_in_one.sh
```

这是当前推荐的唯一入口。

## 目录里现在最重要的东西

### 安装与编排脚本

- [scripts/pi5_all_in_one.sh](./scripts/pi5_all_in_one.sh)
  总控入口，负责 Pi 5 检测、`.env`、LeLamp、OpenClaw、post-boot finalizer。
- [scripts/pi_setup_max.sh](./scripts/pi_setup_max.sh)
  处理 LeLamp runtime、依赖、音频 overlay、`ws2812-pio` LED overlay 持久化、可选 systemd 服务。
- [scripts/openclaw_pi5_setup.sh](./scripts/openclaw_pi5_setup.sh)
  处理 OpenClaw、可选 Tailscale、可选 onboarding。
- [scripts/install_openclaw_skill.sh](./scripts/install_openclaw_skill.sh)
  把 LeLamp 的 OpenClaw skill 装到 `~/.openclaw/skills`。
- [scripts/pi5_post_reboot_finalize.sh](./scripts/pi5_post_reboot_finalize.sh)
  重启后自动跑一遍设备检查和可选下载步骤。

### 运行时配置

- [.env.example](./.env.example)
  环境变量模板。
- [lelamp/runtime_config.py](./lelamp/runtime_config.py)
  统一读取运行时配置。
- [main.py](./main.py)
  离散动作模式。
- [smooth_animation.py](./smooth_animation.py)
  平滑动作模式，默认推荐。

### OpenClaw 集成

- [lelamp/remote_control.py](./lelamp/remote_control.py)
  给 OpenClaw 调用的安全高层控制 CLI。
- [openclaw/skills/lelamp-control/SKILL.md](./openclaw/skills/lelamp-control/SKILL.md)
  OpenClaw 技能模板。

## 当前默认硬件假设

- Raspberry Pi 5
- Raspberry Pi OS Lite 64-bit
- ReSpeaker 2-Mics Pi HAT V2.0
- 8x5 WS2812B = 40 LEDs
- 5x STS3215
- TTL servo driver

如果你的 `ReSpeaker` 不是 `V2.0`，最重要的变量是：

```bash
RESPEAKER_VARIANT=auto|v2|v1|skip
```

默认是 `auto`，在 `Pi 5` 上会优先走 `v2`。

## 环境变量

最关键的是这几个：

```bash
MODEL_PROVIDER=qwen
MODEL_API_KEY=
MODEL_BASE_URL=https://dashscope.aliyuncs.com/api-ws/v1/realtime
MODEL_NAME=qwen3.5-omni-flash-realtime
MODEL_VOICE=Tina
LELAMP_AGENT_LANGUAGE=zh-CN
LELAMP_AGENT_OPENING_LINE=灯灯醒了。
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LELAMP_ID=lelamp
LELAMP_PORT=/dev/ttyACM0
LELAMP_AUDIO_USER=pi
HF_LEROBOT_CALIBRATION=/home/pi/.cache/huggingface/lerobot/calibration
LELAMP_LED_COUNT=40
LELAMP_ENABLE_RGB=true
```

说明：

- `MODEL_*` 是当前仓库的标准配置
- `MODEL_PROVIDER=qwen` 是当前默认路径，直接走 DashScope 的官方 realtime websocket
- `DASHSCOPE_API_KEY` / `QWEN_API_KEY`、`ZAI_API_KEY` 和 `OPENAI_API_KEY` 都保留兼容回退，但不再是主配置键
- `LELAMP_AGENT_LANGUAGE` 和 `LELAMP_AGENT_OPENING_LINE` 控制默认对话语言和开机第一句
- `HF_LEROBOT_CALIBRATION` 建议显式指向你当前用户的 calibration 目录，这样即使 runtime 用 `root` 跑灯光服务，也不会丢掉 follower 校准
- `LELAMP_ENABLE_RGB=false` 可以临时关闭 LED 路径，隔离音频、动作和语音问题
- `qwen` 默认走服务端 `server_vad`，也就是直接说话就会断句，不需要再额外做唤醒词

如果你想接树莓派本地模型，目前前提是：

```bash
MODEL_PROVIDER=custom
MODEL_BASE_URL=http://127.0.0.1:8000/v1/realtime
MODEL_NAME=your-local-model
```

注意：

- 当前这套代码仍然走 `livekit + openai.realtime.RealtimeModel`
- 所以本地模型必须暴露一个 OpenAI 兼容的 realtime 接口
- 如果你的本地模型只有普通 `/v1/chat/completions`，那不能直接替换进来，得另做一套 STT + LLM + TTS 管线

## Manager + Item Layer v1

runtime 现在多了一条独立于 realtime speaker 的慢速 sidecar 路径：

- `speaker` 继续负责低延迟语音回复
- `item layer` 把用户发言、回复、tool invoke/result 镜像成 `lelamp.item.v1` JSONL
- `manager` 读取当前 session items，生成派生 snapshot
- `action DSL` 先约束 scene primitive，再 lower 到现有 recording 和 RGB `solid` / `paint`

这层默认不会替代原始 memory ledger。`events.jsonl` 仍然是 source of truth，manager snapshot 只是可重建的 derived sidecar。

关键变量：

```bash
LELAMP_MANAGER_PROVIDER=glm
LELAMP_MANAGER_MODEL=glm-4.5-air
LELAMP_MANAGER_API_KEY=
LELAMP_MANAGER_BASE_URL=
LELAMP_ITEM_STORE_PATH=/tmp/lelamp-items.jsonl
```

运行时行为：

- manager snapshot 默认写到用户 memory 根目录下的 `derived/manager_snapshot.v1.json`
- speaker prompt 启动时会优先尝试加载这个 snapshot，再拼接 memory header
- scene plan 当前只会编译到现有安全能力，不会直接下发原始舵机轨迹

## Pi 5 LED 路径

Pi 5 上默认走官方 `ws2812-pio` 驱动，不走 `rpi_ws281x` DMA 路径。

ReSpeaker V2 的默认 `/etc/asound.conf` 现在会写成 `dmix/dsnoop + plug` 结构，目的是让 root 启动的 realtime 语音链也能稳定打开 `24kHz mono` 输入输出，而不是只看 codec 的理论上限。

`scripts/pi_setup_max.sh` 会把下面这行持久化到 `/boot/firmware/config.txt`：

```bash
dtoverlay=ws2812-pio,gpio=12,num_leds=40
```

重启以后应当满足：

```bash
ls -l /dev/leds0
sudo uv run -m lelamp.remote_control solid 255 160 32
```

如果你改了灯板数量或信号脚，安装时覆盖：

```bash
LED_PIN=12 LED_COUNT=40 ./scripts/pi_setup_max.sh
```

## OpenClaw 该怎么理解

OpenClaw 在这套系统里是“远程控制和消息入口层”，不是低延迟 teleop 引擎。

适合：

- 手机发命令让灯动
- 远程触发 recording
- 远程改灯光
- 远程健康检查

不适合：

- 真正实时摇杆控制
- 实时视频遥操作

## OpenClaw 对 LeLamp 暴露的命令

```bash
uv run -m lelamp.remote_control show-config
uv run -m lelamp.remote_control list-recordings
uv run -m lelamp.remote_control play curious
uv run -m lelamp.remote_control solid 255 160 32
uv run -m lelamp.remote_control clear
```

## Local Dashboard

本地状态面板现在和 runtime 一起内置在仓库里，适合：

- 树莓派本机屏幕全屏演示
- 同一局域网里的手机或电脑访问
- 手机热点或树莓派热点环境下的本地控制

启动：

```bash
uv run -m lelamp.dashboard.api
```

默认监听：

```bash
LELAMP_DASHBOARD_HOST=0.0.0.0
LELAMP_DASHBOARD_PORT=8765
LELAMP_DASHBOARD_POLL_MS=400
```

打开方式：

- 树莓派本机：`http://127.0.0.1:8765`
- 同网设备：看面板 Diagnostics 里显示的 `reachable_urls`

面板能力：

- 查看 system / motion / light / audio 的实时状态
- 触发 `startup`、`play`、`stop`、`shutdown_pose`
- 切到暖琥珀灯光或清灯
- 看当前可用 recordings、最近错误和可访问 URL

## 重启后自动收尾

总控脚本会安装一个 one-shot systemd 服务，在下一次重启后自动执行：

- `download-files`
- `aplay -l`
- `arecord -l`
- `/dev/ttyACM*`
- `openclaw status`

输出会写到：

- `POST_BOOT_REPORT.md`

## 仍然需要你人手参与的步骤

- 首次物理组装
- 舵机接线
- 舵机 ID 设置
- 首次校准摆位

也就是说：

- 软件与系统 bring-up 已经被尽量压成一条命令
- 机械和校准步骤仍然是互动式的

## 手动命令速查

### 舵机

```bash
uv run lerobot-find-port
uv run -m lelamp.setup_motors --id lelamp --port /dev/ttyACM0
uv run -m lelamp.calibrate --id lelamp --port /dev/ttyACM0
uv run -m lelamp.test.test_motors --id lelamp --port /dev/ttyACM0
```

### 音频与灯光

```bash
uv run -m lelamp.test.test_audio
sudo uv run -m lelamp.test.test_rgb
sudo uv run -m lelamp.remote_control solid 255 160 32
sudo uv run -m lelamp.remote_control clear
```

### 语音 Agent

```bash
uv run smooth_animation.py download-files
uv run smooth_animation.py console
```

说明：

- `console` 是当前这套 Pi + ReSpeaker 的默认路径，直接走树莓派本机麦克风和扬声器
- 如果你要开机常驻，本仓库的 `systemd` 示例同样跑 `uv run smooth_animation.py console`
- `start` / `connect` 仍然保留给 LiveKit 房间模式，但那是可选扩展，不是本地演示的默认路径

### OpenClaw

```bash
openclaw onboard --install-daemon
openclaw doctor
openclaw status
```
