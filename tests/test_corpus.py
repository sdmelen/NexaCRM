from pathlib import Path

import pytest

from nexacrm.corpus import CorpusError, read_document_overview, scan_corpus


def test_reads_yaml_and_ignores_headings_inside_code(tmp_path: Path) -> None:
    document = tmp_path / "sample.md"
    document.write_text(
        "---\ntitle: Название из YAML\ncategory: Тест\nupdated: 2026-10-16\n---\n"
        "# Основной заголовок\n## Раздел\n| Колонка |\n| --- |\n| Значение |\n"
        "~~~markdown\n# Это не заголовок\n~~~\n",
        encoding="utf-8",
    )
    result = read_document_overview(document)
    assert result.title == "Название из YAML"
    assert result.category == "Тест"
    assert [heading.title for heading in result.headings] == ["Основной заголовок", "Раздел"]
    assert result.table_count == 1
    assert result.code_block_count == 1


def test_uses_h1_without_yaml(tmp_path: Path) -> None:
    document = tmp_path / "sample.md"
    document.write_text("# Заголовок документа\nТекст.\n", encoding="utf-8")
    result = read_document_overview(document)
    assert result.title == "Заголовок документа"
    assert result.front_matter == {}


def test_bad_yaml_is_reported(tmp_path: Path) -> None:
    document = tmp_path / "broken.md"
    document.write_text("---\ntitle: [незакрытый список\n---\n# Текст\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="ошибка YAML"):
        read_document_overview(document)


def test_project_corpus_has_fifteen_documents() -> None:
    source_dir = Path(__file__).resolve().parents[1] / "source"
    documents = scan_corpus(source_dir)
    assert len(documents) == 15
    assert all(doc.front_matter.get("title") for doc in documents)

