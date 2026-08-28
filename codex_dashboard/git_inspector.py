from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .config import Config
from .util import compact_text


class GitInspector:
    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def _run(cwd: str | Path, args: list[str], *, timeout: int = 8, limit: int = 2_000_000) -> str:
        process = subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if process.returncode != 0:
            return ""
        return compact_text(process.stdout, limit)

    def root(self, cwd: str | Path) -> str:
        path = Path(cwd).expanduser()
        if not path.exists():
            return ""
        return self._run(path, ["rev-parse", "--show-toplevel"], timeout=3, limit=10_000).strip()

    def inspect(self, cwd: str | Path) -> dict[str, Any]:
        root = self.root(cwd)
        if not root:
            return {"is_git": False, "root": "", "branch": "", "head": "", "changes": []}
        status = self._run(root, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"], limit=1_000_000)
        entries = [item for item in status.split("\x00") if item]
        changes: list[dict[str, str]] = []
        for entry in entries:
            if len(entry) < 4:
                continue
            code = entry[:2]
            path = entry[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            action = "modified"
            if "?" in code or "A" in code:
                action = "added"
            elif "D" in code:
                action = "deleted"
            elif "R" in code:
                action = "renamed"
            changes.append({"path": path, "action": action, "status": code})
        return {
            "is_git": True,
            "root": root,
            "branch": self._run(root, ["branch", "--show-current"], timeout=3, limit=1000).strip(),
            "head": self._run(root, ["rev-parse", "HEAD"], timeout=3, limit=1000).strip(),
            "changes": changes,
        }

    def diff(self, cwd: str | Path, *, path: str | None = None, staged: bool = False) -> str:
        root = self.root(cwd)
        if not root:
            return ""
        args = ["diff", "--no-ext-diff", "--no-color", "--src-prefix=a/", "--dst-prefix=b/"]
        if staged:
            args.append("--cached")
        if path:
            candidate = (Path(root) / path).resolve()
            root_path = Path(root).resolve()
            if candidate != root_path and root_path not in candidate.parents:
                raise ValueError("diff path escapes repository root")
            args.extend(["--", str(candidate.relative_to(root_path))])
        diff = self._run(root, args, timeout=12, limit=self.config.max_diff_bytes)
        if not staged:
            untracked = [change["path"] for change in self.inspect(root)["changes"] if change["action"] == "added"]
            if path:
                untracked = [item for item in untracked if item == path]
            for relative in untracked[:100]:
                candidate = Path(root, relative)
                if not candidate.is_file() or candidate.stat().st_size > 256_000:
                    continue
                try:
                    content = candidate.read_text("utf-8", errors="replace")
                except OSError:
                    continue
                lines = content.splitlines()
                diff += f"\ndiff --git a/{relative} b/{relative}\nnew file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
                diff += "\n".join(f"+{line}" for line in lines[:5000])
                diff += "\n"
                if len(diff) >= self.config.max_diff_bytes:
                    break
        return compact_text(diff, self.config.max_diff_bytes)
