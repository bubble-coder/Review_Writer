"""Read-only cross-project paper, evidence, and reading-note index."""

from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping

from .workflow_models import PaperRecord, ReadingNote


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EvidenceIndex:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fts = True
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS indexed_projects(
                    project_id TEXT PRIMARY KEY, project_path TEXT NOT NULL,
                    title TEXT NOT NULL, indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS indexed_content(
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL, paper_id TEXT NOT NULL,
                    content_type TEXT NOT NULL, locator TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL,
                    evidence_level TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(project_id, paper_id, content_type, locator)
                );
                """
            )
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS indexed_content_fts USING fts5(title, body, content='indexed_content', content_rowid='row_id')")
                db.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS indexed_content_ai AFTER INSERT ON indexed_content BEGIN
                      INSERT INTO indexed_content_fts(rowid,title,body) VALUES(new.row_id,new.title,new.body);
                    END;
                    CREATE TRIGGER IF NOT EXISTS indexed_content_ad AFTER DELETE ON indexed_content BEGIN
                      INSERT INTO indexed_content_fts(indexed_content_fts,rowid,title,body) VALUES('delete',old.row_id,old.title,old.body);
                    END;
                    CREATE TRIGGER IF NOT EXISTS indexed_content_au AFTER UPDATE ON indexed_content BEGIN
                      INSERT INTO indexed_content_fts(indexed_content_fts,rowid,title,body) VALUES('delete',old.row_id,old.title,old.body);
                      INSERT INTO indexed_content_fts(rowid,title,body) VALUES(new.row_id,new.title,new.body);
                    END;
                    """
                )
            except sqlite3.OperationalError:
                self._fts = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def index_project(
        self,
        project_id: str,
        project_path: Path,
        title: str,
        papers: Iterable[PaperRecord],
        notes: Iterable[ReadingNote],
    ) -> int:
        papers = list(papers)
        notes_by_id = {note.paper_id: note for note in notes}
        with self._connect() as db:
            db.execute("DELETE FROM indexed_content WHERE project_id=?", (project_id,))
            db.execute(
                "INSERT OR REPLACE INTO indexed_projects VALUES (?, ?, ?, ?)",
                (project_id, str(Path(project_path).resolve()), title, _now_iso()),
            )
            count = 0
            for paper in papers:
                metadata = " ".join(filter(None, [paper.title, paper.abstract, paper.journal, " ".join(paper.authors)]))
                db.execute(
                    """INSERT INTO indexed_content(project_id,paper_id,content_type,locator,title,body,evidence_level,payload_json)
                       VALUES (?, ?, 'paper', 'metadata', ?, ?, ?, ?)""",
                    (project_id, paper.record_id, paper.title, metadata, paper.evidence_level.value, json.dumps(paper.to_dict(), ensure_ascii=False)),
                )
                count += 1
                note = notes_by_id.get(paper.record_id)
                if note:
                    for block in note.evidence_blocks:
                        db.execute(
                            """INSERT INTO indexed_content(project_id,paper_id,content_type,locator,title,body,evidence_level,payload_json)
                               VALUES (?, ?, 'evidence', ?, ?, ?, ?, ?)""",
                            (project_id, paper.record_id, block.locator or block.block_id, paper.title, block.text, block.evidence_level.value, json.dumps(block.to_dict(), ensure_ascii=False)),
                        )
                        count += 1
            return count

    def search(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        with self._connect() as db:
            if self._fts:
                try:
                    rows = db.execute(
                        """SELECT c.*, p.project_path, p.title AS project_title
                           FROM indexed_content_fts f JOIN indexed_content c ON c.row_id=f.rowid
                           JOIN indexed_projects p ON p.project_id=c.project_id
                           WHERE indexed_content_fts MATCH ? ORDER BY bm25(indexed_content_fts) LIMIT ?""",
                        (query, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
                rows = []
            if not rows:
                pattern = f"%{query}%"
                rows = db.execute(
                    """SELECT c.*, p.project_path, p.title AS project_title
                       FROM indexed_content c JOIN indexed_projects p ON p.project_id=c.project_id
                       WHERE c.title LIKE ? OR c.body LIKE ? LIMIT ?""",
                    (pattern, pattern, limit),
                ).fetchall()
        return [{key: row[key] for key in row.keys() if key != "payload_json"} | {"payload": json.loads(row["payload_json"])} for row in rows]
