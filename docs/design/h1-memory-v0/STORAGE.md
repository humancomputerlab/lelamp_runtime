# H1 Memory v0 — Storage & Atomicity

## 存储根目录

```
$HOME/.lelamp/memory/
├── default/                         # v0 仅此一个 user_id 目录
│   ├── profile.json                 # 身份 / 元数据（不含 PII）
│   ├── events.jsonl                 # 当前活跃事件流（append-only）
│   ├── sessions/
│   │   ├── sess_2026-04-17_23-11-15.meta.json
│   │   └── sess_2026-04-17_23-11-15.summary.json
│   ├── recent_index.json            # 近 N session 的聚合缓存
│   └── archive/
│       └── 2026-04.jsonl            # 老化归档（按月切）
└── .lock                            # 全局写入互斥文件锁
```

**为什么放 `$HOME/.lelamp/memory/` 而不是 repo 内**：

- repo 随时可以 `git clean -fdx`，memory 不能跟着被删
- 同一 Pi 换分支切换代码时，记忆要保留
- `$HOME` 在 Pi 上是 `/home/wujiajun`，systemd 的 `lelamp.service` 以 `wujiajun` 运行（见部署脚本），路径稳定
- **不**使用 `/var/lib/lelamp/`：需要 sudo 才能 chown，破坏"文件可以手工编辑"的初衷

环境变量覆盖：`LELAMP_MEMORY_ROOT` 可覆盖根路径（用于测试 / 隔离不同 dev 机器的数据）。

---

## user_id 策略（v0）

- **只接受** `user_id = "default"`
- writer 端在启动时调用 `resolve_user_id()`：当前实现 **永远** 返回 `"default"`
- 留一个扩展点：`resolve_user_id()` 的函数签名 / 返回值允许 v1 把它换成 speaker ID / OAuth sub / facelandmark hash，但 **v0 必须硬编码返回 "default"，不读环境变量，不读配置文件**
- 理由：v0 的目标是"把记忆跑起来"，不是"做认证"；一旦允许多 user_id，下游的 prompt 注入、归档、UI 展示全部要处理 disambiguation，scope 立刻膨胀

---

## `profile.json`

一次性写入、极少更新的元数据。不是事件流的一部分。

```json
{
  "schema": "lelamp.memory.v0.profile",
  "user_id": "default",
  "created_at_ms": 1776000000000,
  "display_name": "default",
  "nickname": null,
  "preferred_style_hints": [],
  "banned_styles": [],
  "notes": null
}
```

字段：

- `preferred_style_hints`：手工可编辑的 **白名单 hint**；v0 的 prompt 注入层会把它转成一行自然语言加进 memory header；但写入端 **不** 读这个字段，它对事件流无影响
- `banned_styles`：手工可编辑的 **黑名单**；prompt 注入层会在 memory header 里声明 "avoid X, Y"
- `nickname`：允许用户手工填一个昵称，memory header 里可以带；**不做** ASR 姓名抽取

更新策略：整个文件 `tmp+rename` 原子重写；不做 partial update。

---

## `events.jsonl`

**当前活跃**的事件流，append-only。

写入协议：

1. writer 端拿全局文件锁 `.lock`（`fcntl.flock(LOCK_EX)`）
2. `open(events.jsonl, "ab", buffering=0)` → `write(line)` → `fsync(fd)` → close
3. 释放锁

选择 append + fsync 而不是 per-event tmp+rename 的理由：

- 每秒最多 ~10 事件（对话 + tool + fallback + playback），每个 < 2KB → IOPS 完全扛得住
- 事件是**不可变**的历史记录，不是聚合态，append 天然安全
- rotate 通过**独立流程**处理，不和 write path 耦合（见 `LIFECYCLE.md`）

**单文件上限**：`events.jsonl` > **10 MiB** 或 session 跨 **3 天** 时触发 rotate；rotate 逻辑见 `LIFECYCLE.md`。

**崩溃语义**：

- writer 写到一半被 kill → 最后一行可能是截断的 JSON
- reader 必须实现 "skip malformed trailing line"，跳过最后一行坏数据，不传染整个文件
- fsck 工具（`scripts/memory_fsck.py`，v0 设计稿里先不实现，但列入 `OPEN_QUESTIONS`）能检测并在 append 时补 `\n` 修齐

