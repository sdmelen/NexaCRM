"""Просмотр заявок; процесс доступен только через закрытый маршрут Caddy."""

from __future__ import annotations

import streamlit as st

from nexacrm.config import ConfigError, load_config
from nexacrm.tickets import TicketError, list_tickets


REASON_LABELS = {
    "user_request": "Запрос пользователя",
    "insufficient_information": "Недостаточно сведений в документации",
    "session_limit": "Завершение диалога после лимита вопросов",
}

st.set_page_config(page_title="Заявки NexaCRM", page_icon="📋", layout="wide")
st.title("Заявки")

try:
    config = load_config()
    tickets = list_tickets(config.tickets_path)
except (ConfigError, TicketError):
    st.error("Не удалось открыть список заявок.")
    st.stop()

if not tickets:
    st.info("Пока нет сохранённых заявок.")
else:
    st.dataframe(
        [{"№": ticket.id, "Создана (UTC)": ticket.created_at,
          "Тема": ticket.title, "Статус": ticket.status} for ticket in tickets],
        hide_index=True,
        width="stretch",
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
