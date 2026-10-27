"""Воспроизводимая проверка попадания эталонных разделов в top-5 контекста."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from nexacrm.config import ConfigError, load_config
from nexacrm.eval_cases import load_and_validate_cases
from nexacrm.retrieval import RetrievalEngine, RetrievalError
from nexacrm.retrieval_openai import OpenAIRetrievalProvider


def _evidence_found(evidence: dict, context: list[dict], require_quote: bool) -> bool:
    for item in context:
        metadata = item["metadata"]
        if metadata["source_file"] != evidence["source_file"]:
            continue
        if evidence["section"] not in metadata["section_path"]:
            continue
        if require_quote and evidence["quote"] not in item["text"]:
            continue
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Оценить retrieval по контрольным вопросам")
    parser.add_argument("--output", type=Path, default=Path("data/retrieval_eval.json"))
    parser.add_argument("--case-id", help="Запустить только один контрольный случай")
    args = parser.parse_args()
    try:
        config = load_config()
        cases = load_and_validate_cases()
        if args.case_id:
            cases = [case for case in cases if case["id"] == args.case_id]
            if not cases:
                raise ValueError(f"Неизвестный case ID: {args.case_id}")
        provider = OpenAIRetrievalProvider(config)
        records = []
        with RetrievalEngine(config) as engine:
            if engine.index_status.stale:
                raise RetrievalError("Индекс устарел; выполните python ingest.py перед оценкой")
            for case in cases:
                result = engine.search(
                    case["question"], case["history"], provider.embed, provider.rewrite
                )
                diagnostic = result.diagnostic()
                evidence = case["evidence"]
                section_found = all(
                    _evidence_found(item, diagnostic["context"], require_quote=False)
                    for item in evidence
                )
                quote_found = all(
                    _evidence_found(item, diagnostic["context"], require_quote=True)
                    for item in evidence
                )
                records.append({
                    "id": case["id"],
                    "kind": case["kind"],
                    "evidence": evidence,
                    "expected_sections_found": section_found if evidence else None,
                    "expected_quotes_found": quote_found if evidence else None,
                    "retrieval": diagnostic,
                })
                if evidence:
                    print(f"{case['id']}: sections={'OK' if section_found else 'MISS'} "
                          f"quotes={'OK' if quote_found else 'MISS'}")
                else:
                    print(f"{case['id']}: без эталонного источника")
        answerable = [record for record in records if record["evidence"]]
        section_count = sum(record["expected_sections_found"] for record in answerable)
        quote_count = sum(record["expected_quotes_found"] for record in answerable)
        summary = {
            "total_cases": len(records),
            "answerable_cases": len(answerable),
            "section_hits": section_count,
            "section_hit_rate": section_count / len(answerable) if answerable else None,
            "quote_hits": quote_count,
            "quote_hit_rate": quote_count / len(answerable) if answerable else None,
        }
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "cases": records}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Итог: {section_count}/{len(answerable)} по разделам; "
              f"{quote_count}/{len(answerable)} по точным цитатам.")
        print(f"Подробный отчёт: {args.output}")
        return 0 if not answerable or section_count / len(answerable) >= 0.9 else 2
    except (ConfigError, RetrievalError, OSError, ValueError) as exc:
        print(f"Оценка не выполнена: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
