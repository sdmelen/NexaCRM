"""Прогон ответов по контрольному набору с сохранением материала для ручной проверки."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from nexacrm.agent import SupportAgent
from nexacrm.config import ConfigError, load_config
from nexacrm.eval_cases import load_and_validate_cases
from nexacrm.llm import AnswerError
from nexacrm.retrieval import RetrievalError


DEFAULT_REVIEWS = Path("tests/fixtures/answer_reviews_iteration4.json")


def pending_review() -> dict:
    return {
        "expected_facts_met": None,
        "all_substantial_claims_supported": None,
        "expected_behavior_met": None,
        "notes": "",
    }


def _save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def review_hash(response: dict) -> str:
    material = {"answer": response["answer"], "sources": response["sources"]}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_reviews(report: dict, review_path: Path) -> tuple[int, int]:
    if not review_path.is_file():
        return 0, 0
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    matched = stale = 0
    for record in report["cases"]:
        result = record.get("result")
        review = reviews.get(record["id"])
        if not result or not review:
            continue
        if review.get("answer_and_sources_sha256") != review_hash(result["response"]):
            record["manual_review"] = pending_review()
            stale += 1
            continue
        record["manual_review"] = {
            "expected_facts_met": review["expected_facts_met"],
            "all_substantial_claims_supported": review["all_substantial_claims_supported"],
            "expected_behavior_met": review["expected_behavior_met"],
            "notes": review.get("notes", ""),
        }
        matched += 1
    return matched, stale


def main() -> int:
    parser = argparse.ArgumentParser(description="Оценить ответы AI-оператора NexaCRM")
    parser.add_argument("--output", type=Path, default=Path("data/answer_eval.json"))
    parser.add_argument("--case-id", help="Оценить один контрольный случай")
    parser.add_argument("--review-file", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--apply-review", action="store_true", help="Применить ручную проверку к сохранённому отчёту без API")
    args = parser.parse_args()
    try:
        if args.apply_review:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            matched, stale = apply_reviews(report, args.review_file)
            _save(args.output, report)
            print(f"Ручная проверка: {matched} совпадений, {stale} устаревших оценок.")
            return 0 if stale == 0 else 2
        config = load_config()
        cases = load_and_validate_cases()
        if args.case_id:
            cases = [case for case in cases if case["id"] == args.case_id]
            if not cases:
                raise ValueError(f"Неизвестный case ID: {args.case_id}")
        agent = SupportAgent(config)
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "answer_model": config.answer_model,
            "query_model": config.query_model,
            "embedding_model": config.embedding_model,
            "cases": [],
        }
        for case in cases:
            record = {
                "id": case["id"],
                "kind": case["kind"],
                "question": case["question"],
                "history": case["history"],
                "expected_facts": case["expected_facts"],
                "evidence": case["evidence"],
                "expected_behavior": case["expected_behavior"],
                "result": None,
                "error": None,
                "manual_review": pending_review(),
            }
            try:
                reply = agent.answer(case["question"], case["history"])
                record["result"] = reply.as_dict()
                response = record["result"]["response"]
                allowed = {
                    item["metadata"]["chunk_id"]
                    for item in record["result"]["retrieval"]["context"]
                }
                record["source_ids_valid"] = all(
                    chunk_id in allowed for chunk_id in response["used_chunk_ids"]
                )
                print(
                    f"{case['id']}: {'answer' if response['is_answerable'] else 'abstain'}, "
                    f"sources={len(response['sources'])}", flush=True,
                )
            except (AnswerError, RetrievalError) as exc:
                record["error"] = str(exc)
                print(f"{case['id']}: ERROR {type(exc).__name__}", flush=True)
            except Exception as exc:
                record["error"] = type(exc).__name__
                print(f"{case['id']}: ERROR {type(exc).__name__}", flush=True)
            report["cases"].append(record)
            _save(args.output, report)
        errors = sum(record["error"] is not None for record in report["cases"])
        matched, stale = apply_reviews(report, args.review_file)
        _save(args.output, report)
        print(f"Завершено: {len(report['cases'])} случаев, ошибок: {errors}. Файл: {args.output}")
        print(f"Ручная проверка применена к {matched} ответам; устаревших оценок: {stale}.")
        return 1 if errors else 0
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Оценка не выполнена: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
