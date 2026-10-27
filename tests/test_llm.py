import pytest

from nexacrm.llm import AnswerDraft, AnswerError, INSUFFICIENT_ANSWER, build_answer
from nexacrm.retrieval import FusedHit, IndexedChunk, RetrievalResult


def _retrieval(with_context: bool = True) -> RetrievalResult:
    chunk = IndexedChunk(
        chunk_id="chunk-1", source_file="source/guide.md", document_name="guide.md",
        document_title="Руководство", section_title="Раздел", section_path=("Руководство", "Раздел"),
        chunk_index=2, front_matter={"category": "Тест"}, text="Ссылка действует 7 дней.",
    )
    context = (FusedHit(chunk, 1, 0.03, 1, 2),) if with_context else ()
    return RetrievalResult("Сколько действует ссылка?", "Сколько действует ссылка?", False,
                           (), (), context, context)


def test_answer_sources_are_built_from_retrieved_metadata() -> None:
    result = build_answer(
        _retrieval(), [],
        lambda question, history, chunks: AnswerDraft(
            answer="Ссылка действует 7 дней.", used_chunk_ids=["chunk-1"], is_answerable=True
        ),
    )
    assert result.is_answerable
    assert result.sources[0].source_file == "source/guide.md"
    assert result.sources[0].section_title == "Раздел"
    assert result.sources[0].front_matter == {"category": "Тест"}
    assert result.sources[0].text == "Ссылка действует 7 дней."


@pytest.mark.parametrize("ids", [[], ["other-id"], ["chunk-1", "chunk-1"]])
def test_invalid_source_ids_never_become_confirmed_sources(ids: list[str]) -> None:
    with pytest.raises(AnswerError):
        build_answer(
            _retrieval(), [],
            lambda question, history, chunks: AnswerDraft(
                answer="Недостоверный ответ", used_chunk_ids=ids, is_answerable=True
            ),
        )


def test_unanswerable_discards_model_instruction_and_sources() -> None:
    result = build_answer(
        _retrieval(), [],
        lambda question, history, chunks: AnswerDraft(
            answer="Выдуманная кнопка находится в настройках.",
            used_chunk_ids=["chunk-1"], is_answerable=False,
        ),
    )
    assert result.answer == INSUFFICIENT_ANSWER
    assert not result.sources


def test_empty_context_avoids_model_call() -> None:
    def fail(*args):
        raise AssertionError("Не нужно вызывать модель")

    result = build_answer(_retrieval(with_context=False), [], fail)
    assert not result.is_answerable
    assert result.answer == INSUFFICIENT_ANSWER


def test_answer_length_limit_rejects_oversized_model_output() -> None:
    with pytest.raises(AnswerError):
        build_answer(
            _retrieval(), [],
            lambda question, history, chunks: AnswerDraft(
                answer="Слишком длинный ответ", used_chunk_ids=["chunk-1"], is_answerable=True
            ),
            max_answer_chars=10,
        )
