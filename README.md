# Codex Control Center

A local-first, evidence-first web dashboard for observing and managing Codex CLI
sessions across this computer and multiple SSH hosts.

## v0.2.0 highlights

- Runtime language selection: Simplified Chinese, English, Japanese, and Korean.
- SSH host profiles using an identity file or ssh-agent; remote passwords are not
  accepted or stored.
- Strict host-key checking by default, optional jump hosts, incremental remote
  rollout ingestion, connection health, reconnect backoff, and managed remote
  app-server lifecycle.
- Host-scoped session identity so equal Codex thread IDs on different servers do
  not collide.
- Local and remote repository, branch, HEAD, dirty/ahead/behind, conflict, diff,
  process, disk, context, token, and rate-limit evidence.
- Need Attention inbox, browser notifications, concurrent-worktree warnings,
  tags, search, filters, and bounded pagination.
- Conservative status: when Codex does not expose a denominator or capability,
  the UI shows Unknown/Unavailable instead of inventing progress or success.

## Install and run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
codex-control-center --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

The default database is `~/.codex-control-center/control-center.sqlite3`.
Override paths with:

```bash
export CCC_HOME=/path/to/control-center-data
export CODEX_HOME=/path/to/.codex
```

## SSH model

Settings → Remote hosts supports host, user, port, identity file, jump host,
Codex home, and allowed workspace roots.  The collector executes bounded,
read-only probes and incrementally reads rollout JSONL files.  It never stores a
remote password.  Unknown or changed host keys fail closed unless the operator
explicitly enables first-use enrollment for that host.

Observation does not imply control.  Start/stop of a dashboard-owned remote
app-server is available separately; session actions remain disabled unless the
adapter has advertised that capability.

## API

- `GET/PUT /api/settings`
- `GET/POST /api/hosts`
- `PUT/DELETE /api/hosts/{host_id}`
- `POST /api/hosts/{host_id}/test`
- `POST /api/hosts/{host_id}/sync`
- `POST /api/hosts/{host_id}/app-server/start`
- `POST /api/hosts/{host_id}/app-server/stop`
- `GET /api/sessions`
- `GET /api/sessions/{session_key}`
- `GET /api/sessions/{session_key}/events`
- `GET /api/attention`
- `POST /api/attention/{alert_id}/resolve`
- `GET /api/stream` (SSE)

See `docs/COMMUNITY_RESEARCH_2026-08.md` and
`docs/SSH_REMOTE_SECURITY.md` for product and trust-boundary details.
