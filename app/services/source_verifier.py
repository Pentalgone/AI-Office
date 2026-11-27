import re
from urllib.parse import urlparse

from app.services.web_search import read_urls


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_urls(text: str | None) -> list[str]:
    """Extract unique HTTP(S) URLs while removing common trailing punctuation."""
    if not text:
        return []
    found = []
    for match in URL_PATTERN.findall(text):
        url = match.rstrip(".,;:!?)]}")
        if url not in found:
            found.append(url)
    return found[:10]


def research_requested(*parts: str | None) -> bool:
    text = " ".join(part or "" for part in parts).casefold()
    markers = (
        "источник", "источники", "публикац", "научн", "исследован",
        "литератур", "доказательств", "проверить информацию", "найти информацию",
        "преимуществ", "недостатк", "сравн", "обзор рынка", "аналитическ",
        "статистик", "медицинск", "клиническ",
    )
    return any(marker in text for marker in markers)


def classify_fetch_status(item: dict) -> str:
    status = str(item.get("fetch_status") or "unknown")
    if status == "read" and (item.get("content") or "").strip():
        return "page_read_claims_not_independently_verified"
    if status.startswith("http_"):
        return status
    if status in {"rejected_url", "rejected_host", "redirect_limit", "not_html", "empty"}:
        return status
    return "unavailable_or_unverified"


async def verify_report_sources(report: str) -> list[dict]:
    """Check whether cited pages can be safely opened and read; this is not fact-checking."""
    urls = extract_urls(report)
    if not urls:
        return []
    try:
        pages = await read_urls(urls)
    except Exception as error:
        return [
            {
                "url": url,
                "status": "verification_error",
                "details": str(error)[:200],
            }
            for url in urls
        ]

    by_url = {}
    for page in pages:
        original = page.get("url", "")
        by_url[original] = {
            "url": page.get("final_url") or original,
            "status": classify_fetch_status(page),
            "page_title": page.get("page_title") or page.get("title") or "",
            "details": (page.get("content") or "")[:500],
        }

    return [
        by_url.get(url, {
            "url": url,
            "status": "not_checked",
            "page_title": "",
            "details": "Проверка страницы не вернула результата.",
        })
        for url in urls
    ]


def format_source_audit(sources: list[dict]) -> str:
    if not sources:
        return "В отчёте не обнаружено URL-источников."
    lines = [
        "| URL | Статус технической проверки | Заголовок страницы |",
        "|---|---|---|",
    ]
    for source in sources:
        url = str(source.get("url", "")).replace("|", "%7C")
        title = str(source.get("page_title", "")).replace("|", "/").replace("\n", " ")[:180]
        status = str(source.get("status", "unknown"))
        lines.append(f"| {url} | {status} | {title} |")
    lines.append(
        "\nВажно: доступность страницы и её заголовок не подтверждают достоверность "
        "публикации или то, что её содержание доказывает конкретное утверждение. "
        "Для этого нужна отдельная проверка метаданных и содержания."
    )
    return "\n".join(lines)
