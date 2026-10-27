from dataclasses import replace
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from nexacrm.config import load_config
from nexacrm.knowledge_base import INDEX_ALIAS, build_index, get_index_status


def _config(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.md").write_text(
        "---\ntitle: Проверочный документ\ncategory: Тест\nupdated: 2026-10-16\n---\n"
        "# Заголовок\n\n## Раздел\n\nПроверочный текст для индекса.\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    return replace(
        load_config(),
        project_root=tmp_path,
        source_dir=source,
        data_dir=data,
        qdrant_path=data / "qdrant",
        index_manifest_path=data / "index_manifest.json",
        chunk_target_tokens=100,
        chunk_overlap_tokens=10,
    )


def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    return [[float(len(text)), 1.0, 0.5] for text in texts]


def test_persists_metadata_and_replaces_index_without_duplicates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = build_index(config, _fake_embeddings)
    assert first.document_count == 1
    assert first.embeddings_count == first.chunk_count
    assert get_index_status(config).ready
    assert get_index_status(replace(config, chunk_target_tokens=110)).stale

    client = QdrantClient(path=str(config.qdrant_path))
    try:
        points, _ = client.scroll(
            collection_name=INDEX_ALIAS, with_payload=True, with_vectors=True, limit=100
        )
        assert len(points) == first.chunk_count
        assert points[0].payload["source_file"] == "source/guide.md"
        assert points[0].payload["document_title"] == "Проверочный документ"
        assert points[0].payload["section_title"]
        assert points[0].payload["front_matter"]["category"] == "Тест"
        assert len(points[0].vector) == 3
    finally:
        client.close()

    second = build_index(config, _fake_embeddings)
    assert second.collection_name != first.collection_name
    client = QdrantClient(path=str(config.qdrant_path))
    try:
        assert [item.name for item in client.get_collections().collections] == [
            second.collection_name
        ]
        assert client.count(collection_name=INDEX_ALIAS, exact=True).count == first.chunk_count
    finally:
        client.close()


def test_failed_reindex_keeps_previous_collection(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    first = build_index(config, _fake_embeddings)
    (config.source_dir / "guide.md").write_text(
        "# Изменённый документ\n\nНовая версия документа.\n", encoding="utf-8"
    )

    def failing_embeddings(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Проверочный сбой embeddings")

    with pytest.raises(RuntimeError, match="Проверочный сбой"):
        build_index(config, failing_embeddings)
    status = get_index_status(config)
    assert status.ready and status.stale

    original_upsert = QdrantClient.upsert

    def failing_upsert(self, *args, **kwargs):
        if kwargs.get("collection_name", "").startswith("nexacrm_docs_"):
            raise RuntimeError("Проверочный сбой записи")
        return original_upsert(self, *args, **kwargs)

    monkeypatch.setattr(QdrantClient, "upsert", failing_upsert)
    with pytest.raises(RuntimeError, match="Проверочный сбой записи"):
        build_index(config, _fake_embeddings)

    client = QdrantClient(path=str(config.qdrant_path))
    try:
        aliases = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
        assert aliases[INDEX_ALIAS] == first.collection_name
        assert [item.name for item in client.get_collections().collections] == [
            first.collection_name
        ]
    finally:
        client.close()
