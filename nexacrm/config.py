"""Конфигурация локального приложения без вывода секретов в интерфейс."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    """Неверное значение настройки приложения."""


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом") from exc
    if result <= 0:
        raise ConfigError(f"{name} должно быть больше нуля")
    return result


def _project_path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


def _model_name(name: str, default: str, provider: str) -> str:
    value = os.getenv(name, default)
    if provider == "proxyapi" and "/" not in value:
        return f"openai/{value}"
    return value


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    source_dir: Path
    data_dir: Path
    qdrant_path: Path
    index_manifest_path: Path
    tickets_path: Path
    api_provider: str
    api_key_available: bool
    embedding_model: str
    query_model: str
    answer_model: str
    chunk_target_tokens: int
    chunk_overlap_tokens: int
    semantic_top_k: int
    bm25_top_k: int
    context_top_k: int
    rrf_k: int
    max_user_questions: int
    max_question_chars: int
    max_answer_chars: int
    daily_question_limit: int

    @property
    def index_available(self) -> bool:
        # На итерации 2 манифест создаст ingest.py после проверки полного индекса.
        return self.qdrant_path.is_dir() and self.index_manifest_path.is_file()


def load_config() -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    source_dir = PROJECT_ROOT / "source"
    data_dir = _project_path("DATA_DIR", "data")
    target = _positive_int("CHUNK_TARGET_TOKENS", 500)
    overlap = _positive_int("CHUNK_OVERLAP_TOKENS", 60)
    if overlap >= target:
        raise ConfigError("CHUNK_OVERLAP_TOKENS должно быть меньше CHUNK_TARGET_TOKENS")
    provider = "proxyapi" if os.getenv("PROXY_API_KEY", "").strip() else "openai"
    return AppConfig(
        project_root=PROJECT_ROOT,
        source_dir=source_dir,
        data_dir=data_dir,
        qdrant_path=data_dir / "qdrant",
        index_manifest_path=data_dir / "index_manifest.json",
        tickets_path=_project_path("TICKETS_PATH", "data/tickets.sqlite3"),
        api_provider=provider,
        api_key_available=bool(os.getenv("PROXY_API_KEY" if provider == "proxyapi" else "OPENAI_API_KEY", "").strip()),
        embedding_model=_model_name("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small", provider),
        query_model=_model_name("OPENAI_QUERY_MODEL", "gpt-6-luna", provider),
        answer_model=_model_name("OPENAI_ANSWER_MODEL", "gpt-6.1-sol", provider),
        chunk_target_tokens=target,
        chunk_overlap_tokens=overlap,
        semantic_top_k=_positive_int("SEMANTIC_TOP_K", 8),
        bm25_top_k=_positive_int("BM25_TOP_K", 8),
        context_top_k=_positive_int("CONTEXT_TOP_K", 5),
        rrf_k=_positive_int("RRF_K", 60),
        max_user_questions=_positive_int("MAX_USER_QUESTIONS", 15),
        max_question_chars=_positive_int("MAX_QUESTION_CHARS", 4000),
        max_answer_chars=_positive_int("MAX_ANSWER_CHARS", 2500),
        daily_question_limit=_positive_int("DAILY_QUESTION_LIMIT", 100),
    )

