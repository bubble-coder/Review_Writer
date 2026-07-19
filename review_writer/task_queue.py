"""Persistent local task queue for long-running desktop research jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import traceback
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATES = {"pending", "running", "retry_wait", "paused"}


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    task_type: str
    state: str
    payload: dict[str, Any]
    progress_current: int = 0
    progress_total: int = 0
    progress_message: str = ""
    checkpoint: dict[str, Any] | None = None
    attempts: int = 0
    max_attempts: int = 3
    created_at: str = ""
    updated_at: str = ""
    next_attempt_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error_code: str = ""
    error_message: str = ""
    result: dict[str, Any] | None = None
    idempotency_key: str = ""
    cancel_requested: bool = False

    @property
    def progress_fraction(self) -> float:
        return min(1.0, self.progress_current / self.progress_total) if self.progress_total else 0.0


class TaskCancelled(RuntimeError):
    pass


class RetryableTaskError(RuntimeError):
    def __init__(self, message: str, *, code: str = "retryable_error") -> None:
        super().__init__(message)
        self.code = code


class TaskStore:
    """SQLite-backed queue with atomic claiming and append-only event logs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    progress_message TEXT NOT NULL DEFAULT '',
                    checkpoint_json TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency
                    ON tasks(idempotency_key) WHERE idempotency_key <> '';
                CREATE INDEX IF NOT EXISTS idx_tasks_claim
                    ON tasks(state, next_attempt_at, created_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                """
            )

    def enqueue(
        self,
        task_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str = "",
        max_attempts: int = 3,
    ) -> TaskRecord:
        now = _iso()
        with self._lock, self._connect() as db:
            if idempotency_key:
                row = db.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row is not None:
                    return self._record(row)
            task_id = f"task-{uuid4().hex}"
            db.execute(
                """INSERT INTO tasks(
                    task_id, task_type, state, payload_json, max_attempts,
                    created_at, updated_at, idempotency_key
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
                (task_id, task_type, _json(dict(payload)), max(1, max_attempts), now, now, idempotency_key),
            )
            self._event(db, task_id, "info", "enqueued", "任务已进入队列", {})
            return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._record(row)

    def list(self, *, states: set[str] | None = None, limit: int = 200) -> list[TaskRecord]:
        with self._connect() as db:
            if states:
                placeholders = ",".join("?" for _ in states)
                rows = db.execute(
                    f"SELECT * FROM tasks WHERE state IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
                    (*sorted(states), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._record(row) for row in rows]

    def recover_interrupted(self) -> int:
        """Return orphaned running jobs to pending after application restart."""

        with self._lock, self._connect() as db:
            rows = db.execute("SELECT task_id FROM tasks WHERE state = 'running'").fetchall()
            now = _iso()
            db.execute(
                """UPDATE tasks SET state='pending', updated_at=?, progress_message=?
                   WHERE state='running'""",
                (now, "应用重新启动，任务将从检查点继续"),
            )
            for row in rows:
                self._event(db, row["task_id"], "warning", "recovered", "检测到中断并恢复为待执行", {})
        return len(rows)

    def claim_next(self) -> TaskRecord | None:
        now = _iso()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM tasks
                   WHERE (state='pending' OR (state='retry_wait' AND next_attempt_at <= ?))
                     AND cancel_requested=0
                   ORDER BY created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return None
            db.execute(
                """UPDATE tasks SET state='running', attempts=attempts+1,
                   started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                   updated_at=?, progress_message='正在执行'
                   WHERE task_id=?""",
                (now, now, row["task_id"]),
            )
            self._event(db, row["task_id"], "info", "started", "任务开始执行", {})
            db.execute("COMMIT")
        return self.get(row["task_id"])

    def start(self, task_id: str) -> TaskRecord:
        """Atomically start a known pending task (primarily useful in tests/UI)."""

        now = _iso()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """UPDATE tasks SET state='running', attempts=attempts+1,
                   started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                   updated_at=?, progress_message='正在执行'
                   WHERE task_id=? AND state IN ('pending','retry_wait') AND cancel_requested=0""",
                (now, now, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("任务当前不可启动。")
            self._event(db, task_id, "info", "started", "任务开始执行", {})
        return self.get(task_id)

    def update_progress(
        self,
        task_id: str,
        current: int,
        total: int,
        message: str = "",
        *,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> None:
        if current < 0 or total < 0 or (total and current > total):
            raise ValueError("任务进度必须满足 0 <= current <= total。")
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET progress_current=?, progress_total=?, progress_message=?,
                   checkpoint_json=COALESCE(?, checkpoint_json), updated_at=? WHERE task_id=?""",
                (current, total, message[:500], _json(dict(checkpoint)) if checkpoint is not None else None, _iso(), task_id),
            )
            self._event(db, task_id, "info", "progress", message or "进度更新", {"current": current, "total": total})

    def log(self, task_id: str, message: str, *, level: str = "info", event_type: str = "log", details: Mapping[str, Any] | None = None) -> None:
        with self._connect() as db:
            self._event(db, task_id, level, event_type, message, dict(details or {}))

    def request_cancel(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET state='cancelled', cancel_requested=1, finished_at=?,
                   updated_at=?, progress_message='已取消'
                   WHERE task_id=? AND state IN ('pending','retry_wait','paused')""",
                (_iso(), _iso(), task_id),
            )
            db.execute(
                """UPDATE tasks SET cancel_requested=1, updated_at=?, progress_message='正在取消…'
                   WHERE task_id=? AND state='running'""",
                (_iso(), task_id),
            )
            self._event(db, task_id, "warning", "cancel_requested", "用户请求取消任务", {})

    def pause(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE tasks SET state='paused', updated_at=? WHERE task_id=? AND state IN ('pending','retry_wait')", (_iso(), task_id))

    def resume(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE tasks SET state='pending', cancel_requested=0, updated_at=? WHERE task_id IN (SELECT task_id FROM tasks WHERE task_id=? AND state IN ('paused','failed','cancelled'))", (_iso(), task_id))

    def succeed(self, task_id: str, result: Mapping[str, Any] | None = None) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET state='succeeded', result_json=?, finished_at=?,
                   updated_at=?, progress_message='已完成' WHERE task_id=?""",
                (_json(dict(result or {})), _iso(), _iso(), task_id),
            )
            self._event(db, task_id, "info", "succeeded", "任务执行完成", {})

    def cancel(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE tasks SET state='cancelled', finished_at=?, updated_at=?, progress_message='已取消' WHERE task_id=?", (_iso(), _iso(), task_id))
            self._event(db, task_id, "warning", "cancelled", "任务已取消", {})

    def fail(self, task_id: str, error: Exception, *, retryable: bool = False) -> None:
        record = self.get(task_id)
        code = getattr(error, "code", error.__class__.__name__)
        message = str(error)[:1000] or error.__class__.__name__
        can_retry = retryable and record.attempts < record.max_attempts
        state = "retry_wait" if can_retry else "failed"
        delay = min(300, 2 ** max(0, record.attempts - 1))
        next_at = _iso(_now() + timedelta(seconds=delay)) if can_retry else ""
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET state=?, error_code=?, error_message=?,
                   next_attempt_at=?, finished_at=?, updated_at=?, progress_message=?
                   WHERE task_id=?""",
                (state, str(code), message, next_at, "" if can_retry else _iso(), _iso(), "等待重试" if can_retry else "执行失败", task_id),
            )
            self._event(db, task_id, "warning" if can_retry else "error", state, message, {"retry_in_seconds": delay if can_retry else 0})

    def events(self, task_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY event_id LIMIT ?", (task_id, limit)
            ).fetchall()
        return [
            {
                "event_id": row["event_id"], "task_id": row["task_id"], "at": row["at"],
                "level": row["level"], "event_type": row["event_type"], "message": row["message"],
                "details": json.loads(row["details_json"] or "{}"),
            }
            for row in rows
        ]

    @staticmethod
    def _event(db: sqlite3.Connection, task_id: str, level: str, event_type: str, message: str, details: Mapping[str, Any]) -> None:
        db.execute(
            "INSERT INTO task_events(task_id, at, level, event_type, message, details_json) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, _iso(), level, event_type, message[:1000], _json(dict(details))),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"], task_type=row["task_type"], state=row["state"],
            payload=json.loads(row["payload_json"] or "{}"),
            progress_current=row["progress_current"], progress_total=row["progress_total"],
            progress_message=row["progress_message"],
            checkpoint=json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None,
            attempts=row["attempts"], max_attempts=row["max_attempts"], created_at=row["created_at"],
            updated_at=row["updated_at"], next_attempt_at=row["next_attempt_at"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            error_code=row["error_code"], error_message=row["error_message"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            idempotency_key=row["idempotency_key"], cancel_requested=bool(row["cancel_requested"]),
        )


class TaskContext:
    def __init__(self, store: TaskStore, task_id: str) -> None:
        self.store = store
        self.task_id = task_id

    @property
    def record(self) -> TaskRecord:
        return self.store.get(self.task_id)

    @property
    def checkpoint(self) -> dict[str, Any]:
        return dict(self.record.checkpoint or {})

    def check_cancelled(self) -> None:
        if self.record.cancel_requested:
            raise TaskCancelled("任务已由用户取消")

    def progress(self, current: int, total: int, message: str = "", *, checkpoint: Mapping[str, Any] | None = None) -> None:
        self.check_cancelled()
        self.store.update_progress(self.task_id, current, total, message, checkpoint=checkpoint)

    def log(self, message: str, *, level: str = "info", details: Mapping[str, Any] | None = None) -> None:
        self.store.log(self.task_id, message, level=level, details=details)


TaskHandler = Callable[[TaskContext, dict[str, Any]], Mapping[str, Any] | None]


class TaskWorker:
    """Small cooperative worker suitable for a Tk desktop process."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.handlers: dict[str, TaskHandler] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, task_type: str, handler: TaskHandler) -> None:
        self.handlers[task_type] = handler

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="review-writer-task-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def run_once(self) -> bool:
        task = self.store.claim_next()
        if task is None:
            return False
        context = TaskContext(self.store, task.task_id)
        handler = self.handlers.get(task.task_type)
        if handler is None:
            self.store.fail(task.task_id, RuntimeError(f"未注册任务处理器：{task.task_type}"))
            return True
        try:
            result = handler(context, task.payload)
            context.check_cancelled()
        except TaskCancelled:
            self.store.cancel(task.task_id)
        except RetryableTaskError as error:
            self.store.fail(task.task_id, error, retryable=True)
        except Exception as error:
            self.store.log(task.task_id, traceback.format_exc(limit=12), level="error", event_type="traceback")
            self.store.fail(task.task_id, error, retryable=False)
        else:
            self.store.succeed(task.task_id, result)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(0.5)
                self._wake.clear()
