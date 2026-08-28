# Requirements Traceability

This matrix maps the v1.0 product requirements to concrete implementation and verification surfaces.

| Requirement area | Implementation | Verification |
|---|---|---|
| Local session discovery | `collector.py`: read-only Codex state DB adapter and rollout path discovery | `test_state_database_discovers_thread_metadata_read_only` |
| Incremental, bounded ingestion | Per-file inode, offset, partial line, head hash; bounded initial head/tail | collector idempotency, partial-line and truncate/rewrite tests |
| Raw facts remain replayable | `events` table with unique stable `source_id`, normalized and raw JSON | duplicate scan test; SQLite unique constraint |
| Version-tolerant Codex parsing | Shape-based unwrapping and type aliases in `parser.py` | legacy session metadata, event envelope, command, plan tests |
| Session status truthfulness | Conservative transitions in `engine.py` | turn complete and process exit remain `IDLE` |
| Progress must not be fabricated | `progress_known=0` until structured plan; weighted completed steps only | no-plan Unknown test |
| Replanning | append-only plan versions; new scope may lower progress | 100% to 25% replan test with both versions retained |
| Need Attention | keyed alerts with severity, count, evidence, acknowledgement, resolution | input Critical and repeated-test escalation tests |
| Input and approval visibility | immediate `WAITING_INPUT` / `WAITING_APPROVAL` plus Critical alert | engine input test and Demo approval session |
| Command/tool evidence | command/tool projection with output, exit, duration and failure alerts | parser/engine tests; Task Detail tabs |
| Test evidence | recognized test commands and pass/fail count extraction | third failure escalates to Critical |
| Hung command detection | `last_output_at` threshold rather than total duration | continuing-output test prevents false alert |
| File and Diff visibility | event-level file changes plus read-only Git status/diff | Task Detail Files & Diff; path containment check |
| External-session safety | pause/cancel require an in-memory dashboard-owned process | external capability test |
| Managed task lifecycle | `codex exec --json`, durable run log, process group, queue/resume | fake Codex process integration tests |
| Explicit completion | Codex completion event or human Mark complete only | turn/process exit tests; completion action audit |
| Overview | status cards, recent work, alert queue, activity signal | browser application and `/api/overview` test |
| Need Attention page | severity-first action queue with evidence and task link | Demo and overview API test |
| High-density Table | searchable task inventory with status/progress/alerts/files/tests | browser application |
| Board | Backlog, In progress, Waiting, Blocked, Done grouping | browser application |
| Completed page | only `status=COMPLETED` | browser application and state semantics tests |
| Task Detail | timeline, plans, commands, tools, files, tests, audit, controls | `/api/tasks/{id}` HTTP test and browser application |
| Real-time updates | invalidation-only SSE with keep-alive and reconnect | SSE starts with refresh event test |
| Settings | runtime thresholds persisted to SQLite and validated | HTTP settings success and invalid-value tests |
| Demo mode | running, approval, repeated-test-failure and complete sessions | `/api/demo` test |
| Loopback-first security | default `127.0.0.1`; token required outside loopback | authentication integration test |
| Browser hardening | CSP, frame denial, no-sniff, no CORS, escaped UI values | HTTP security-header test; no inline script |
| Graceful lifecycle | collector and dashboard-owned processes stop in order | service/process manager teardown tests |
| Supported Python versions | no third-party runtime dependencies; 3.11+ | CI matrix 3.11, 3.12, 3.13 |

## Acceptance commands

```bash
python -m compileall -q codex_dashboard tests
python -m unittest discover -s tests -v
node --check codex_dashboard/static/app.js
python -m codex_dashboard --help
```

A repository revision is considered verified only when all four gates succeed.
