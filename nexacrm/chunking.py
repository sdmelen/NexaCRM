"""Разбиение Markdown по разделам и блокам с сохранением источников."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import tiktoken

from nexacrm.corpus import FENCE_RE, HEADING_RE, CorpusError, _split_front_matter


EMBEDDING_TOKEN_LIMIT = 8192
_ENCODING = tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_file: str
    document_name: str
    document_title: str
    section_title: str
    section_path: tuple[str, ...]
    chunk_index: int
    front_matter: dict[str, Any]
    text: str

    @property
    def embedding_text(self) -> str:
        path = " → ".join(self.section_path)
        return f"Документ: {self.document_title}\nРаздел: {path}\n\n{self.text}"

    def payload(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_file": self.source_file,
            "document_name": self.document_name,
            "document_title": self.document_title,
            "section_title": self.section_title,
            "section_path": list(self.section_path),
            "chunk_index": self.chunk_index,
            "front_matter": self.front_matter,
            "text": self.text,
        }


@dataclass(frozen=True)
class _Section:
    title: str
    path: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class _Block:
    kind: str
    text: str


def token_count(text: str) -> int:
    return len(_ENCODING.encode(text))


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise CorpusError(f"Неподдерживаемое значение YAML: {type(value).__name__}")


def _sections(body: list[str], document_title: str) -> list[_Section]:
    sections: list[_Section] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_title = document_title
    current_path: tuple[str, ...] = (document_title,)
    in_fence = False
    fence_char = ""
    fence_length = 0

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(_Section(current_title, current_path, text))

    for line in body:
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                in_fence = False
        heading = None if in_fence or fence else HEADING_RE.match(line)
        if heading:
            flush()
            current_lines = [line]
            level, title = len(heading.group(1)), heading.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current_title = title
            current_path = tuple(item[1] for item in heading_stack)
        else:
            current_lines.append(line)
    if in_fence:
        raise CorpusError("Незакрытый fenced code block")
    flush()
    return sections


def _blocks(text: str) -> list[_Block]:
    result: list[_Block] = []
    lines: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0

    def flush() -> None:
        block = "\n".join(lines).strip()
        if not block:
            return
        nonempty = [line.strip() for line in block.splitlines() if line.strip()]
        if nonempty[0].startswith((chr(96) * 3, "~~~")):
            kind = "code"
        elif all(line.startswith("|") and line.endswith("|") for line in nonempty):
            kind = "table"
        elif len(nonempty) == 1 and HEADING_RE.match(nonempty[0]):
            kind = "heading"
        else:
            kind = "prose"
        result.append(_Block(kind, block))

    for line in text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                in_fence = False
        if not line.strip() and not in_fence:
            flush()
            lines = []
        else:
            lines.append(line)
    flush()
    return result


def _split_long_prose(text: str, target_tokens: int) -> list[str]:
    pieces: list[str] = []
    remaining = text
    while token_count(remaining) > target_tokens:
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if token_count(remaining[:middle]) <= target_tokens:
                low = middle
            else:
                high = middle - 1
        boundary = low
        whitespace = [remaining.rfind(char, max(1, boundary // 2), boundary) for char in " \n\t"]
        cut = max(whitespace)
        if cut > 0:
            boundary = cut + 1
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:]
    if remaining.strip():
        pieces.append(remaining.strip())
    return pieces


def _overlap_from(blocks: list[_Block], overlap_tokens: int) -> _Block | None:
    for block in reversed(blocks):
        if block.kind != "prose":
            continue
        text = block.text
        if token_count(text) <= overlap_tokens:
            return block
        low, high = 0, len(text) - 1
        while low < high:
            middle = (low + high) // 2
            if token_count(text[middle:]) <= overlap_tokens:
                high = middle
            else:
                low = middle + 1
        suffix = text[low:].lstrip()
        if suffix:
            return _Block("prose", suffix)
    return None


def _pack(section: _Section, target_tokens: int, overlap_tokens: int) -> list[str]:
    blocks: list[_Block] = []
    for block in _blocks(section.text):
        if block.kind == "prose" and token_count(block.text) > target_tokens:
            blocks.extend(_Block("prose", piece) for piece in _split_long_prose(block.text, target_tokens))
        else:
            blocks.append(block)
    chunks: list[str] = []
    current: list[_Block] = []
    for block in blocks:
        candidate = "\n\n".join(item.text for item in [*current, block])
        if current and token_count(candidate) > target_tokens:
            chunks.append("\n\n".join(item.text for item in current))
            overlap = _overlap_from(current, overlap_tokens)
            current = [overlap, block] if overlap and token_count(
                f"{overlap.text}\n\n{block.text}"
            ) <= target_tokens + overlap_tokens else [block]
        else:
            current.append(block)
    if current:
        chunks.append("\n\n".join(item.text for item in current))
    return chunks


def chunk_document(
    path: Path, project_root: Path, target_tokens: int = 500, overlap_tokens: int = 60
) -> list[Chunk]:
    if target_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("Неверные параметры chunking")
    try:
        relative = path.resolve().relative_to(project_root.resolve()).as_posix()
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise CorpusError(f"{path.name}: не удалось прочитать файл внутри проекта: {exc}") from exc
    raw_front_matter, body = _split_front_matter(text, path)
    front_matter = _json_value(raw_front_matter)
    h1 = next(
        (match.group(2).strip() for line in body if (match := HEADING_RE.match(line)) and len(match.group(1)) == 1),
        path.stem,
    )
    document_title = str(front_matter.get("title") or h1)
    document_chunks: list[Chunk] = []
    for section in _sections(body, document_title):
        for fragment in _pack(section, target_tokens, overlap_tokens):
            index = len(document_chunks) + 1
            fingerprint = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{relative}|{section.path}|{index}|{fingerprint}"))
            chunk = Chunk(
                chunk_id=point_id,
                source_file=relative,
                document_name=path.name,
                document_title=document_title,
                section_title=section.title,
                section_path=section.path,
                chunk_index=index,
                front_matter=front_matter,
                text=fragment,
            )
            if token_count(chunk.embedding_text) > EMBEDDING_TOKEN_LIMIT:
                raise CorpusError(
                    f"{path.name}, раздел «{section.title}»: фрагмент превышает "
                    f"{EMBEDDING_TOKEN_LIMIT} токенов для embeddings"
                )
            document_chunks.append(chunk)
    if not document_chunks:
        raise CorpusError(f"{path.name}: документ не содержит текста для индексации")
    return document_chunks


def chunk_corpus(
    source_dir: Path, project_root: Path, target_tokens: int = 500, overlap_tokens: int = 60
) -> list[Chunk]:
    if not source_dir.is_dir():
        raise CorpusError(f"Папка документации не найдена: {source_dir}")
    files = sorted(source_dir.glob("*.md"))
    if not files:
        raise CorpusError(f"В папке {source_dir} нет Markdown-файлов")
    chunks = [
        chunk
        for path in files
        for chunk in chunk_document(path, project_root, target_tokens, overlap_tokens)
    ]
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise CorpusError("Обнаружены повторяющиеся идентификаторы фрагментов")
    return chunks
