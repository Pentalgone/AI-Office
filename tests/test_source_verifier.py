import pytest

from app.services.source_verifier import (
    classify_fetch_status,
    extract_urls,
    format_source_audit,
    research_requested,
)


def test_extract_urls_deduplicates_and_trims_punctuation():
    text = "См. https://example.org/paper. И ещё https://example.org/paper, https://example.org/other"
    assert extract_urls(text) == [
        "https://example.org/paper",
        "https://example.org/other",
    ]


def test_research_requested_detects_source_based_tasks():
    assert research_requested("Обзор преимуществ инструмента")
    assert research_requested(None, "Найти научные публикации")
    assert not research_requested("Подготовить письмо о встрече")


def test_readable_page_is_not_mislabeled_as_fact_checked():
    status = classify_fetch_status({
        "fetch_status": "read",
        "content": "Страница открылась",
    })
    assert status == "page_read_claims_not_independently_verified"


def test_unavailable_page_keeps_http_status():
    assert classify_fetch_status({"fetch_status": "http_404"}) == "http_404"


def test_audit_explains_limit_of_technical_check():
    rendered = format_source_audit([{
        "url": "https://example.org/paper",
        "status": "http_404",
        "page_title": "",
    }])
    assert "http_404" in rendered
    assert "не подтверждают достоверность" in rendered
