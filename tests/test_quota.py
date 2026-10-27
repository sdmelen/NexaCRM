from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nexacrm.quota import QuotaExceeded, reserve_question


def test_quota_is_shared_and_resets_at_moscow_midnight(tmp_path) -> None:
    path = tmp_path / "tickets.sqlite3"
    moscow = ZoneInfo("Europe/Moscow")
    before_midnight = datetime(2026, 10, 27, 23, 59, tzinfo=moscow)

    assert reserve_question(path, 2, now=before_midnight) == 1
    assert reserve_question(path, 2, now=before_midnight) == 0
    with pytest.raises(QuotaExceeded):
        reserve_question(path, 2, now=before_midnight)

    assert reserve_question(path, 2, now=before_midnight + timedelta(minutes=1)) == 1
