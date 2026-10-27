"""Ручной запрос к AI-оператору и просмотр проверенных источников."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nexacrm.agent import SupportAgent
from nexacrm.config import ConfigError, load_config
from nexacrm.llm import AnswerError
from nexacrm.retrieval import RetrievalError


def main() -> int:
    parser = argparse.ArgumentParser(description="Спросить AI-оператора NexaCRM")
    parser.add_argument("question")
    parser.add_argument("--history-file", type=Path, help="JSON-файл со списком сообщений")
    parser.add_argument("--json", action="store_true", help="Ответ, источники и поисковая выдача в JSON")
    args = parser.parse_args()
    try:
        history = []
        if args.history_file:
            history = json.loads(args.history_file.read_text(encoding="utf-8"))
            if not isinstance(history, list) or any(
                not isinstance(item, dict)
                or item.get("role") not in {"user", "assistant"}
                or not isinstance(item.get("content"), str)
                for item in history
            ):
                raise ValueError("История должна быть списком сообщений с role и content")
        reply = SupportAgent(load_config()).answer(args.question, history)
        if args.json:
            print(json.dumps(reply.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(reply.grounded.answer)
            if reply.grounded.sources:
                print("\nИсточники:")
                for source in reply.grounded.sources:
                    print(
                        f"- {source.document_title} / {source.section_title} "
                        f"({source.source_file}, фрагмент {source.chunk_index}, ID {source.chunk_id})"
                    )
        return 0
    except (AnswerError, RetrievalError, ConfigError, OSError, ValueError) as exc:
        print(f"Ответ не получен: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
