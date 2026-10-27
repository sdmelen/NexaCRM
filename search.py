"""Ручной просмотр semantic/BM25/RRF результатов по одному вопросу."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nexacrm.config import ConfigError, load_config
from nexacrm.retrieval import RetrievalEngine, RetrievalError, RetrievalResult
from nexacrm.retrieval_openai import OpenAIRetrievalProvider


def _print_result(result: RetrievalResult) -> None:
    print(f"Вопрос: {result.original_question}")
    print(f"Поисковый запрос: {result.search_query}")
    if result.rewrite_fallback:
        print("Преобразование запроса не удалось; использован исходный вопрос.")
    for title, hits in (
        ("Semantic", result.semantic), ("BM25", result.bm25), ("RRF", result.fused),
    ):
        print(f"\n{title}:")
        for hit in hits:
            chunk = hit.chunk
            print(
                f"  {hit.rank}. score={hit.score:.5f} id={chunk.chunk_id} "
                f"{chunk.source_file} / {' > '.join(chunk.section_path)}"
            )
            if title == "RRF":
                print(f"     semantic_rank={hit.semantic_rank}, bm25_rank={hit.bm25_rank}")
    print("\nКонтекст для ответа:")
    for hit in result.context:
        print(f"  {hit.rank}. {hit.chunk.chunk_id} — {hit.chunk.document_title} / {hit.chunk.section_title}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Найти фрагменты документации NexaCRM")
    parser.add_argument("question", help="Вопрос на русском языке")
    parser.add_argument("--history-file", type=Path, help="JSON-файл с историей [{role, content}, ...]")
    parser.add_argument("--json", action="store_true", help="Вывести текст и metadata всех результатов в JSON")
    args = parser.parse_args()
    try:
        config = load_config()
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
        provider = OpenAIRetrievalProvider(config)
        with RetrievalEngine(config) as engine:
            if engine.index_status.stale:
                print(f"Предупреждение: {engine.index_status.message}", file=sys.stderr)
            result = engine.search(args.question, history, provider.embed, provider.rewrite)
        if args.json:
            print(json.dumps(result.diagnostic(), ensure_ascii=False, indent=2))
        else:
            _print_result(result)
        return 0
    except (ConfigError, RetrievalError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Поиск не выполнен: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
