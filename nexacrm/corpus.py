"""Лёгкий аудит Markdown-корпуса для первой итерации."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


class CorpusError(ValueError):
    """Ошибка структуры исходной документации."""


@dataclass(frozen=True)
class Heading:
    level: int
    title: str


@dataclass(frozen=True)
class DocumentOverview:
    path: Path
    title: str
    category: str
    updated: str
    front_matter: dict[str, Any]
    headings: tuple[Heading, ...]
    table_count: int
    code_block_count: int


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise CorpusError(f"{path.name}: не закрыт YAML front matter") from exc
    try:
        raw = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path.name}: ошибка YAML front matter: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError(f"{path.name}: YAML front matter должен быть объектом")
    return raw, lines[end + 1 :]


def read_document_overview(path: Path) -> DocumentOverview:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CorpusError(f"{path.name}: не удалось прочитать UTF-8: {exc}") from exc
    metadata, body = _split_front_matter(text, path)
    headings: list[Heading] = []
    table_count = 0
    code_block_count = 0
    in_fence = False
    fence_char = ""
    fence_length = 0
    in_table = False
    for line in body:
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_length = len(marker)
                code_block_count += 1
            elif marker[0] == fence_char and len(marker) >= fence_length:
                in_fence = False
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            headings.append(Heading(level=len(match.group(1)), title=match.group(2).strip()))
        is_table_line = line.strip().startswith("|") and line.strip().endswith("|")
        if is_table_line and not in_table:
            table_count += 1
        in_table = is_table_line
    h1 = next((item.title for item in headings if item.level == 1), path.stem)
    return DocumentOverview(
        path=path,
        title=str(metadata.get("title") or h1),
        category=str(metadata.get("category") or ""),
        updated=str(metadata.get("updated") or ""),
        front_matter=metadata,
        headings=tuple(headings),
        table_count=table_count,
        code_block_count=code_block_count,
    )


def scan_corpus(source_dir: Path) -> list[DocumentOverview]:
    if not source_dir.is_dir():
        raise CorpusError(f"Папка документации не найдена: {source_dir}")
    files = sorted(source_dir.glob("*.md"))
    if not files:
        raise CorpusError(f"В папке {source_dir} нет Markdown-файлов")
    return [read_document_overview(path) for path in files]

