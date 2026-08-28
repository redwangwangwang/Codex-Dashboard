from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import Config
from .server import serve
from .service import DashboardService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-dashboard",
        description="Local-first observability and control dashboard for Codex sessions.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    server = subparsers.add_parser("serve", help="run the dashboard web server")
    server.add_argument("--host", default=None, help="bind address (default: 127.0.0.1)")
    server.add_argument("--port", type=int, default=None, help="bind port (default: 8765)")
    server.add_argument("--token", default=None, help="API bearer token; required outside loopback")
    server.add_argument("--codex-home", type=Path, default=None, help="Codex data directory")
    server.add_argument("--data-dir", type=Path, default=None, help="dashboard state directory")
    server.add_argument("--no-browser", action="store_true", help="do not open a browser")

    scan = subparsers.add_parser("scan", help="perform one collection pass and print a summary")
    scan.add_argument("--codex-home", type=Path, default=None)
    scan.add_argument("--data-dir", type=Path, default=None)

    doctor = subparsers.add_parser("doctor", help="check Codex, storage, and collector availability")
    doctor.add_argument("--codex-home", type=Path, default=None)
    doctor.add_argument("--data-dir", type=Path, default=None)

    demo = subparsers.add_parser("demo", help="seed representative sessions for UI evaluation")
    demo.add_argument("--data-dir", type=Path, default=None)
    demo.add_argument("--reset", action="store_true")
    return parser


def _config(args: argparse.Namespace) -> Config:
    overrides = {
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
        "token": getattr(args, "token", None),
        "codex_home": getattr(args, "codex_home", None),
        "data_dir": getattr(args, "data_dir", None),
    }
    return Config.from_env(**overrides)


def _serve_command(args: argparse.Namespace) -> int:
    config = _config(args)
    ready = threading.Event()
    error: list[BaseException] = []

    def run() -> None:
        try:
            serve(config, ready=ready)
        except BaseException as exc:  # surfaced to the CLI thread
            error.append(exc)
            ready.set()

    thread = threading.Thread(target=run, name="codex-dashboard-server", daemon=False)
    thread.start()
    ready.wait(10)
    if error:
        raise error[0]
    display_host = "127.0.0.1" if config.host in {"0.0.0.0", "::"} else config.host
    url = f"http://{display_host}:{config.port}/"
    if config.token:
        url += f"?token={config.token}"
    print(f"Codex Control Center: {url}")
    print(f"Data: {config.database_path}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while thread.is_alive():
            thread.join(1)
    except KeyboardInterrupt:
        print("\nStopping…", file=sys.stderr)
        # Daemon shutdown is coordinated by process termination; managed children receive cleanup.
        return 130
    if error:
        raise error[0]
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    try:
        if command == "serve":
            if args.command is None:
                args = parser.parse_args(["serve", *(argv or [])])
            return _serve_command(args)
        service = DashboardService(_config(args))
        if command == "scan":
            changed = service.collector.scan_once()
            print(json.dumps({"changed": changed, "overview": service.overview()}, ensure_ascii=False, indent=2))
            return 0
        if command == "doctor":
            result = service.doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if command == "demo":
            print(json.dumps(service.seed_demo(reset=args.reset), ensure_ascii=False, indent=2))
            return 0
        parser.error(f"unknown command: {command}")
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
