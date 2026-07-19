"""Runtime discovery shared by local connector adapters."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

from .base import IntegrationError


def find_node() -> Path | None:
    discovered = shutil.which("node")
    candidates = [
        Path(discovered) if discovered else None,
        Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin" / "node.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def hidden_subprocess_kwargs() -> dict[str, int]:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_json_command(
    command: Sequence[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IntegrationError(f"连接器命令无法运行：{error}") from error
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "连接器执行失败").strip()
        raise IntegrationError(message[:1200])
    return result.stdout.strip()
