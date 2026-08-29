# SSH remote security model

Each SSH host is a separate trust, identity, cursor, and failure domain.

- Authentication uses an OpenSSH identity file or ssh-agent. Passwords are
  rejected by the validation model and never stored.
- Strict host-key checking is enabled by default. First-use enrollment requires
  an explicit per-host setting; changed keys still fail closed.
- Local SSH execution uses an argv array, BatchMode, bounded connection and
  command timeouts, keepalives, and validated host fields.
- Remote shell text is generated only by fixed collectors and quoted paths. The
  HTTP API never accepts an arbitrary remote command.
- Workspace roots can constrain repository probes.
- Incremental cursors prevent repeatedly copying complete history files.
- Session keys include host identity.
- Only app-server processes using the dashboard's own PID file are stopped.
  Existing TUI sessions and unrelated processes are not killed.
- All stored payloads pass through credential redaction before immutable event
  insertion.

Bind the HTTP service to loopback. For another device, use an authenticated VPN
or reverse proxy; do not expose an authentication-disabled instance publicly.
