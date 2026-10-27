import sqlite3

import pytest

from nexacrm.tickets import TicketError, create_ticket, list_tickets


def test_ticket_persists_and_confirmation_is_idempotent(tmp_path) -> None:
    path = tmp_path / "nested" / "tickets.sqlite3"
    history = [{"role": "user", "content": "Нужна помощь"}]

    first = create_ticket(
        path, request_id="one-confirmation", title="  Вопрос  ",
        description="  Не работает  ", reason="user_request", conversation=history,
    )
    repeated = create_ticket(
        path, request_id="one-confirmation", title="Другая тема",
        description="Другое описание", reason="user_request", conversation=[],
    )

    assert first.id == repeated.id
    assert first.title == "Вопрос"
    assert first.description == "Не работает"
    assert first.conversation == history
    assert first.status == "new"
    assert list_tickets(path) == [first]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_tickets_are_newest_first_and_reject_empty_fields(tmp_path) -> None:
    path = tmp_path / "tickets.sqlite3"
    assert list_tickets(path) == []
    assert not path.exists()
    with pytest.raises(TicketError):
        create_ticket(
            path, request_id="invalid", title=" ", description="Описание",
            reason="user_request", conversation=[],
        )
    assert not path.exists()

    first = create_ticket(
        path, request_id="first", title="Первая", description="Текст",
        reason="user_request", conversation=[],
    )
    second = create_ticket(
        path, request_id="second", title="Вторая", description="Текст",
        reason="session_limit", conversation=[],
    )
    assert [ticket.id for ticket in list_tickets(path)] == [second.id, first.id]
