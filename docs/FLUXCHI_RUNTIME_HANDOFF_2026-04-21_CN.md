# FluxChi × LeLamp 运行时交接

日期：2026-04-21

## 当前真实跑通的链路

### Mac

- FluxChi backend 以 demo 方式运行：
  - `python web/app.py --demo --speed 10 --host 0.0.0.0 --web-port 8000`
- Mac 到 Pi 已验证可用的桥接方式不是直连 LAN，而是 **SSH 反向隧道**
  - Pi 本地入口：`127.0.0.1:18000`
  - 实际桥接：`ssh -fNT -R 18000:127.0.0.1:8000 wujiajun@10.161.139.125`
- Mac 侧自启动现已接到 `launchd`
  - label：`com.wujiajun.fluxchi-pi-bridge`
  - plist：`~/Library/LaunchAgents/com.wujiajun.fluxchi-pi-bridge.plist`
  - log：`/tmp/fluxchi-pi-bridge.log`
  - 当前 `launchctl print` 状态：
    - `state = spawn scheduled`
    - `last exit code = 255`
  - 含义：
    - **自启动机制已装好并在重试**
    - 但当前 Pi SSH 入口建连后立刻断开，所以 tunnel 暂时还没重新挂上

### Pi

- 实际在跑的 runtime 不是 `~/lelamp-dev/lelamp_runtime`
- 实际可运行路径是：
  - `/home/wujiajun/lelamp-dev/lelamp_runtime_canary_0fb2450`
- `lelamp.service` 当前已经指向这份 canary worktree：
  - `smooth_animation.py console`
- dashboard 入口已验证可启动：
  - `.venv/bin/python -m lelamp.dashboard.api`
- FluxChi listener 已验证可启动：
  - `.venv/bin/python -m lelamp.integrations.fluxchi_listener --ws ws://127.0.0.1:18000/ws/harness`

## 这次实际修掉的问题

### 1. Pi 上 runtime 路径错位

- `~/lelamp-dev/lelamp_runtime` 这份代码与当前 agent / dashboard 运行环境不一致
- dashboard import 在这份代码上会炸：
  - `ImportError: cannot import name 'project_action_compile_result' ...`
- 所以实际部署、联调、启动都必须基于：
  - `~/lelamp-dev/lelamp_runtime_canary_0fb2450`

### 2. Pi 直连 Mac 的 `:8000` 不可靠

- Pi 对 `http://10.161.220.17:8000/api/v1/pulse` 出现过：
  - TCP connect 成功
  - HTTP 5 秒内无响应
- 当前稳定 workaround 是 **Mac 反向 SSH 隧道到 Pi**
- listener 因此固定改连：
  - `ws://127.0.0.1:18000/ws/harness`

### 3. listener 先打灯再播动作会撞上 dashboard busy 锁

- 原始行为：
  - `POST /api/lights/solid` -> `202`
  - 紧接 `POST /api/actions/play` -> `409 Conflict`
- 根因：
  - dashboard executor 是单 busy lock
  - 灯光 action 还没释放，动作 action 已经到了
- 已修复：
  - `lelamp/integrations/fluxchi_listener.py`
  - 在灯光与动作之间显式轮询 `/api/actions` 等待 `busy=false`
  - 同时对 `play` 加了 409 retry

### 4. listener 后台起不来不是代码炸，是 shell 杀了自己

- 远程起后台时用了：
  - `pkill -f "lelamp.integrations.fluxchi_listener"`
- 这会把当前 ssh 远程 shell 自己打死，表现像“后台没起”
- 现在正确做法是：
  - 直接 `nohup ... &`
  - 或交给 systemd

### 5. Mac LaunchAgent 不能直接执行 `Downloads` 里的脚本

- 之前 plist 指向：
  - `/Users/wujiajun/Downloads/Lelamp/host_tools/start_fluxchi_pi_reverse_tunnel.sh`
- `launchd` 实际报错：
  - `Operation not permitted`
