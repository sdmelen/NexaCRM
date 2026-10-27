"""Локальный AI-оператор NexaCRM с ответами по проверенным источникам."""

from __future__ import annotations

import streamlit as st
from uuid import uuid4

from nexacrm.agent import SupportAgent
from nexacrm.config import ConfigError, load_config
from nexacrm.corpus import CorpusError, scan_corpus
from nexacrm.knowledge_base import get_index_status
from nexacrm.llm import AnswerError
from nexacrm.retrieval import RetrievalError
from nexacrm.tickets import TicketError, create_ticket, list_tickets


def show_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander(f"Источники ({len(sources)})"):
        for source in sources:
            with st.container(border=True):
                st.markdown(f"**{source['document_title']}** — {source['section_title']}")
                st.caption(
                    f"{source['source_file']} · фрагмент {source['chunk_index']} "
                    f"· ID {source['chunk_id']}"
                )
                if source["front_matter"]:
                    st.json(source["front_matter"], expanded=False)
                st.text(source["text"])


REASON_LABELS = {
    "user_request": "Запрос пользователя",
    "insufficient_information": "Недостаточно сведений в документации",
    "session_limit": "Завершение диалога после лимита вопросов",
}


def question_count(messages: list[dict]) -> int:
    return sum(message["role"] == "user" for message in messages)


def open_ticket(reason: str, question: str = "") -> None:
    """Подготовить черновик без записи в базу до подтверждения."""
    st.session_state.ticket_draft = {
        "request_id": uuid4().hex,
        "reason": reason,
        "title": question[:120] or "Вопрос по NexaCRM",
        "description": question,
    }
    st.session_state.ticket_saved_id = None
    if reason == "session_limit":
        st.session_state.session_limit_offered = True


st.set_page_config(page_title="NexaCRM Support AI", page_icon="💬", layout="wide")

try:
    config = load_config()
except ConfigError as exc:
    st.error(f"Ошибка конфигурации: {exc}")
    st.stop()

with st.sidebar:
    st.header("NexaCRM Support AI")
    page = st.radio("Раздел", ["AI-оператор", "Заявки"])
    st.caption("Локальный демонстрационный MVP")

for key, default in (
    ("messages", []), ("pending_question", None), ("new_dialog_notice", False),
    ("ticket_draft", None), ("ticket_saved_id", None),
    ("session_limit_offered", False),
):
    st.session_state.setdefault(key, default)

