"""Постоянный локальный индекс Qdrant и безопасная повторная индексация."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from qdrant_client import QdrantClient, models

from nexacrm.chunking import Chunk, chunk_corpus
from nexacrm.config import AppConfig


INDEX_ALIAS = "nexacrm_knowledge"
COLLECTION_PREFIX = "nexacrm_docs_"
EMBEDDING_BATCH_SIZE = 32
UPSERT_BATCH_SIZE = 64
EmbedBatch = Callable[[list[str]], list[list[float]]]


class IndexingError(RuntimeError):
    """Индекс не удалось полностью создать или проверить."""


@dataclass(frozen=True)
class IndexResult:
    document_count: int
    chunk_count: int
    embeddings_count: int
    vector_size: int
    collection_name: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexStatus:
    ready: bool
    message: str
    document_count: int = 0
    chunk_count: int = 0
    stale: bool = False


def source_fingerprint(source_dir: Path) -> str:
    files = sorted(source_dir.glob("*.md"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _active_collection(client: QdrantClient) -> str | None:
    for alias in client.get_aliases().aliases:
        if alias.alias_name == INDEX_ALIAS:
            return alias.collection_name
    return None


def _write_manifest(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix="manifest_", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _validate_vectors(vectors: list[list[float]], expected_count: int, size: int | None) -> int:
    if len(vectors) != expected_count:
        raise IndexingError(
            f"Embeddings API вернул {len(vectors)} векторов вместо {expected_count}"
        )
    if not vectors or not vectors[0]:
        raise IndexingError("Embeddings API вернул пустой вектор")
    current_size = len(vectors[0])
    if size is not None and current_size != size:
        raise IndexingError("Размерность embeddings изменилась между пакетами")
    for vector in vectors:
        if len(vector) != current_size or any(not math.isfinite(value) for value in vector):
            raise IndexingError("Embeddings API вернул вектор неверной размерности или значения")
    return current_size


def _verify_points(client: QdrantClient, collection: str, chunks: list[Chunk]) -> None:
    expected = {chunk.chunk_id: chunk.payload() for chunk in chunks}
    if client.count(collection_name=collection, exact=True).count != len(expected):
        raise IndexingError("Число записей в Qdrant не совпадает с числом подготовленных фрагментов")
    found: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=128,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            point_id = str(point.id)
            if point_id not in expected or point.payload != expected[point_id]:
                raise IndexingError(f"Qdrant вернул неполные или изменённые metadata: {point_id}")
            found.add(point_id)
        if offset is None:
            break
    if found != set(expected):
        raise IndexingError("Не все подготовленные фрагменты удалось прочитать из Qdrant")


def _switch_alias(client: QdrantClient, old_collection: str | None, new_collection: str) -> None:
    operations: list[models.AliasOperations] = []
    if old_collection:
        operations.append(
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=INDEX_ALIAS))
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=new_collection, alias_name=INDEX_ALIAS)
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndexingError(f"Не удалось прочитать манифест индекса: {exc}") from exc
    if not isinstance(data, dict):
        raise IndexingError("Манифест индекса имеет неверный формат")
    return data


def get_index_status(config: AppConfig) -> IndexStatus:
    if not config.index_manifest_path.is_file() or not config.qdrant_path.is_dir():
        return IndexStatus(False, "Индекс знаний ещё не создан.")
    try:
        manifest = _read_manifest(config.index_manifest_path)
        if manifest.get("embedding_model") != config.embedding_model:
            return IndexStatus(False, "Модель embeddings изменилась; выполните индексацию повторно.")
        source_changed = manifest.get("source_fingerprint") != source_fingerprint(config.source_dir)
        settings_changed = (
            manifest.get("chunk_target_tokens") != config.chunk_target_tokens
            or manifest.get("chunk_overlap_tokens") != config.chunk_overlap_tokens
        )
        client = QdrantClient(path=str(config.qdrant_path))
        try:
            collection = _active_collection(client)
            if collection != manifest.get("collection_name"):
                return IndexStatus(False, "Рабочая коллекция не совпадает с манифестом индекса.")
            count = client.count(collection_name=INDEX_ALIAS, exact=True).count
        finally:
            client.close()
        if count != manifest.get("chunk_count") or count == 0:
            return IndexStatus(False, "Индекс пуст или неполон; выполните индексацию повторно.")
        return IndexStatus(
            True,
            (
                "Индекс готов, но документация или параметры разбиения изменились; "
                "выполните индексацию повторно."
                if source_changed or settings_changed else "Индекс готов."
            ),
            document_count=int(manifest["document_count"]),
            chunk_count=count,
            stale=source_changed or settings_changed,
        )
    except Exception:
        return IndexStatus(False, "Индекс недоступен или занят другим процессом.")


def build_index(config: AppConfig, embed_batch: EmbedBatch) -> IndexResult:
    """Построить новый индекс; старый остаётся активным при любой ошибке до публикации."""
    chunks = chunk_corpus(
        config.source_dir,
        config.project_root,
        config.chunk_target_tokens,
        config.chunk_overlap_tokens,
    )
    document_count = len({chunk.source_file for chunk in chunks})
    fingerprint_before = source_fingerprint(config.source_dir)
    vectors: list[list[float]] = []
    vector_size: int | None = None
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        texts = [chunk.embedding_text for chunk in chunks[start : start + EMBEDDING_BATCH_SIZE]]
        batch = embed_batch(texts)
        vector_size = _validate_vectors(batch, len(texts), vector_size)
        vectors.extend(batch)
    if source_fingerprint(config.source_dir) != fingerprint_before:
        raise IndexingError("Документация изменилась во время создания embeddings")
    if vector_size is None or len(vectors) != len(chunks):
        raise IndexingError("Не все embeddings были получены")

    config.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        client = QdrantClient(path=str(config.qdrant_path))
    except RuntimeError as exc:
        raise IndexingError("Qdrant занят. Закройте приложение перед индексацией.") from exc
    staging_name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex[:12]}"
    old_collection = None
    alias_switched = False
    try:
        old_collection = _active_collection(client)
        client.create_collection(
            collection_name=staging_name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
        for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
            batch_chunks = chunks[start : start + UPSERT_BATCH_SIZE]
            batch_vectors = vectors[start : start + UPSERT_BATCH_SIZE]
            points = [
                models.PointStruct(id=chunk.chunk_id, vector=vector, payload=chunk.payload())
                for chunk, vector in zip(batch_chunks, batch_vectors, strict=True)
            ]
            client.upsert(collection_name=staging_name, points=points, wait=True)
        _verify_points(client, staging_name, chunks)
        if source_fingerprint(config.source_dir) != fingerprint_before:
            raise IndexingError("Документация изменилась во время записи индекса")
        manifest: dict[str, object] = {
            "collection_name": staging_name,
            "document_count": document_count,
            "chunk_count": len(chunks),
            "embeddings_count": len(vectors),
            "vector_size": vector_size,
            "embedding_model": config.embedding_model,
            "source_fingerprint": fingerprint_before,
            "chunk_target_tokens": config.chunk_target_tokens,
            "chunk_overlap_tokens": config.chunk_overlap_tokens,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _switch_alias(client, old_collection, staging_name)
        alias_switched = True
        _write_manifest(config.index_manifest_path, manifest)
        warnings: list[str] = []
        for collection in client.get_collections().collections:
            name = collection.name
            if name.startswith(COLLECTION_PREFIX) and name != staging_name:
                try:
                    client.delete_collection(name)
                except Exception:
                    warnings.append(f"Не удалось удалить старую коллекцию {name}")
        return IndexResult(
            document_count=document_count,
            chunk_count=len(chunks),
            embeddings_count=len(vectors),
            vector_size=vector_size,
            collection_name=staging_name,
            warnings=tuple(warnings),
        )
    except Exception:
        if alias_switched:
            try:
                operations: list[models.AliasOperations] = [
                    models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=INDEX_ALIAS))
                ]
                if old_collection:
                    operations.append(
                        models.CreateAliasOperation(
                            create_alias=models.CreateAlias(
                                collection_name=old_collection, alias_name=INDEX_ALIAS
                            )
                        )
                    )
                client.update_collection_aliases(change_aliases_operations=operations)
            except Exception:
                pass
        try:
            if client.collection_exists(staging_name) and _active_collection(client) != staging_name:
                client.delete_collection(staging_name)
        except Exception:
            pass
        raise
    finally:
        client.close()
