import json

from evaluate_answers import apply_reviews, review_hash


def test_manual_review_only_applies_to_exact_answer_and_sources(tmp_path) -> None:
    response = {"answer": "Ссылка действует 7 дней.", "sources": [{"chunk_id": "one", "text": "7 дней"}]}
    review_path = tmp_path / "reviews.json"
    review_path.write_text(json.dumps({
        "case-1": {
            "answer_and_sources_sha256": review_hash(response),
            "expected_facts_met": True,
            "all_substantial_claims_supported": True,
            "expected_behavior_met": True,
            "notes": "Проверено",
        }
    }), encoding="utf-8")
    report = {"cases": [{"id": "case-1", "result": {"response": response}, "manual_review": {}}]}
    assert apply_reviews(report, review_path) == (1, 0)
    assert report["cases"][0]["manual_review"]["expected_facts_met"] is True

    response["sources"][0]["text"] = "8 дней"
    assert apply_reviews(report, review_path) == (0, 1)
    assert report["cases"][0]["manual_review"]["expected_facts_met"] is None
