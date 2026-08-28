# HTTP API

The API is intentionally small and local-first. All JSON responses use UTF-8. Error responses use:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "human-readable explanation"
  }
}
```

When a dashboard token is configured, send:

```http
Authorization: Bearer <token>
```

`X-API-Token` is also accepted. Server-Sent Events may use `?token=<token>` because the browser EventSource API cannot set custom headers.

## Health and discovery

### `GET /api/health`

Returns process-level health and storage locations.

```json
{
  "ok": true,
  "version": "1.0.0",
  "revision": 42,
  "codex_available": true,
  "codex_home": "/home/user/.codex",
  "database": "/home/user/.codex-dashboard/dashboard.sqlite"
}
```

### `GET /api/doctor`

Checks Codex CLI, Codex home, known state DBs, rollout count, and Dashboard database.

### `GET /api/overview`

Returns status counts, top open alerts, recent sessions, collector configuration, and the current revision.

## Tasks

`/api/sessions` is an alias for the task collection and item routes.

### `GET /api/tasks`

Query parameters:

| Name | Example | Meaning |
|---|---|---|
| `status` | `RUNNING,WAITING_INPUT` | Comma-separated status filter |
| `attention` | `true` | Require at least one open alert |
| `completed` | `true` or `false` | Filter explicit completed state |
| `q` | `oauth` | Search title, cwd, ID, or summary |
| `limit` | `500` | Maximum result count, capped at 2000 |
| `offset` | `0` | Result offset |

Response:

```json
{
  "items": [
    {
      "id": "thread UUID",
      "title": "Implement OAuth callback",
      "status": "RUNNING",
      "progress": 50.0,
      "progress_known": true,
      "open_alerts": 0,
      "changed_files": 3,
      "capabilities": {
        "instruct": true,
        "pause": false,
        "cancel": false
      }
    }
  ]
}
```

### `POST /api/tasks`

Create a Dashboard task.

```json
{
  "prompt": "Implement the endpoint and tests",
  "title": "Export endpoint",
  "cwd": "/path/to/repository",
  "model": "",
  "start": true
}
```

- `start: true` launches `codex exec --json` and requires Codex CLI.
- `start: false` creates a durable idle/draft task without launching a process.

### `GET /api/tasks/{id}`

Returns the full task projection:

- session metadata and capabilities;
- recent normalized events;
- commands;
- tools;
- file changes;
- recognized test runs;
- every plan version;
- alerts;
- audit log.

### `PATCH /api/tasks/{id}`

Editable fields:

```json
{
  "title": "New display title",
  "summary": "Operator note",
  "archived": false
}
```

### `GET /api/tasks/{id}/diff`

Optional query parameters:

- `path`: repository-relative file path;
- `staged=true`: show staged diff.

The path is resolved beneath the repository root. The response is size-bounded.

## Actions

Actions use:

```text
POST /api/tasks/{id}/actions/{action}
```

### `pause`

Sends SIGSTOP to a live dashboard-owned POSIX process. Empty body.

### `continue`

Sends SIGCONT to a dashboard-owned paused process. Empty body.

### `cancel`

Terminates the dashboard-owned process group, waits, then force-kills if required. Empty body.

### `instruct`

```json
{ "message": "Use the existing repository abstraction." }
```

For a live session the manager uses Codex queue support. For an idle resumable session it launches `codex exec --json resume`.

### `complete`

```json
{ "summary": "Endpoint, tests, and documentation delivered." }
```

Creates explicit completion evidence and an audit record. This is the only human path to `COMPLETED`.

### `acknowledge`

```json
{ "alert_id": 123 }
```

Acknowledges a displayed alert without deleting its evidence.

### `scan`

Runs one collector pass. The action is accepted on any existing task because collection itself is global.

## Settings

### `GET /api/settings`

Returns resolved paths, bind policy, executable, and runtime thresholds. Token values are never returned.

### `PUT /api/settings`

Runtime-adjustable fields:

```json
{
  "poll_interval": 2.0,
  "stale_seconds": 900,
  "command_hung_seconds": 600,
  "git_refresh_seconds": 15
}
```

Updates affect future collection/reconciliation. Raw events are unchanged.

## Demo

### `POST /api/demo`

```json
{ "reset": false }
```

Creates representative running, approval-waiting, repeated-test-failure, and explicitly completed sessions. `reset: true` clears Dashboard business data first; it never touches Codex files.

## Real-time updates

### `GET /api/events`

Server-Sent Events stream. Messages invalidate cached UI queries:

```text
event: refresh
id: 17
data: {"revision":42,"reason":"changed"}
```

The stream emits comments as keep-alives and is periodically re-established by the browser. Consumers should fetch current REST projections after each refresh; the SSE message is not the source of task data.

## Status codes

| Code | Meaning |
|---:|---|
| 200 | Successful read/update/action |
| 201 | Task or demo created |
| 400 | Invalid payload, unavailable capability, or failed managed operation |
| 401 | Token missing or invalid |
| 404 | Task or route not found |
| 405 | Write attempted outside API |
| 500 | Unexpected server error; details are written to the local audit log |
