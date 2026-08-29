# Codex Dashboard v0.2.0 — implementation and research matrix

Release date: 2026-08-29

## Requested capabilities

### Runtime-selectable languages

Settings exposes four locales without a server restart:

- English (`en-US`)
- Simplified Chinese (`zh-CN`)
- Japanese (`ja-JP`)
- Korean (`ko-KR`)

The selected locale is persisted in the local settings store. Missing keys fall
back to English so a partially translated future feature never becomes an empty
control.

### SSH remote session management

Remote hosts are first-class entities rather than labels attached to a local
thread. Each host has independent trust, health, ingestion cursors, workspace
roots, and app-server ownership state.

Implemented security contract:

- identity-file or `ssh-agent` authentication;
- password fields are rejected and never persisted;
- `BatchMode=yes` and bounded connection/command timeouts;
- strict host-key verification by default;
- optional explicit first-use enrollment with `accept-new`;
- changed host keys still fail closed;
- optional jump host;
- no arbitrary remote-shell endpoint;
- fixed, quoted, read-only collection probes;
- host-scoped session keys, so identical Codex thread IDs on two hosts do not
  collide;
- incremental remote JSONL reads with durable byte cursors;
- dashboard-owned app-server PID files, so stop/delete never kills unrelated
  Codex processes.

The remote projection includes repository root, branch, HEAD, dirty state,
ahead/behind, merge conflict evidence, context/token/rate data when Codex emits
it, and host connection health. Controls remain disabled unless the connected
adapter advertises the corresponding capability.

## Community needs implemented in this release

| Need observed in the community | v0.2.0 response |
| --- | --- |
| SSH/cloud development and cross-machine visibility | Secure SSH hosts, host-scoped IDs, remote rollout collection, health, jump hosts, and managed app-server lifecycle |
| Context-window and quota visibility | Input/cache/output/reasoning/total tokens, context pressure, primary/secondary rate-limit pressure; `Unknown` when no denominator exists |
| Central operator inbox | Approval/input, failure, conflict, SSH outage, context/rate pressure, storage/process degradation, and concurrent-worktree warnings |
| Completion/background notification | Opt-in browser notifications with focus suppression and event de-duplication |
| Large history navigation | Search, host/state/tag filtering, bounded pages, editable labels/tags where supported, and stable host-aware identity |
| Parallel-session safety | Same-host/same-worktree concurrent writer detection plus dirty/conflict/ahead/behind evidence |
| Storage and process health | Bounded incremental reads, host diagnostics, history/disk thresholds, and dashboard-owned app-server leak detection |
| Localization | Four runtime locales with English fallback |
| Sensitive data protection | Pre-persistence credential redaction, excluded paths, no SSH password storage, and audit records for control actions |

## Deliberate product boundaries

The dashboard does not pretend to own capabilities that belong to Codex, a
model provider, or an IDE:

- LSP server installation and semantic-edit loops;
- subscription quota policy or model context-window policy;
- native JetBrains/VS Code editor integrations;
- model/provider selection for Codex subagents;
- a provenance-safe atomic rewind of both conversation and workspace state;
- automatic answers to Codex plan-mode questions.

These are shown only when upstream capability negotiation provides a real
operation. Unsupported capabilities remain unavailable instead of mutating only
the dashboard database.

## Research sources

Primary Codex sources and representative community requests:

- App-server protocol: <https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>
- Remote development over SSH: <https://github.com/openai/codex/issues/10450>
- Remote control from another device: <https://github.com/openai/codex/issues/9224>
- Context/token visibility: <https://github.com/openai/codex/issues/23794>
- Completion sound/notification: <https://github.com/openai/codex/issues/3962>
- Rename/history navigation: <https://github.com/openai/codex/issues/12564>
- Central diff/approval: <https://github.com/openai/codex/issues/2998>
- Conversation + workspace rewind: <https://github.com/openai/codex/issues/11626>
- SQLite logging/write amplification: <https://github.com/openai/codex/issues/28224>
- Sensitive path exclusions: <https://github.com/openai/codex/issues/2847>

Adjacent session dashboards reviewed:

- <https://github.com/ArnabCodes/codex-dash>
- <https://github.com/jstuart0/agentpulse>

## Release gate

The repository publication workflow refuses to create the release marker or
move `v0.2.0` unless all of these pass:

1. source feature probes for locales, SSH hardening, context semantics, and
   worktree safety;
2. Python bytecode compilation;
3. JavaScript syntax validation;
4. the complete automated test suite;
5. route validation that prevents a session-detail wildcard from shadowing
   action and event endpoints.

A successful run writes `PUBLISH_COMPLETE_v0.2.0.json` and then points the
annotated `v0.2.0` tag at the verified `main` commit.
