from datetime import date

from news_parsers.news_archive import archive_cutoff, archive_object_key


def test_archive_cutoff_is_one_year_before():
    assert archive_cutoff(date(2026, 10, 22), days=365) == date(2025, 10, 22)


def test_archive_key_groups_by_month():
    assert (
        archive_object_key("rag", date(2024, 3, 15))
        == "news/rag/2024/2024-03.jsonl.gz"
    )
