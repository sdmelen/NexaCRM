"""Dense/BM25 поиск по сохранённым chunks и объединение рангов RRF."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi

from nexacrm.config import AppConfig
from nexacrm.knowledge_base import INDEX_ALIAS, get_index_status


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
EmbedQuery = Callable[[str], list[float]]
RewriteQuery = Callable[[str, list[dict[str, str]]], str]


class RetrievalError(RuntimeError):
    """Поисковый индекс или запрос не удалось обработать."""


def tokenize(text: str) -> list[str]:
    """Общие правила BM25 для русских слов, латиницы и чисел."""
    return TOKEN_RE.findall(text.casefold().replace("ё", "е"))


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source_file: str
    document_name: str
    document_title: str
    section_title: str
    section_path: tuple[str, ...]
    chunk_index: int
    front_matter: dict[str, Any]
    text: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IndexedChunk:
        required = {
            "chunk_id", "source_file", "document_name", "document_title", "section_title",
            "section_path", "chunk_index", "front_matter", "text",
        }
        if not required.issubset(payload):
            raise RetrievalError(f"У фрагмента отсутствуют поля: {sorted(required - set(payload))}")
        return cls(
            chunk_id=str(payload["chunk_id"]),
            source_file=str(payload["source_file"]),
            document_name=str(payload["document_name"]),
            document_title=str(payload["document_title"]),
            section_title=str(payload["section_title"]),
            section_path=tuple(payload["section_path"]),
            chunk_index=int(payload["chunk_index"]),
            front_matter=dict(payload["front_matter"]),
            text=str(payload["text"]),
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_file": self.source_file,
            "document_name": self.document_name,
            "document_title": self.document_title,
            "section_title": self.section_title,
            "section_path": list(self.section_path),
            "chunk_index": self.chunk_index,
            "front_matter": self.front_matter,
        }


@dataclass(frozen=True)
class SearchHit:
    chunk: IndexedChunk
    rank: int
    score: float


@dataclass(frozen=True)
class FusedHit:
    chunk: IndexedChunk
    rank: int
    score: float
    semantic_rank: int | None
    bm25_rank: int | None


@dataclass(frozen=True)
class RetrievalResult:
    original_question: str
    search_query: str
    rewrite_fallback: bool
    semantic: tuple[SearchHit, ...]
    bm25: tuple[SearchHit, ...]
    fused: tuple[FusedHit, ...]
    context: tuple[FusedHit, ...]

    def diagnostic(self, include_text: bool = True) -> dict[str, Any]:
        def entry(hit: SearchHit | FusedHit) -> dict[str, Any]:
            data: dict[str, Any] = {
                "rank": hit.rank,
                "score": hit.score,
                "metadata": hit.chunk.metadata(),
            }
            if isinstance(hit, FusedHit):
                data.update(semantic_rank=hit.semantic_rank, bm25_rank=hit.bm25_rank)
            if include_text:
                data["text"] = hit.chunk.text
            return data

        return {
            "original_question": self.original_question,
            "search_query": self.search_query,
            "rewrite_fallback": self.rewrite_fallback,
            "semantic": [entry(hit) for hit in self.semantic],
            "bm25": [entry(hit) for hit in self.bm25],
            "fused": [entry(hit) for hit in self.fused],
            "context": [entry(hit) for hit in self.context],
        }


def reciprocal_rank_fusion(
    semantic: Iterable[SearchHit], bm25: Iterable[SearchHit], k: int = 60
) -> tuple[FusedHit, ...]:
    if k <= 0:
        raise ValueError("RRF_K должно быть больше нуля")
    combined: dict[str, dict[str, Any]] = {}
    for method, hits in (("semantic", semantic), ("bm25", bm25)):
        for hit in hits:
            item = combined.setdefault(
                hit.chunk.chunk_id,
                {"chunk": hit.chunk, "score": 0.0, "semantic_rank": None, "bm25_rank": None},
            )
            rank_key = f"{method}_rank"
            if item[rank_key] is None or hit.rank < item[rank_key]:
                if item[rank_key] is not None:
                    item["score"] -= 1 / (k + item[rank_key])
                item["score"] += 1 / (k + hit.rank)
                item[rank_key] = hit.rank
    ordered = sorted(
        combined.values(),
        key=lambda item: (
            -item["score"],
            min(rank for rank in (item["semantic_rank"], item["bm25_rank"]) if rank is not None),
            item["chunk"].chunk_id,
        ),
    )
    return tuple(
        FusedHit(rank=index, **item) for index, item in enumerate(ordered, start=1)
    )


class RetrievalEngine:
    """Один открытый локальный Qdrant client и BM25 из его payload."""

    def __init__(self, config: AppConfig):
        status = get_index_status(config)
        if not status.ready:
            raise RetrievalError(status.message)
        self.config = config
        self.index_status = status
        self.client = QdrantClient(path=str(config.qdrant_path))
        try:
            chunks: list[IndexedChunk] = []
            offset = None
            while True:
                points, offset = self.client.scroll(
                    collection_name=INDEX_ALIAS, limit=128, offset=offset,
                    with_payload=True, with_vectors=False,
                )
                for point in points:
                    payload = point.payload or {}
                    chunk = IndexedChunk.from_payload(payload)
                    if chunk.chunk_id != str(point.id):
                        raise RetrievalError("ID фрагмента не совпадает с ID записи Qdrant")
                    chunks.append(chunk)
                if offset is None:
                    break
            if len(chunks) != status.chunk_count:
                raise RetrievalError("Количество фрагментов изменилось при загрузке BM25")
            self.chunks = tuple(sorted(chunks, key=lambda chunk: chunk.chunk_id))
            self.by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
            if len(self.by_id) != len(self.chunks):
                raise RetrievalError("В индексе обнаружены повторяющиеся ID")
            documents = [tokenize(chunk.text) for chunk in self.chunks]
            self.bm25_index = BM25Okapi(documents)
        except Exception:
            self.client.close()
            raise

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> RetrievalEngine:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def bm25_search(self, query: str) -> tuple[SearchHit, ...]:
        tokens = tokenize(query)
        if not tokens:
            return ()
        scores = self.bm25_index.get_scores(tokens)
        ranked = sorted(
            ((float(score), chunk) for score, chunk in zip(scores, self.chunks, strict=True) if score > 0),
            key=lambda item: (-item[0], item[1].chunk_id),
        )[: self.config.bm25_top_k]
        return tuple(
            SearchHit(chunk=chunk, rank=rank, score=score)
            for rank, (score, chunk) in enumerate(ranked, start=1)
        )

    def semantic_search(self, vector: list[float]) -> tuple[SearchHit, ...]:
        if not vector or any(not math.isfinite(value) for value in vector):
            raise RetrievalError("Embedding запроса пуст или содержит некорректные значения")
        results = self.client.query_points(
            collection_name=INDEX_ALIAS, query=vector,
            limit=self.config.semantic_top_k, with_payload=True, with_vectors=False,
        ).points
        hits: list[SearchHit] = []
        for rank, point in enumerate(results, start=1):
            chunk = self.by_id.get(str(point.id))
            if chunk is None:
                raise RetrievalError(f"Неизвестный ID в semantic search: {point.id}")
            hits.append(SearchHit(chunk=chunk, rank=rank, score=float(point.score)))
        return tuple(hits)

    def search(
        self,
        question: str,
        history: list[dict[str, str]],
        embed_query: EmbedQuery,
        rewrite_query: RewriteQuery | None = None,
    ) -> RetrievalResult:
        original = question.strip()
        if not original:
            raise RetrievalError("Пустой поисковый вопрос")
        if len(original) > self.config.max_question_chars:
            raise RetrievalError("Вопрос превышает допустимую длину")
        query = original
        fallback = False
        if history and rewrite_query is not None:
            try:
                candidate = rewrite_query(original, history).strip()
                if not candidate or len(candidate) > self.config.max_question_chars:
                    raise ValueError("Пустой или слишком длинный поисковый запрос")
                query = candidate
            except Exception:
                fallback = True
        bm25 = self.bm25_search(query)
        semantic = self.semantic_search(embed_query(query))
        fused = reciprocal_rank_fusion(semantic, bm25, self.config.rrf_k)
        return RetrievalResult(
            original_question=original,
            search_query=query,
            rewrite_fallback=fallback,
            semantic=semantic,
            bm25=bm25,
            fused=fused,
            context=fused[: self.config.context_top_k],
        )
