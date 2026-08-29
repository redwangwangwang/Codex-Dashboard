# Community research: Codex session management (August 2026)

The release review sampled current `openai/codex` issues, app-server protocol
material, and adjacent community dashboards. Requests were grouped into operator
problems instead of copied as an unbounded feature list.

## Implemented in v0.2.0

1. **Remote development and fleet visibility.** Requests such as
   `openai/codex#10450` and `#9224` describe SSH/cloud development and remote
   control. v0.2.0 adds key/agent-based SSH hosts, strict host verification,
   jump-host support, incremental remote histories, host-scoped IDs, health,
   managed app-server lifecycle, and repository/process/disk evidence.
2. **Context, quota, and storage pressure.** `#23794`, `#14593`, `#28879`, and
   `#28224` motivate visible context/rate usage and bounded storage. The
   dashboard projects counters only when present, computes percentages only
   with a denominator, monitors pressure, and uses incremental bounded reads.
3. **Central attention and approvals.** `#2998` and multi-session operator tools
   motivate a single queue. The dashboard combines approval/input, failure,
   conflict, stale, host outage, context/rate, and worktree collision signals.
4. **Completion/background notification.** `#3962` motivates opt-in browser
   notifications for completion and other actionable transitions.
5. **History discoverability.** `#12564` motivates titles; operators also need
   tags, host-aware search, filters, archive, and pagination at fleet scale.
6. **Parallel safety and recovery evidence.** `#11626` and `#9203` motivate
   safer iteration. The dashboard exposes dirty/conflict/diff evidence and
   destructive confirmations, but does not fake a provenance-safe workspace
   rewind.
7. **Localization.** Four runtime-selectable languages are shipped with English
   fallback and no restart requirement.

## Deliberate boundaries

LSP installation (`#8745`), model quota/context policy (`#19464`, `#30364`),
native IDE plugins (`#4313`), Codex plan semantics (`#2101`, `#28969`),
subagent routing (`#2604`, `#31814`), and a chat-plus-workspace `/rewind`
(`#11626`) require Codex, model-provider, or IDE support. The dashboard observes
and capability-gates these surfaces instead of displaying controls that only
change dashboard state.

## Adjacent projects

- `ArnabCodes/codex-dash` is a lightweight local TUI and synced-snapshot board.
- `jstuart0/agentpulse` combines broad multi-agent observation/orchestration and
  an operator inbox.

This project keeps a narrower evidence contract: Codex-native events,
orthogonal states, conservative Unknown semantics, SSH trust boundaries,
repository/worktree collision evidence, and immutable local audit history.

## Source index

- https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
- https://github.com/openai/codex/issues/10450
- https://github.com/openai/codex/issues/9224
- https://github.com/openai/codex/issues/23794
- https://github.com/openai/codex/issues/3962
- https://github.com/openai/codex/issues/12564
- https://github.com/openai/codex/issues/2998
- https://github.com/openai/codex/issues/11626
- https://github.com/openai/codex/issues/28224
- https://github.com/ArnabCodes/codex-dash
- https://github.com/jstuart0/agentpulse
