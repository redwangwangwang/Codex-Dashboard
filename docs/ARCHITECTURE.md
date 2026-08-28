# Architecture

## 1. Goals

Codex Control Center is a local-first operational console. Its job is not to re-create Codex's reasoning; it turns durable evidence into a consistent view of:

- what sessions exist;
- what has actually happened;
- whether the agent is running, idle, waiting, blocked, failed, cancelled, or explicitly complete;
- which human action is required and why;
- which control operations are genuinely available.

The system prioritizes truthfulness, replayability, bounded resource use, and safe control boundaries.

## 2. Component map

```text
┌──────────────────────────────── Local machine ────────────────────────────────┐
│                                                                                │
│  ~/.codex/state_5.sqlite  ─┐                                                   │
│  ~/.codex/sessions/*.jsonl ─┼─ Collector ──► immutable events                  │
│  archived rollouts          ─┤       │                │                        │
│  managed exec JSONL         ─┘       │                ▼                        │
│                                      │        deterministic projections        │
│  git status / git diff ──────────────┤                │                        │
│                                      ▼                ▼                        │
│  dashboard-owned codex exec ◄─ Process Manager ── SQLite/WAL                  │
│                                                   │                            │
│                                                   ├─ REST query/control         │
│                                                   └─ SSE invalidation           │
│                                                            │                   │
│                                                            ▼                   │
│                                         Vanilla HTML/CSS/JavaScript dashboard │
└────────────────────────────────────────────────────────────────────────────────┘
```

## 3. Source adapters

### 3.1 Codex state database

The collector opens known state database candidates with SQLite `mode=ro`. It discovers a compatible table (`threads`, `sessions`, or `conversations`) and selects only columns that are present. Rows become synthetic `session.meta` events with deterministic source IDs.

The Dashboard never migrates, vacuums, writes, or locks Codex's database.

### 3.2 Rollout JSONL

The JSONL collector tracks per-path:

- device/inode identity;
- consumed byte offset;
- last observed size and mtime;
- unfinished final-line bytes;
- a hash of the first 4 KiB.

The head hash catches truncate-and-rewrite cases where a new file grows beyond the prior offset before the next scan. Source IDs include path, byte position, and raw content, so repeated scans are idempotent.

For a large, previously unseen file, startup reads a bounded head and bounded recent tail. It does not materialize the full history. Future appends are consumed from the stored offset.

### 3.3 Managed `codex exec --json`

A task launched by the Dashboard is started in its requested working directory. Stdout is appended to a durable JSONL file and parsed through the same normalizer as ordinary rollouts. Stderr or non-JSON diagnostics are retained as `process.output` evidence.

When Codex reports its real thread UUID, the local launch placeholder is linked to that thread. Process signals are only sent through an in-memory ownership object created by the current Dashboard process.

### 3.4 Git

Git inspection uses commands that do not write repository state:

- `git rev-parse --show-toplevel`;
- `git branch --show-current`;
- `git rev-parse HEAD`;
- `git status --porcelain=v1 -z`;
- `git diff --no-ext-diff --no-color`.

`GIT_OPTIONAL_LOCKS=0` is set. User-selected Diff paths are resolved beneath the repository root before invoking Git.

## 4. Event model

`events` is the source of truth. Each record includes:

```text
session_id, source_id, timestamp, kind, actor, text, payload_json, raw_json
```

`source_id` has a unique constraint. Raw input is retained so future parser versions can rebuild projections.

Projection tables include:

- `sessions`;
- `commands`;
- `tool_calls`;
- `file_changes`;
- `test_runs`;
- `plans`;
- `alerts`;
- `audit_log`.

The current implementation updates projections during ingestion, but the rules are deterministic and do not require hidden in-memory history.

## 5. State semantics

### 5.1 Statuses

| Status | Evidence meaning |
|---|---|
| `DISCOVERED` | Metadata exists, but no active turn evidence has been seen. |
| `RUNNING` | A turn, command, tool, response, or supplied input indicates active work. |
| `IDLE` | A turn or managed CLI invocation ended normally without explicit task completion. |
| `WAITING_INPUT` | Codex emitted a user-input request. |
| `WAITING_APPROVAL` | Codex emitted a protected-action approval request. |
| `PAUSED` | The current Dashboard sent SIGSTOP to a process it owns. |
| `BLOCKED` | Active work has no recent evidence beyond the configured threshold. |
| `FAILED` | A turn or managed process failed terminally. |
| `CANCELLED` | A dashboard-owned process was cancelled, or a temporary launch placeholder was replaced. |
| `COMPLETED` | Explicit Codex completion evidence or an explicit human confirmation exists. |

A command failure creates an alert and failed phase evidence; it does not necessarily make the entire task terminal, because Codex may recover in the same turn.

### 5.2 Progress

Progress is `NULL`/Unknown until a structured plan is observed. A plan version stores all canonical steps and their weights. Only steps explicitly marked completed contribute to the numerator:

```text
progress = completed_weight / total_weight × 100
```

An in-progress step contributes zero rather than an arbitrary fraction. Every replan creates a new version; prior versions remain queryable. Scope expansion may lower the displayed percentage.

### 5.3 Completion

The engine intentionally does not infer completion from:

- process exit code 0;
- turn completion;
- passing tests;
- inactivity;
- all currently observed commands succeeding.

These are supporting facts, not proof that the user's requested outcome is delivered.

## 6. Alert model

Alerts are keyed by session and stable cause. An alert tracks severity, count, first/last seen, evidence, acknowledgement, and resolution.

Immediate Critical cases:

- input required;
- approval required;
- third consecutive recognized test failure.

High/Warning cases include:

- turn failure;
- runtime error;
- command/tool failure;
- managed process abnormal exit;
- command with no new output beyond threshold;
- active session with no new evidence beyond threshold.

A long command that continues to emit output updates `last_output_at`, so it does not become a false hung-command alert.

## 7. Control boundaries

Capabilities are calculated per task at query time.

- `pause`, `continue`, and `cancel` require a live `ManagedProcess` object in the current server process.
- A PID persisted in SQLite is not sufficient to re-establish ownership after restart.
- `instruct` and `resume` additionally require a usable Codex CLI and thread identifier.
- explicit completion is available for non-terminal tasks and is recorded in both events and audit log.

On POSIX systems, managed commands start in a new process group so cancellation reaches child processes. Windows exposes cancellation but not POSIX pause/continue.

## 8. HTTP and browser security

- Default bind address is loopback.
- A non-loopback host fails configuration validation unless a token is present.
- API routes use Bearer token or `X-API-Token`; SSE also supports a query token.
- No permissive cross-origin CORS headers are returned.
- Static responses set CSP, frame denial, no-sniff, referrer, permissions, and no-store headers.
- JSON body size is bounded.
- Diff paths and static file paths are resolved beneath allowed roots.
- UI strings are HTML-escaped before DOM insertion.

The token protects the API transport; it is not a multi-user authorization system. The service should still be placed behind an authenticated tunnel or reverse proxy before exposure beyond a trusted network.

## 9. Concurrency and shutdown

SQLite uses WAL and short-lived connections. Writes are serialized inside the process by a re-entrant lock and `BEGIN IMMEDIATE`; HTTP readers use separate connections.

Shutdown order is:

1. stop collector loop;
2. terminate only current dashboard-owned Codex processes;
3. close HTTP server;
4. allow short-lived SQLite connections to close normally.

## 10. Evolution

The parser recognizes shape and aliases rather than one fixed Codex version. Unknown events are still stored with canonicalized types and complete raw payloads. A future app-server adapter can feed the same `ParsedEvent` contract without changing the projections or browser API.
