"""Service health checks with durable last-failure diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any, Callable, Iterable


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class HealthResult:
    component: str
    ok: bool
    status: str
    message: str
    checked_at: str = field(default_factory=_now_iso)
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


HealthProbe = Callable[[], Any]


class HealthRegistry:
    def __init__(self, history_path: Path | None = None) -> None:
        self.probes: dict[str, HealthProbe] = {}
        self.history_path = Path(history_path) if history_path else None

    def register(self, name: str, probe: HealthProbe) -> None:
        self.probes[name] = probe

    def run(self, names: Iterable[str] | None = None) -> list[HealthResult]:
        import time
        selected = list(names) if names is not None else list(self.probes)
        results: list[HealthResult] = []
        for name in selected:
            probe = self.probes.get(name)
            if probe is None:
                results.append(HealthResult(name, False, "not_registered", "未注册健康检查。"))
                continue
            started = time.perf_counter()
            try:
                raw = probe()
                latency = round((time.perf_counter() - started) * 1000)
                if isinstance(raw, HealthResult):
                    result = raw
                    result.latency_ms = result.latency_ms if result.latency_ms is not None else latency
                else:
                    ok = bool(getattr(raw, "ok", raw))
                    result = HealthResult(
                        name, ok, str(getattr(raw, "status", "ready" if ok else "unavailable")),
                        str(getattr(raw, "message", "可用" if ok else "不可用")), latency_ms=latency,
                        details=dict(getattr(raw, "details", {}) or {}),
                    )
            except Exception as error:
                result = HealthResult(name, False, error.__class__.__name__, str(error)[:1000])
            results.append(result)
        self._save(results)
        return results

    def _save(self, results: list[HealthResult]) -> None:
        if self.history_path is None:
            return
        prior = self.load_history()
        latest = {item.component: item.to_dict() for item in results}
        failures = prior.get("recent_failures", []) if isinstance(prior, dict) else []
        failures.extend(item.to_dict() for item in results if not item.ok)
        payload = {"updated_at": _now_iso(), "latest": latest, "recent_failures": failures[-100:]}
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.history_path.with_suffix(self.history_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.history_path)

    def load_history(self) -> dict[str, Any]:
        if self.history_path is None or not self.history_path.exists():
            return {}
        try:
            value = json.loads(self.history_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}


def local_runtime_probes() -> dict[str, HealthProbe]:
    def command(name: str) -> HealthProbe:
        return lambda: HealthResult(
            name, bool(shutil.which(name)), "ready" if shutil.which(name) else "missing",
            f"已找到 {shutil.which(name)}" if shutil.which(name) else f"未安装可选组件 {name}",
        )

    return {"OCR (ocrmypdf)": command("ocrmypdf"), "PDF 导出 (Edge)": command("msedge")}


def render_health_markdown(results: Iterable[HealthResult]) -> str:
    lines = ["# 系统健康检查", "", "| 组件 | 状态 | 延迟 | 说明 |", "| --- | --- | ---: | --- |"]
    for item in results:
        message = item.message.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item.component} | {'可用' if item.ok else '不可用'} / {item.status} | {item.latency_ms if item.latency_ms is not None else '—'} ms | {message} |")
    return "\n".join(lines).rstrip() + "\n"

