from dataclasses import replace
from pathlib import Path

import pytest

from nexacrm.config import load_config
from nexacrm.knowledge_base import build_index
from nexacrm.retrieval import (
    IndexedChunk, RetrievalEngine, RetrievalError, SearchHit,
    reciprocal_rank_fusion, tokenize,
)


def _chunk(chunk_id: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, source_file=f"source/{chunk_id}.md", document_name=f"{chunk_id}.md",
        document_title=chunk_id, section_title=chunk_id, section_path=(chunk_id,),
        chunk_index=1, front_matter={}, text=chunk_id,
    )


def test_tokenization_handles_russian_punctuation_and_yo() -> None:
    assert tokenize("Ёлка, ёлка! CRM-429 / user_id") == [
        "елка", "елка", "crm", "429", "user", "id"
    ]


def test_rrf_deduplicates_and_combines_independent_ranks() -> None:
    a, b, c = (_chunk(name) for name in "abc")
    semantic = (SearchHit(a, 1, 0.9), SearchHit(b, 2, 0.8))
    bm25 = (SearchHit(b, 1, 5.0), SearchHit(c, 2, 4.0), SearchHit(b, 3, 1.0))
    fused = reciprocal_rank_fusion(semantic, bm25, k=60)
    assert [hit.chunk.chunk_id for hit in fused] == ["b", "a", "c"]
    assert fused[0].semantic_rank == 2 and fused[0].bm25_rank == 1
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert len(fused) == 3


def test_search_uses_qdrant_payload_and_rewrite_fallback(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "invites.md").write_text(
        "---\ntitle: Приглашения\ncategory: Доступ\n---\n"
        "# Приглашения\n\nСсылка приглашения действует семь дней.\n", encoding="utf-8"
    )
    (source / "exports.md").write_text(
        "# Экспорт\n\nСсылка экспорта действует сутки.\n", encoding="utf-8"
    )
    (source / "deals.md").write_text(
        "# Сделки\n\nВероятность этапа определяет прогноз.\n", encoding="utf-8"
    )
    data = tmp_path / "data"
    config = replace(
        load_config(), project_root=tmp_path, source_dir=source, data_dir=data,
        qdrant_path=data / "qdrant", index_manifest_path=data / "index_manifest.json",
        chunk_target_tokens=100, chunk_overlap_tokens=10,
    )

    def document_vectors(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "приглашения" in text.casefold() else [0.0, 1.0] for text in texts]

    build_index(config, document_vectors)
    with RetrievalEngine(config) as engine:
        result = engine.search(
            "А сколько она действует?",
            [{"role": "user", "content": "Как пригласить коллегу?"}],
            lambda query: [1.0, 0.0],
            lambda question, history: "Сколько действует ссылка приглашения?",
        )
        assert result.original_question == "А сколько она действует?"
        assert result.search_query == "Сколько действует ссылка приглашения?"
        assert result.semantic[0].chunk.document_title == "Приглашения"
        assert result.bm25[0].chunk.document_title == "Приглашения"
        assert result.context[0].chunk.front_matter["category"] == "Доступ"
        assert result.diagnostic()["context"][0]["metadata"]["source_file"] == "source/invites.md"

        def fail_rewrite(_question, _history):
            raise RuntimeError("offline")

        fallback = engine.search("Сколько действует ссылка?", [{"role": "user", "content": "test"}],
                                 lambda query: [1.0, 0.0], fail_rewrite)
        assert fallback.rewrite_fallback
        assert fallback.search_query == "Сколько действует ссылка?"
        with pytest.raises(RetrievalError, match="Пустой"):
            engine.search("  ", [], lambda query: [1.0, 0.0])
