import asyncio

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



def test_report_url_not_present_in_research_context_is_flagged():
    from app.services.source_verifier import verify_report_sources

    result = asyncio.run(verify_report_sources(
        "Источник: https://invented.example/fake-paper",
        available_sources=[{
            "url": "https://real.example/paper",
            "title": "Real paper",
            "fetch_status": "read",
            "content": "Actual page text",
            "source_origin": "Результат веб-поиска",
        }],
    ))

    assert len(result) == 1
    assert result[0]["status"] == "not_in_research_context"


def test_report_url_must_match_a_source_actually_received():
    from app.services.source_verifier import verify_report_sources

    result = asyncio.run(verify_report_sources(
        "Источник: https://real.example/paper",
        available_sources=[{
            "url": "https://real.example/paper/",
            "title": "Real paper",
            "page_title": "Real paper page",
            "fetch_status": "read",
            "content": "Actual page text",
            "source_origin": "Результат веб-поиска",
        }],
    ))

    assert result[0]["status"] == "page_read_claims_not_independently_verified"
    assert result[0]["source_origin"] == "Результат веб-поиска"
