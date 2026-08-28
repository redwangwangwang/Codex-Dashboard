# Codex Control Center

一个面向本地 Codex 会话的 **可观测、可解释、可控制** Dashboard。它读取 Codex 已经落盘的状态库与 rollout JSONL，将原始事实保存到独立 SQLite 事件库，再投影成适合操作的任务状态、计划进度、告警和证据视图。

> 核心原则：**不伪造进度，不把进程退出当完成，不对不拥有的进程提供破坏性控制。**

## 功能

- **Overview**：运行中、等待、阻塞、显式完成和 Need Attention 的总览。
- **Need Attention**：输入请求、审批请求、失败、停滞、长时间无输出、连续测试失败等可解释告警。
- **All Tasks / Board / Completed**：高密度表格、流程看板和只包含显式完成证据的结果列表。
- **Task Detail**：时间线、版本化计划、命令、工具调用、文件变更、测试结果、Git Diff 和控制审计。
- **实时刷新**：SSE 推送变更通知；断线自动重连。
- **Codex 采集**：只读发现 `state_5.sqlite` / `state.sqlite` / `state.db`，增量尾读 `rollout-*.jsonl`。
- **大文件保护**：首次仅读取文件头和最近尾部，后续按 inode、偏移、半行缓冲与头部哈希增量读取。
- **受管任务**：通过 `codex exec --json` 启动；可发送后续指令，并仅对 Dashboard 自己启动的进程提供暂停、继续和取消。
- **Git 证据**：使用只读 Git 命令展示工作区状态与 Diff，不修改仓库。
- **本地优先**：运行时零第三方 Python 依赖；数据保存在用户指定目录。
- **安全绑定**：默认只监听 `127.0.0.1`；绑定到非回环地址时强制要求令牌。

## 快速开始

要求 Python 3.11 或更高版本。Codex CLI 不是浏览历史的硬依赖，但启动/恢复受管任务时需要。

```bash
git clone https://github.com/redwangwangwang/Codex-Dashboard.git
cd Codex-Dashboard
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .
codex-dashboard serve
```

浏览器默认打开：

```text
http://127.0.0.1:8765/
```

也可以直接运行模块：

```bash
python -m codex_dashboard serve --no-browser
```

### 常用命令

```bash
# 单次扫描并输出摘要
codex-dashboard scan

# 检查 Codex、状态库、rollout 和本地数据库
codex-dashboard doctor

# 注入一组可演示所有视图的本地数据
codex-dashboard demo

# 清空业务数据后重新注入 Demo
codex-dashboard demo --reset
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `CODEX_HOME` | `~/.codex` | Codex 本地数据目录 |
| `CODEX_DASHBOARD_DATA` | `~/.codex-dashboard` | Dashboard SQLite 与受管运行日志 |
| `CODEX_DASHBOARD_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `CODEX_DASHBOARD_PORT` | `8765` | HTTP 端口 |
| `CODEX_DASHBOARD_TOKEN` | 空 | API Bearer Token；非回环绑定必填 |
| `CODEX_DASHBOARD_POLL` | `2` | 采集轮询秒数 |
| `CODEX_DASHBOARD_STALE_SECONDS` | `900` | 活跃会话无新证据多久后提示停滞 |
| `CODEX_DASHBOARD_COMMAND_HUNG_SECONDS` | `600` | 运行命令无新输出多久后告警 |
| `CODEX_DASHBOARD_GIT_REFRESH_SECONDS` | `15` | Git 证据刷新周期 |
| `CODEX_BIN` | `codex` | Codex CLI 可执行文件 |

非本机绑定示例：

```bash
export CODEX_DASHBOARD_HOST=0.0.0.0
export CODEX_DASHBOARD_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
codex-dashboard serve --no-browser
```

API 支持：

```http
Authorization: Bearer <token>
```

SSE 也可使用 `?token=<token>`；前端会把首次 URL 中的 token 保存到当前浏览器的 localStorage，然后从地址栏移除。

## 数据与状态语义

### 原始事实不可变

每条 Codex 事件按来源、字节位置和内容生成稳定 `source_id`，写入 `events` 后不再改写。会话、命令、工具、文件、测试、计划和告警是可重算投影。

### 完成必须明确

下列情况 **不会** 自动变成 `COMPLETED`：

- 一个 turn 正常结束；
- `codex exec` 以退出码 0 结束；
- 长时间没有新事件；
- 测试通过但没有任务完成证据。

它们通常进入 `IDLE`。只有 Codex 发出明确完成事件，或用户在 Dashboard 中执行“Mark complete”，任务才进入 `COMPLETED`。

### 进度必须来自计划

- 没有结构化计划：`Unknown`；
- 有计划：只按步骤权重与明确完成状态计算；
- Replan：创建新版本，旧版本保留；
- 新计划扩大范围时，进度允许下降。

### 控制能力按所有权协商

- 外部终端启动的 Codex：可查看、可在 CLI 支持时继续对话，但 Dashboard 不会发送暂停/终止信号；
- Dashboard 启动的 Codex：记录 PID 与审计，可暂停、继续、取消；
- 服务重启后不会凭数据库中残留 PID 重新取得“所有权”。

## API 摘要

```text
GET    /api/health
GET    /api/doctor
GET    /api/overview
GET    /api/tasks
POST   /api/tasks
GET    /api/tasks/{id}
PATCH  /api/tasks/{id}
GET    /api/tasks/{id}/diff
POST   /api/tasks/{id}/actions/{pause|continue|cancel|instruct|complete|acknowledge|scan}
GET    /api/settings
PUT    /api/settings
POST   /api/demo
GET    /api/events              # Server-Sent Events
```

详见 [`docs/API.md`](docs/API.md)。

## 开发与验证

运行时不依赖第三方包，测试使用标准库 `unittest`：

```bash
python -m compileall -q codex_dashboard tests
python -m unittest discover -s tests -v
node --check codex_dashboard/static/app.js
python -m codex_dashboard --help
```

CI 在 Python 3.11、3.12、3.13 上执行同一组门控。覆盖重点包括：

- 多代 rollout 包装格式；
- 增量偏移、半行续读、截断重写与幂等；
- 无计划进度未知、Replan 可回退；
- 输入/审批 Critical；
- turn / 进程退出不等于完成；
- 第三次连续测试失败升级；
- 持续输出的长命令不误判卡住；
- HTTP CRUD、设置校验、SSE、Demo 与非本机令牌认证；
- 受管进程暂停/继续/取消边界。

## 架构

```text
Codex state DB (read-only) ─┐
rollout JSONL (incremental) ├─ Collector ─ Immutable events ─ Projection engine
managed codex exec --json ──┘                         │
Git status/diff (read-only) ──────────────────────────┤
                                                     ▼
                                               SQLite / WAL
                                                     │
                           REST + SSE ────────────────┤
                                                     ▼
                              Overview / Attention / Table / Board / Detail
```

更多设计说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 隐私与限制

- Dashboard 会读取配置的 Codex 目录和会话中记录的工作目录；不要把服务暴露到不可信网络。
- 原始事件可能包含命令输出、路径和模型消息。SQLite 与受管 JSONL 日志默认位于本机数据目录。
- Codex 的本地格式会演进；解析器采用形状兼容并保留原始事件，但未知新事件可能先以通用类型显示。
- 暂停/继续依赖 POSIX 进程信号；Windows 上不会显示这两个能力。
- 发送后续指令优先使用当前 Codex CLI 的 `queue` / `exec resume` 能力；旧版本 CLI 可能只支持恢复启动。

## License

MIT