if page == "AI-оператор":
    st.title("AI-оператор NexaCRM")
    st.caption("Ответы по документации и заявки в поддержку")
    index_status = get_index_status(config)

    try:
        documents = scan_corpus(config.source_dir)
    except CorpusError as exc:
        documents = []
        st.error(str(exc))

    left, middle, right = st.columns(3)
    left.metric("Документов", len(documents))
    middle.metric("API-ключ", "добавлен" if config.api_key_available else "не задан")
    right.metric("Индекс знаний", "готов" if index_status.ready else "не готов")

    if not config.api_key_available:
        st.info("Для ответов добавьте OPENAI_API_KEY в файл .env. Значение ключа здесь не отображается.")
    if not index_status.ready:
        st.warning(f"{index_status.message} Для создания выполните `python ingest.py`.")
    elif index_status.stale:
        st.warning(index_status.message)
    else:
        st.success(
            f"Индекс содержит {index_status.document_count} документов и "
            f"{index_status.chunk_count} фрагментов."
        )

    st.divider()
    st.subheader("Диалог")
    used_questions = question_count(st.session_state.messages)
    st.caption(f"Вопросов в диалоге: {used_questions} из {config.max_user_questions}")

    if st.session_state.new_dialog_notice:
        st.success("Новый диалог начат. Можно задать вопрос.")
        st.session_state.new_dialog_notice = False
    if st.session_state.ticket_saved_id is not None:
        st.success(f"Заявка №{st.session_state.ticket_saved_id} сохранена локально.")

    if not st.session_state.messages:
        st.chat_message("assistant").write(
            "Привет! Задайте вопрос по документации NexaCRM. "
            "Если сведений не хватит, я сообщу об этом."
        )
    for message_index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message["role"] == "assistant":
                show_sources(message.get("sources", []))
                if not message.get("is_answerable", True):
                    if st.button("Создать заявку в поддержку", key=f"ticket_{message['message_id']}"):
                        earlier_question = next(
                            (item["content"] for item in reversed(
                                st.session_state.messages[:message_index]
                            ) if item["role"] == "user"),
                            "",
                        )
                        open_ticket("insufficient_information", earlier_question)
                        st.rerun()

    if used_questions >= config.max_user_questions:
        st.info("Лимит вопросов достигнут. Диалог завершён; можно сохранить заявку или начать новый.")
        if not st.session_state.session_limit_offered:
            open_ticket("session_limit", next(
                (item["content"] for item in reversed(st.session_state.messages)
                 if item["role"] == "user"), ""
            ))

    can_ask = (
        config.api_key_available and index_status.ready
        and used_questions < config.max_user_questions
    )
    if st.button("Новый диалог"):
        st.session_state.messages = []
        st.session_state.pending_question = None
        st.session_state.ticket_draft = None
        st.session_state.ticket_saved_id = None
        st.session_state.session_limit_offered = False
        st.session_state.new_dialog_notice = True
        st.rerun()
    if st.button("Создать заявку", key="ticket_explicit"):
        last_question = next(
            (item["content"] for item in reversed(st.session_state.messages)
             if item["role"] == "user"), ""
        )
        open_ticket("user_request", last_question)
        st.rerun()

    draft = st.session_state.ticket_draft
    if draft:
        st.subheader("Черновик заявки")
        st.caption(f"Причина: {REASON_LABELS[draft['reason']]}")
        st.warning(
            "Не вводите пароли, API-ключи, резервные коды и реальные персональные данные. "
            "История этого диалога будет сохранена вместе с заявкой."
        )
        with st.form(f"ticket_form_{draft['request_id']}"):
            title = st.text_input("Тема обращения", value=draft["title"], max_chars=200)
            description = st.text_area(
                "Описание проблемы", value=draft["description"], max_chars=5000
            )
            confirmed = st.form_submit_button("Подтвердить и сохранить заявку")
        if confirmed:
            try:
                ticket = create_ticket(
                    config.tickets_path, request_id=draft["request_id"],
                    title=title, description=description, reason=draft["reason"],
                    conversation=st.session_state.messages,
                )
            except TicketError as exc:
                st.error(str(exc))
            else:
                st.session_state.ticket_draft = None
                st.session_state.ticket_saved_id = ticket.id
                st.rerun()
        if st.button("Отменить черновик"):
            st.session_state.ticket_draft = None
            st.rerun()

    retry = False
    if st.session_state.pending_question:
        st.warning("Предыдущий вопрос не обработан. Его можно отправить повторно.")
        retry = st.button("Повторить вопрос")
    prompt = st.chat_input(
        "Задайте вопрос по документации NexaCRM",
        disabled=not can_ask,
        max_chars=config.max_question_chars,
    )
    if prompt:
        st.session_state.pending_question = prompt
    if (prompt or retry) and can_ask:
        question = st.session_state.pending_question
        if not question or len(question.strip()) > config.max_question_chars:
            st.session_state.pending_question = None
            st.error(f"Вопрос должен содержать не более {config.max_question_chars} символов.")
            st.stop()
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in st.session_state.messages
        ]
        try:
            with st.spinner("Ищу подтверждение в документации..."):
                reply = SupportAgent(config).answer(question, history)
            st.session_state.messages.append({"role": "user", "content": question})
            st.session_state.messages.append({
                "role": "assistant",
                "content": reply.grounded.answer,
                "sources": [source.as_dict() for source in reply.grounded.sources],
                "is_answerable": reply.grounded.is_answerable,
                "message_id": len(st.session_state.messages) + 1,
            })
            st.session_state.pending_question = None
            if question_count(st.session_state.messages) >= config.max_user_questions:
                open_ticket("session_limit", question)
            st.rerun()
        except (AnswerError, RetrievalError):
            st.error("Не удалось подготовить ответ. Попробуйте повторить вопрос.")
        except Exception:
            st.error("Поиск временно недоступен. Попробуйте повторить вопрос.")

    if documents:
        with st.expander(f"Исходные документы ({len(documents)})"):
            st.dataframe(
                [
                    {
                        "Файл": doc.path.name,
                        "Название": doc.title,
                        "Категория": doc.category,
                        "Обновлено": doc.updated,
                        "Заголовков": len(doc.headings),
                        "Таблиц": doc.table_count,
                        "Блоков кода": doc.code_block_count,
                    }
                    for doc in documents
                ],
                hide_index=True,
                width="stretch",
            )

else:
    st.title("Заявки")
    st.caption("Обращения сохраняются в SQLite на этом компьютере после подтверждения.")
    try:
        tickets = list_tickets(config.tickets_path)
    except TicketError as exc:
        st.error(str(exc))
    else:
        if not tickets:
            st.info("Пока нет сохранённых заявок.")
        else:
            st.dataframe(
                [{"№": ticket.id, "Создана (UTC)": ticket.created_at,
                  "Тема": ticket.title, "Статус": ticket.status} for ticket in tickets],
                hide_index=True, width="stretch",
            )
            selected_id = st.selectbox(
                "Открыть заявку", [ticket.id for ticket in tickets],
                format_func=lambda ticket_id: next(
                    f"№{ticket.id} — {ticket.title}" for ticket in tickets
                    if ticket.id == ticket_id
                ),
            )
            selected = next(ticket for ticket in tickets if ticket.id == selected_id)
            st.subheader(selected.title)
            st.caption(
                f"№{selected.id} · {selected.created_at} UTC · статус: {selected.status} · "
                f"{REASON_LABELS[selected.reason]}"
            )
            st.write(selected.description)
            if selected.conversation:
                st.subheader("История переписки")
                for message in selected.conversation:
                    with st.chat_message(message["role"]):
                        st.write(message["content"])