---

## 聚合态的原子写入

`sessions/*.summary.json`、`recent_index.json`、`profile.json` 都是**聚合态**（整个文件都是一致体），必须原子：

```
1. write new content → tmp/abc.json.tmp (same filesystem as target)
2. fsync(tmp fd)
3. os.replace(tmp, target)   # rename is atomic on POSIX
4. fsync(parent dir fd)      # 让目录条目持久化
```

错误处理：

- `tmp+rename` 的任何一步失败 → 删 tmp，**不**动原文件；原文件永远是上一次成功写入的版本
- 写入器必须是**幂等**的：给定同一组输入事件，多次运行结果一致（便于 rebuild）

---

## 写入器的进程拓扑

```
┌─────────────────────────────────────────────────────────────┐
│ Pi 上可能同时存在的写入源                                    │
├─────────────────────────────────────────────────────────────┤
│ 1. lelamp.service (smooth_animation.py)                     │
│    - conversation / function_tool / fallback_expression     │
│    - function_tool (voice_agent 调工具时；不重复写 playback) │
│ 2. dashboard (uvicorn)                                      │
│    - playback (手动点按钮)                                  │
│ 3. python -m lelamp.remote_control                          │
│    - playback (CLI 触发)                                    │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         └────────────────────┴────────────────────┘
                              ▼
                    全局文件锁 .lock
                              ▼
                  $HOME/.lelamp/memory/default/events.jsonl
```

关键点：

- **每个进程独立写**，不经过 motor_bus。memory writer ≠ arbiter
- 用 `fcntl.flock(LOCK_EX)` 保证跨进程互斥；`flock` 在 Pi 上的 ext4 是可靠的
- 锁粒度 = 单次 append（通常 < 1ms）；**不允许**长时间持锁
- `remote_control` 本来是**短命进程**，起来写一条就 exit，也走同一把锁
- **Session 归属**：dashboard 和 remote_control 写入前必须调 `attach_or_create_session()`：优先依附到存活 agent session；agent 不在就新建 `sess_manual_<ts>`。详见 `LIFECYCLE.md` §"Agent session vs manual session"
- 去重契约：voice-agent 调工具产生的硬件动作由 `function_tool` 唯一承载，**不**再写一条 playback。见 `SCHEMA.md` §"去重契约"

---

## 与 MotorBusArbiter 的独立性（重申）

H1 writer 不碰：

- `motor_bus/server.py`（HTTP arbiter，只管硬件）
- `motor_bus/client.py`（proxy，只管硬件 fallback）
- `AnimationService` / `RGBService` 的构造流程

H1 reader（prompt 注入层）不碰：

- 任何 HTTP；只读本地文件
- 任何事件循环；同步读取，启动时一次性加载到内存

**硬件孤立保证**：即使 memory 目录整个被删掉，`lelamp.service` 启动时不应崩；writer / reader 都要有 "graceful degrade → 当无记忆版本使用" 的分支。

---

## 权限与所有权

- 所有文件 mode = `0600`（仅用户可读写）；目录 mode = `0700`
- systemd unit 已经以 `wujiajun` 运行，不需要额外 `chown`
- **不**用 setuid / setgid；**不**放 `/etc/` 或 `/var/`

---

## 磁盘占用预算

v0 的目标上限：

| 项 | 预算 | 说明 |
|---|---|---|
| 单条事件 | ≤ 2 KiB | 超出说明 payload 膨胀，触发 writer warning |
| 活跃 `events.jsonl` | ≤ 10 MiB | 触发 rotate，见 LIFECYCLE |
| 单 session summary | ≤ 4 KiB | LLM 总结产物，强裁剪 |
| 全部归档累计 | ≤ 200 MiB | 触达后给 user 一个清理提示，不自动删 |
| 总目录 | ≤ 256 MiB | 超出视为异常，要人工干预 |

这些预算都是**观察口径**，不是硬约束。实现阶段的 writer 可以打 `WARN` 但**不主动删数据**。
