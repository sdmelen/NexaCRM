from nexacrm.eval_cases import load_and_validate_cases


def test_control_cases_are_supported_by_source() -> None:
    cases = load_and_validate_cases()
    assert len(cases) >= 30
    assert any(case["kind"] == "unanswerable" for case in cases)
    assert any(case["kind"] == "followup" for case in cases)
    assert any(case["expected_behavior"] == "answer_then_ticket_confirmation" for case in cases)
