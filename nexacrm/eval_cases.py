"""Проверка эталонных вопросов и точных цитат из source/."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from nexacrm.config import PROJECT_ROOT
from nexacrm.corpus import HEADING_RE, CorpusError, read_document_overview


DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "eval_cases.jsonl"
ALLOWED_BEHAVIOR = {
    "answer",
    "abstain_offer_ticket",
    "offer_ticket_confirmation",
    "answer_then_ticket_confirmation",
}


def _section_text(path: Path, title: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    level = None
    for index, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if not heading:
            continue
        current_level = len(heading.group(1))
        current_title = heading.group(2).strip()
        if start is not None and current_level <= level:
            return "\n".join(lines[start:index])
        if start is None and current_title == title:
            start = index + 1
            level = current_level
    if start is None:
        raise CorpusError(f"{path.name}: раздел «{title}» не найден")
    return "\n".join(lines[start:])


def load_and_validate_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    source_files: set[str] = set()
    categories: Counter[str] = Counter()
    allowed_source_dir = (PROJECT_ROOT / "source").resolve()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Строка {line_number}: неверный JSON: {exc}") from exc
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError(f"Строка {line_number}: отсутствует или повторяется id")
        ids.add(case_id)
        if case.get("kind") not in {"answerable", "followup", "unanswerable", "interaction"}:
            raise ValueError(f"{case_id}: неизвестный kind")
        if case.get("expected_behavior") not in ALLOWED_BEHAVIOR:
            raise ValueError(f"{case_id}: неизвестный expected_behavior")
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError(f"{case_id}: пустой вопрос")
        if not isinstance(case.get("expected_facts"), list) or not isinstance(case.get("evidence"), list):
            raise ValueError(f"{case_id}: expected_facts и evidence должны быть списками")
        if case["kind"] in {"answerable", "followup"} and (
            not case["expected_facts"] or not case["evidence"]
        ):
            raise ValueError(f"{case_id}: для ответа нужны факты и подтверждающие фрагменты")
        if case["kind"] == "unanswerable" and (case["expected_facts"] or case["evidence"]):
            raise ValueError(f"{case_id}: вопрос без ответа не должен иметь ожидаемых фактов")
        history = case.get("history", [])
        if not isinstance(history, list) or any(
            not isinstance(message, dict)
            or message.get("role") not in {"user", "assistant"}
            or not isinstance(message.get("content"), str)
            for message in history
        ):
            raise ValueError(f"{case_id}: некорректная история диалога")
        if case["kind"] == "followup" and not history:
            raise ValueError(f"{case_id}: уточняющему вопросу нужна история")
        tags = case.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"{case_id}: tags должны быть списком строк")
        categories.update(tags)
        for item in case["evidence"]:
            if not isinstance(item, dict):
                raise ValueError(f"{case_id}: неверный элемент evidence")
            source_file = item.get("source_file")
            section = item.get("section")
            quote = item.get("quote")
            if not all(isinstance(value, str) and value for value in (source_file, section, quote)):
                raise ValueError(f"{case_id}: evidence требует source_file, section и quote")
            source_path = (PROJECT_ROOT / source_file).resolve()
            if source_path.parent != allowed_source_dir or source_path.suffix.lower() != ".md":
                raise ValueError(f"{case_id}: источник должен быть файлом из source/")
            if not source_path.is_file():
                raise ValueError(f"{case_id}: источник не найден: {source_file}")
            read_document_overview(source_path)
            if quote not in _section_text(source_path, section):
                raise ValueError(f"{case_id}: цитата не найдена в разделе «{section}» файла {source_file}")
            source_files.add(source_file)
        cases.append(case)
    if len(cases) < 30:
        raise ValueError("Набор оценки должен содержать не менее 30 случаев")
    if len(source_files) < 15:
        raise ValueError("Набор оценки должен покрывать все 15 файлов source/")
    for tag, minimum in {
        "direct": 15,
        "table": 4,
        "code": 4,
        "multi_document": 4,
        "context": 4,
        "out_of_scope": 4,
        "ticket": 1,
        "session_limit": 1,
    }.items():
        if categories[tag] < minimum:
            raise ValueError(f"Недостаточно случаев с тегом {tag}: {categories[tag]} < {minimum}")
    return cases


if __name__ == "__main__":
    validated = load_and_validate_cases()
    print(f"Проверено {len(validated)} контрольных случаев; ссылки и цитаты найдены в source/.")

