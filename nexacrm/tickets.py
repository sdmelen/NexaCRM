"""Локальные заявки с идемпотентным подтверждением в SQLite."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class TicketError(RuntimeError):
    """Заявку не удалось проверить или сохранить."""


@dataclass(frozen=True)
class Ticket:
    id: int
    created_at: str
    title: str
    description: str
    reason: str
    conversation: list[dict[str, str]]
    status: str


@contextmanager
def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                reason TEXT NOT NULL,
                conversation_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                request_id TEXT NOT NULL UNIQUE
            )
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def _ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"], created_at=row["created_at"], title=row["title"],
        description=row["description"], reason=row["reason"],
        conversation=json.loads(row["conversation_json"]), status=row["status"],
    )


def create_ticket(
    path: Path, *, request_id: str, title: str, description: str,
    reason: str, conversation: list[dict[str, str]],
) -> Ticket:
    title = title.strip()
    description = description.strip()
    if not request_id or not title or not description:
        raise TicketError("Заполните тему и описание заявки.")
    if len(title) > 200 or len(description) > 5000:
        raise TicketError("Тема или описание заявки слишком длинные.")
    if reason not in {"user_request", "insufficient_information", "session_limit"}:
        raise TicketError("Неизвестная причина создания заявки.")
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in conversation if item.get("role") in {"user", "assistant"}
    ]
    try:
        with _connect(path) as connection:
            connection.execute(
                """INSERT INTO tickets
                   (created_at, title, description, reason, conversation_json, status, request_id)
                   VALUES (?, ?, ?, ?, ?, 'new', ?)
                   ON CONFLICT(request_id) DO NOTHING""",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), title,
                 description, reason, json.dumps(history, ensure_ascii=False), request_id),
            )
            row = connection.execute(
                "SELECT * FROM tickets WHERE request_id = ?", (request_id,)
            ).fetchone()
        return _ticket(row)
    except (OSError, sqlite3.Error) as exc:
        raise TicketError("Не удалось сохранить заявку в локальную базу.") from exc


def list_tickets(path: Path) -> list[Ticket]:
    if not path.exists():
        return []
    try:
        with _connect(path) as connection:
            rows = connection.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall()
        return [_ticket(row) for row in rows]
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise TicketError("Не удалось прочитать локальные заявки.") from exc