- 根因：
  - macOS 对 `Downloads` 目录下脚本执行有额外限制，`launchd` 没法稳定直接起它
- 已修复：
  - plist 改为直接启动 `/usr/bin/ssh`
  - 不再经过 `Downloads` 里的 shell 脚本

### 6. 当前新的真实阻塞点：Pi 的 SSH 端口在 KEX 前主动断开

- 2026-04-21 这次收口时，Mac 对 Pi 做了直接探测：
  - `ssh -v -p 22 wujiajun@10.161.139.125 true`
  - `ssh -v -p 2222 wujiajun@10.161.139.125 true`
- 两个端口表现一致：
  - TCP connect 成功
  - 随后立即：
    - `kex_exchange_identification: Connection closed by remote host`
- 说明：
  - 现在不是 Mac 自启动配置有误
  - 而是 **Pi 端 SSH 入口当前在主动断连**
  - 只要 Pi SSH 恢复正常，已经装好的 LaunchAgent 会自动把 tunnel 拉起

### 7. Scene B（呼吸共振）已在本地工作树落地

- 新增：
  - `lelamp/breath_orchestrator.py`
  - `lelamp/breath_run.py`
  - `lelamp/integrations/profiles/scene_b.yaml`
  - `lelamp/test/test_breath_orchestrator.py`
- `lelamp/integrations/fluxchi_listener.py` 已扩展：
  - `action.type: motion | breath`
  - `breath` 通过 motor bus `ProxyRGBService` 连续推 `/rgb/solid`
  - `motion` / `breath` 互相 preempt
- 当前验证：
  - `lelamp.test.test_breath_orchestrator` + `lelamp.test.test_fluxchi_listener` 全通过
  - `python -m lelamp.breath_run --dry-run --duration 0.6 --tick-hz 5` 可正常跑
  - 额外修了一个幂等尾巴：
    - natural completion 后再次 `stop()` 不会重复 clear

## 已新增 / 修改

本地仓库：

- `lelamp/integrations/__init__.py`
- `lelamp/integrations/fluxchi_listener.py`
- `lelamp/breath_orchestrator.py`
- `lelamp/breath_run.py`
- `lelamp/integrations/profiles/scene_a.yaml`
- `lelamp/integrations/profiles/scene_b.yaml`
- `lelamp/test/test_breath_orchestrator.py`
- `lelamp/test/test_fluxchi_listener.py`
- `scripts/install_fluxchi_sidecars.sh`
- `host_tools/start_fluxchi_pi_reverse_tunnel.sh`
- `host_tools/com.wujiajun.fluxchi-pi-bridge.plist`

Pi canary worktree 已同步：

- `lelamp/integrations/__init__.py`
- `lelamp/integrations/fluxchi_listener.py`
- `lelamp/integrations/profiles/scene_a.yaml`

## 验证证据

### dry-run

Pi 上已验证：

- listener 成功连接 `/ws/harness`
- 首帧触发：
  - `level=moderate`
  - `recording=headshake`
  - `rgb=(255,170,50)`

### 真触发

Pi 上 listener 真跑后已验证：

- `POST /api/lights/solid` -> `202 Accepted`
- `POST /api/actions/play` -> `202 Accepted`

随后 dashboard 状态确认：

- `motion.last_completed_recording = "headshake"`
- `motion.last_result = "Finished playing recording"`
- `motion.current_recording = "home_safe"`
- `light.color = {255,170,50}`

### 自启动收口验证

Mac 本机已验证：

- `curl http://127.0.0.1:8000/api/v1/pulse`
  - 返回 `{"ok":true,...}`，说明 FluxChi backend 活着
- `plutil -lint host_tools/com.wujiajun.fluxchi-pi-bridge.plist`
  - `OK`
- `launchctl print gui/$(id -u)/com.wujiajun.fluxchi-pi-bridge`
  - `path = /Users/wujiajun/Library/LaunchAgents/com.wujiajun.fluxchi-pi-bridge.plist`
  - `program = /usr/bin/ssh`
  - `state = spawn scheduled`
  - `last exit code = 255`
