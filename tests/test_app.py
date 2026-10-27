from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from nexacrm.config import load_config
from nexacrm.knowledge_base import IndexStatus
from nexacrm.llm import AnswerError, GroundedAnswer, Source


def test_public_shell_shows_corpus_without_ticket_list(monkeypatch, tmp_path) -> None:
    import nexacrm.config

    config = replace(load_config(), tickets_path=tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    assert not app.exception
    assert [item.value for item in app.title] == ["AI-оператор NexaCRM"]
    assert any(item.label == "Документов" and item.value == "15" for item in app.metric)
    assert any(item.label == "Индекс знаний" for item in app.metric)
    assert any("Индекс" in item.value for item in [*app.warning, *app.success])

    assert not app.sidebar.radio
    assert not app.selectbox
    assert not config.tickets_path.exists()


def test_new_dialog_is_available_before_first_question() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()

    button = next(item for item in app.button if item.label == "Новый диалог")
    assert not button.disabled

    button.click().run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert any("Новый диалог начат" in item.value for item in app.success)


def test_chat_displays_answer_and_qdrant_source(monkeypatch) -> None:
    import nexacrm.agent
    import nexacrm.config
    import nexacrm.knowledge_base

    config = replace(load_config(), api_key_available=True)
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    monkeypatch.setattr(
        nexacrm.knowledge_base, "get_index_status",
        lambda _config: IndexStatus(True, "Индекс готов.", 15, 68),
    )
    source = Source(
        chunk_id="test-id", source_file="source/guide.md", document_name="guide.md",
        document_title="Руководство", section_title="Приглашения",
        section_path=("Руководство", "Приглашения"), chunk_index=2,
        front_matter={"category": "Доступ"}, text="Ссылка действует 7 дней.",
    )

    class FakeAgent:
        def __init__(self, _config):
            pass

        def answer(self, question, history):
            assert question == "Сколько действует ссылка?"
            assert history == []
            return SimpleNamespace(
                grounded=GroundedAnswer("Ссылка действует 7 дней.", True, (source,))
            )

    monkeypatch.setattr(nexacrm.agent, "SupportAgent", FakeAgent)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    app.chat_input[0].set_value("Сколько действует ссылка?").run()
    assert not app.exception
    assert any("Ссылка действует 7 дней." in item.value for item in app.markdown)
    assert any("Источники (1)" in item.label for item in app.get("expander"))


def test_chat_error_keeps_question_for_retry_without_showing_internal_detail(monkeypatch) -> None:
    import nexacrm.agent
    import nexacrm.config
    import nexacrm.knowledge_base

    config = replace(load_config(), api_key_available=True)
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    monkeypatch.setattr(
        nexacrm.knowledge_base, "get_index_status",
        lambda _config: IndexStatus(True, "Индекс готов.", 15, 68),
    )

    class FailingAgent:
        def __init__(self, _config):
            pass

        def answer(self, question, history):
            raise AnswerError("internal detail")

    monkeypatch.setattr(nexacrm.agent, "SupportAgent", FailingAgent)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    app.chat_input[0].set_value("Сколько действует ссылка?").run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert app.session_state["pending_question"] == "Сколько действует ссылка?"
    assert app.session_state["messages"] == []
    assert any("Попробуйте повторить вопрос" in item.value for item in app.error)
    assert not any("internal detail" in item.value for item in app.error)


def test_explicit_ticket_requires_confirmation_and_appears_only_in_admin(monkeypatch, tmp_path) -> None:
    import nexacrm.config

    config = replace(load_config(), tickets_path=tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()

    next(item for item in app.button if item.label == "Создать заявку").click().run()
    assert not app.exception
    assert not config.tickets_path.exists()
    assert any("Не вводите пароли" in item.value for item in app.warning)

    app.text_input[0].set_value("Вопрос о доступе")
    app.text_area[0].set_value("Не удаётся войти в CRM")
    app.form_submit_button[0].click().run()
    assert not app.exception
    assert app.session_state["ticket_draft"] is None
    assert any("Заявка №1 сохранена" in item.value for item in app.success)

    fresh_public = AppTest.from_file(app_path, default_timeout=15).run()
    assert not fresh_public.selectbox
    assert not any("Не удаётся войти в CRM" in item.value for item in fresh_public.markdown)
    admin = AppTest.from_file(app_path.with_name("admin.py"), default_timeout=15).run()
    assert not admin.exception
    assert admin.selectbox[0].value == 1
    assert any("Не удаётся войти в CRM" in item.value for item in admin.markdown)


def test_fifteenth_question_gets_answer_then_opens_session_ticket(monkeypatch, tmp_path) -> None:
    import nexacrm.agent
    import nexacrm.config
    import nexacrm.knowledge_base

    config = replace(load_config(), api_key_available=True, tickets_path=tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    monkeypatch.setattr(
        nexacrm.knowledge_base, "get_index_status",
        lambda _config: IndexStatus(True, "Индекс готов.", 15, 68),
    )

    class FakeAgent:
        def __init__(self, _config):
            pass

        def answer(self, question, history):
            assert question == "Последний вопрос"
            assert len(history) == 28
            return SimpleNamespace(grounded=GroundedAnswer("Последний ответ", True, ()))

    monkeypatch.setattr(nexacrm.agent, "SupportAgent", FakeAgent)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    app.session_state["messages"] = [
        {"role": role, "content": f"Сообщение {index}"}
        for index in range(14) for role in ("user", "assistant")
    ]
    app.run()
    app.chat_input[0].set_value("Последний вопрос").run()

    assert not app.exception
    assert len(app.session_state["messages"]) == 30
    assert app.chat_input[0].disabled
    assert any("Последний ответ" in item.value for item in app.markdown)
    assert app.session_state["ticket_draft"]["reason"] == "session_limit"
    from nexacrm.tickets import list_tickets
    assert not list_tickets(config.tickets_path)

    next(item for item in app.button if item.label == "Отменить черновик").click().run()
    assert app.session_state["ticket_draft"] is None
    app.run()
    assert app.session_state["ticket_draft"] is None
    next(item for item in app.button if item.label == "Новый диалог").click().run()
    assert app.session_state["messages"] == []
    assert not app.chat_input[0].disabled


def test_unanswered_question_prefills_ticket_with_conversation(monkeypatch, tmp_path) -> None:
    import nexacrm.agent
    import nexacrm.config
    import nexacrm.knowledge_base
    from nexacrm.tickets import list_tickets

    config = replace(load_config(), api_key_available=True, tickets_path=tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(nexacrm.config, "load_config", lambda: config)
    monkeypatch.setattr(
        nexacrm.knowledge_base, "get_index_status",
        lambda _config: IndexStatus(True, "Индекс готов.", 15, 68),
    )

    class FakeAgent:
        def __init__(self, _config):
            pass

        def answer(self, question, history):
            return SimpleNamespace(grounded=GroundedAnswer("Нет данных.", False, ()))

    monkeypatch.setattr(nexacrm.agent, "SupportAgent", FakeAgent)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()
    app.chat_input[0].set_value("Как включить видеоредактор?").run()
    next(item for item in app.button if item.label == "Создать заявку в поддержку").click().run()

    assert app.session_state["ticket_draft"]["reason"] == "insufficient_information"
    assert app.text_input[0].value == "Как включить видеоредактор?"
    assert not list_tickets(config.tickets_path)

    app.form_submit_button[0].click().run()
    tickets = list_tickets(config.tickets_path)
    assert len(tickets) == 1
    assert tickets[0].reason == "insufficient_information"
    assert [item["role"] for item in tickets[0].conversation] == ["user", "assistant"]
