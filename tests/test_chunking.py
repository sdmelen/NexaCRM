from pathlib import Path

from nexacrm.chunking import chunk_corpus, chunk_document, token_count


def test_metadata_tables_and_code_blocks_stay_intact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    path = source / "guide.md"
    table = "| Поле | Значение |\n| --- | --- |\n| Код | 42 |"
    code = "~~~python\nvalue = 42\n\nprint(value)\n~~~"
    path.write_text(
        "---\ntitle: Название из YAML\ncategory: Проверка\nupdated: 2026-10-16\n---\n"
        "# Заголовок H1\n\n## Раздел H2\n\n"
        + table
        + "\n\n"
        + code
        + "\n\n"
        + "Описание функции. " * 100,
        encoding="utf-8",
    )
    first = chunk_document(path, tmp_path, target_tokens=90, overlap_tokens=15)
    second = chunk_document(path, tmp_path, target_tokens=90, overlap_tokens=15)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.chunk_index for chunk in first] == list(range(1, len(first) + 1))
    assert all(chunk.source_file == "source/guide.md" for chunk in first)
    assert all(chunk.document_title == "Название из YAML" for chunk in first)
    assert all(chunk.front_matter["updated"] == "2026-10-16" for chunk in first)
    assert any(chunk.section_path == ("Заголовок H1", "Раздел H2") for chunk in first)
    assert any(table in chunk.text for chunk in first)
    assert any(code in chunk.text for chunk in first)
    assert len(first) > 2
    assert max(token_count(chunk.embedding_text) for chunk in first) < 8192


def test_document_without_yaml_uses_h1_and_unique_ids(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.md").write_text("# Документ А\n\nТекст А.\n", encoding="utf-8")
    (source / "b.md").write_text("# Документ Б\n\nТекст Б.\n", encoding="utf-8")
    chunks = chunk_corpus(source, tmp_path, target_tokens=100, overlap_tokens=10)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert {chunk.document_title for chunk in chunks} == {"Документ А", "Документ Б"}
    assert all(chunk.front_matter == {} for chunk in chunks)


def test_full_project_corpus_chunks_are_stable() -> None:
    root = Path(__file__).resolve().parents[1]
    first = chunk_corpus(root / "source", root)
    second = chunk_corpus(root / "source", root)
    assert len(first) >= 15
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert {chunk.source_file for chunk in first} == {
        f"source/{path.name}" for path in (root / "source").glob("*.md")
    }

