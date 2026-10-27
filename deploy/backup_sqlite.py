"""Ежедневная согласованная копия SQLite, без копирования занятого файла."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE = Path(os.environ.get("TICKETS_PATH", "/srv/nexacrm/shared/tickets.sqlite3"))
BACKUP_DIR = Path("/srv/nexacrm/backups")
KEEP = 7


def main() -> None:
    if not SOURCE.is_file():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d")
    destination = BACKUP_DIR / f"tickets-{stamp}.sqlite3"
    temporary = BACKUP_DIR / f".tickets-{stamp}.tmp"
    try:
        with closing(sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    for old in sorted(BACKUP_DIR.glob("tickets-*.sqlite3"))[:-KEEP]:
        old.unlink()


if __name__ == "__main__":
    main()
