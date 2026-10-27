"""Ручная индексация документации: python ingest.py."""

from __future__ import annotations

import argparse
import sys

from openai import OpenAIError

from nexacrm.api_client import create_api_client
from nexacrm.chunking import chunk_corpus, token_count
from nexacrm.config import ConfigError, load_config
from nexacrm.corpus import CorpusError
from nexacrm.knowledge_base import IndexingError, build_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Индексировать Markdown-документацию NexaCRM")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверить разбиение без OpenAI API и записи в Qdrant",
    )
    args = parser.parse_args()
    try:
        config = load_config()
        if args.dry_run:
            chunks = chunk_corpus(
                config.source_dir,
                config.project_root,
                config.chunk_target_tokens,
                config.chunk_overlap_tokens,
            )
            print(f"Документов: {len({chunk.source_file for chunk in chunks})}")
            print(f"Фрагментов: {len(chunks)}")
            print(f"Максимальный размер embedding-входа: {max(token_count(chunk.embedding_text) for chunk in chunks)} токенов")
            print("Embeddings не запрашивались; индекс не изменён.")
            return 0
        if not config.api_key_available:
            raise IndexingError("PROXY_API_KEY или OPENAI_API_KEY не задан в .env или окружении")
        client = create_api_client(config, timeout=60.0)

        def embed_batch(texts: list[str]) -> list[list[float]]:
            try:
                response = client.embeddings.create(
                    model=config.embedding_model,
                    input=texts,
                    encoding_format="float",
                )
            except OpenAIError as exc:
                status = getattr(exc, "status_code", None)
                detail = f"HTTP {status}" if status else type(exc).__name__
                raise IndexingError(f"Ошибка Embeddings API ({detail})") from None
            ordered: list[list[float] | None] = [None] * len(texts)
            for item in response.data:
                if not 0 <= item.index < len(texts) or ordered[item.index] is not None:
                    raise IndexingError("Embeddings API вернул неверные индексы результатов")
                ordered[item.index] = item.embedding
            if any(vector is None for vector in ordered):
                raise IndexingError("Embeddings API вернул не все векторы")
            return [vector for vector in ordered if vector is not None]

        result = build_index(config, embed_batch)
        print(f"Документов: {result.document_count}")
        print(f"Фрагментов: {result.chunk_count}")
        print(f"Успешно сохранённых embeddings: {result.embeddings_count}")
        print(f"Размерность вектора: {result.vector_size}")
        print("Индекс создан и проверен.")
        for warning in result.warnings:
            print(f"Предупреждение: {warning}", file=sys.stderr)
        return 0
    except (ConfigError, CorpusError, IndexingError) as exc:
        print(f"Индексация не выполнена: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Индексация не выполнена: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