- `/tmp/fluxchi-pi-bridge.log`
  - 最近错误是：
    - `Connection closed by 10.161.139.125 port 22`
  - 结论：
    - launchd 已在工作
    - 当前阻塞在 Pi 端 SSH，不在 Mac 自启动逻辑

### Scene B 本地自部署验证（Pi 自己拉包）

由于 Mac→Pi 的 SSH 在 pre-auth 阶段仍被远端链路掐断，本轮改走了 Pi 本地自部署：

- Pi 已成功从 Mac 临时 HTTP 拉到：
  - `lelamp-sceneb.tgz`
  - `sb-deploy.sh`
- Pi 本地执行 `sb-deploy.sh` 时，已确认通过的步骤：
  - `tar -xzf /tmp/sb.tgz -C /home/wujiajun/lelamp-dev/lelamp_runtime_canary_0fb2450`
  - `.venv/bin/python -m py_compile lelamp/breath_orchestrator.py`
  - `.venv/bin/python -m py_compile lelamp/breath_run.py`
  - `.venv/bin/python -m py_compile lelamp/integrations/fluxchi_listener.py`
  - `systemctl is-active lelamp-dashboard.service` -> `active`
  - `systemctl is-active lelamp-fluxchi-listener.service` -> `active`
  - `curl http://127.0.0.1:8770/health` -> `{"ok":true,"motor_ok":true,"rgb_ok":true,...}`
  - `python -m lelamp.breath_run --duration 6 --log-level INFO`
    - 已看到：
      - `breath start name=shared_breath_cli duration=6.0s bpm 12.0→4.0 kelvin 3000→2400`
- 终端输出在 `breath start` 后被用户侧截断，**没有抓到最终 `SCENE_B_DEPLOY_OK` 文本**
- 但如果用户看到随后一瞬间 `shutdown` 并且 Pi 已下线，则按 `sb-deploy.sh` 的串行逻辑可以合理推断：
  - breath smoke 没报错
  - 脚本走到了 `sudo shutdown -h now`
  - Scene B 已部署到 canary runtime 且至少跑通过一次真实 breath

## 当前推荐的常驻方式

### Pi

安装两个 sidecar service：

- `lelamp-dashboard.service`
- `lelamp-fluxchi-listener.service`

listener 默认配置：

- `FLUXCHI_WS=ws://127.0.0.1:18000/ws/harness`
- `LELAMP_DASHBOARD=http://127.0.0.1:8765`
- `FLUXCHI_DISABLE_VOICE_GATE=1`
- 默认 profile 仍可能指向 `scene_a.yaml`
- 如果要切到 Scene B，改：
  - `FLUXCHI_PROFILE_PATH=.../lelamp/integrations/profiles/scene_b.yaml`

### Mac

手动起 tunnel：

- `host_tools/start_fluxchi_pi_reverse_tunnel.sh`

登录后自动维持 tunnel：

- `~/Library/LaunchAgents/com.wujiajun.fluxchi-pi-bridge.plist`
- label：`com.wujiajun.fluxchi-pi-bridge`
- 当前已实际 bootstrap 到用户 launchd
- 会按 `ThrottleInterval=10` 自动重试

## 还没收口到“完全产品化”的点

1. Mac 侧 feed 仍依赖 FluxChi backend 进程本身在跑
2. 反向隧道当前默认写死 LAN IP `10.161.139.125`
3. 当前 Pi SSH `22 / 2222` 都会在 KEX 前主动断开，需先恢复 Pi 侧 SSH 可用性
4. 如果 Pi 换 IP，需要更新：
   - `host_tools/start_fluxchi_pi_reverse_tunnel.sh`
   - `~/Library/LaunchAgents/com.wujiajun.fluxchi-pi-bridge.plist`
   - 或换成稳定可用的 Tailscale / mDNS host
5. 目前 sidecar service 以 canary worktree 为准，不是 `~/lelamp-dev/lelamp_runtime`
