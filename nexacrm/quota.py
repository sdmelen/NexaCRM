"""Общий суточный лимит запросов к API для публичного демо."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")


class QuotaError(RuntimeError):
    """Состояние лимита недоступно."""


class QuotaExceeded(QuotaError):
    """Суточный лимит исчерпан."""


def reserve_question(path: Path, limit: int, *, now: datetime | None = None) -> int:
    """Атомарно учесть попытку до вызова API; вернуть остаток на этот день."""
    if limit <= 0:
        raise ValueError("Лимит должен быть положительным")
    day = (now or datetime.now(MOSCOW)).astimezone(MOSCOW).date().isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS daily_usage "
                    "(day TEXT PRIMARY KEY, questions INTEGER NOT NULL)"
                )
                row = connection.execute(
                    "SELECT questions FROM daily_usage WHERE day = ?", (day,)
                ).fetchone()
                used = row[0] if row else 0
                if used >= limit:
                    raise QuotaExceeded("Дневной лимит вопросов исчерпан")
                connection.execute(
                    "INSERT INTO daily_usage (day, questions) VALUES (?, 1) "
                    "ON CONFLICT(day) DO UPDATE SET questions = questions + 1",
                    (day,),
                )
                return limit - used - 1
    except QuotaExceeded:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise QuotaError("Не удалось проверить дневной лимит") from exc
