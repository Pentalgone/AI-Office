import re
from urllib.parse import urlparse, urlunparse

from app.services.web_search import read_urls


URL_PATTERN = re.compile(r"https?://[^\\s<>\"']+", re.IGNORECASE)


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


def _url_key(url: str) -> str:
    """Normalize harmless URL differences for provenance matching."""
    try:
        parsed = urlparse(url.strip())
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            parsed.query,
            "",
        ))
    except Exception:
        return url.strip()


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


async def verify_report_sources(
    report: str,
    available_sources: list[dict] | None = None,
) -> list[dict]:
    """
    Audit cited URLs against the actual research context.

    A URL absent from available_sources is never fetched just because an LLM
    inserted it into a report. This prevents invented links from being treated
    as research results. Without available_sources, retain the legacy safe
    page-read check for callers that explicitly need it.
    """
    urls = extract_urls(report)
    if not urls:
        return []

    if available_sources is not None:
        known = {}
        for source in available_sources:
            for candidate in (source.get("url"), source.get("final_url")):
                if candidate:
                    known[_url_key(candidate)] = source

        audited = []
        for url in urls:
            source = known.get(_url_key(url))
            if source is None:
                audited.append({
                    "url": url,
                    "status": "not_in_research_context",
                    "page_title": "",
                    "source_origin": "",
                    "details": "Этой ссылки не было среди материалов, реально полученных поиском или переданных сотрудниками.",
                })
                continue
            audited.append({
                "url": source.get("final_url") or source.get("url") or url,
                "status": classify_fetch_status(source),
                "page_title": source.get("page_title") or source.get("title") or "",
                "source_origin": source.get("source_origin") or "Переданный материал",
                "details": (source.get("content") or "")[:500],
            })
        return audited

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
        by_url[_url_key(original)] = {
            "url": page.get("final_url") or original,
            "status": classify_fetch_status(page),
            "page_title": page.get("page_title") or page.get("title") or "",
            "details": (page.get("content") or "")[:500],
        }

    return [
        by_url.get(_url_key(url), {
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
        "| URL | Происхождение | Технический статус | Заголовок страницы |",
        "|---|---|---|---|",
    ]
    for source in sources:
        url = str(source.get("url", "")).replace("|", "%7C")
        title = str(source.get("page_title", "")).replace("|", "/").replace("\\n", " ")[:180]
        origin = str(source.get("source_origin", "не установлено")).replace("|", "/")
        status = str(source.get("status", "unknown"))
        lines.append(f"| {url} | {origin} | {status} | {title} |")
    lines.append(
        "\\n**Важно:** статус «page_read_claims_not_independently_verified» означает "
        "только то, что текст страницы был получен. Он не подтверждает научную "
        "достоверность публикации и не доказывает конкретное утверждение. "
        "Статус «not_in_research_context» означает, что ссылка отсутствовала "
        "в фактических материалах исследования и не должна использоваться как доказательство."
    )
    return "\\n".join(lines)
