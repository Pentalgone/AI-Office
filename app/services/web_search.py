
import os
import re
import base64
import asyncio
import ipaddress
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()


class SearchResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attributes = dict(attrs)
            href = attributes.get("href", "")

            if href.startswith("http"):
                self.current_link = {
                    "title": "",
                    "url": href
                }

    def handle_data(self, data):
        if self.current_link:
            self.current_link["title"] += data.strip()

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link:
            title = self.current_link["title"].strip()

            if title and not any(
                item["url"] == self.current_link["url"]
                for item in self.results
            ):
                self.results.append({
                    "title": title,
                    "url": self.current_link["url"]
                })

            self.current_link = None


class PageTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.page_title = ""
        self.in_title = False
        self.skip_depth = 0

        self.skip_tags = {
            "script", "style", "noscript",
            "svg", "nav", "footer", "header"
        }

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

        if tag in self.skip_tags:
            self.skip_depth += 1

        if tag in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

        if tag in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

        if tag in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        text = data.strip()

        if not text:
            return

        if self.in_title:
            self.page_title += " " + text

        if self.skip_depth == 0:
            self.text_parts.append(text)

    def get_text(self):
        text = " ".join(self.text_parts)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


def is_safe_url(url: str) -> bool:
    """Базовая проверка URL до сетевого обращения."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.username or parsed.password or not parsed.hostname:
            return False
        if parsed.port not in (None, 80, 443):
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return True
    except Exception:
        return False


async def hostname_resolves_publicly(hostname: str) -> bool:
    """Отклоняет DNS-имена, указывающие на приватные/служебные IP-адреса."""
    import socket
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM
        )
        addresses = {record[4][0] for record in records}
        return bool(addresses) and all(
            ipaddress.ip_address(address.split("%")[0]).is_global
            for address in addresses
        )
    except Exception:
        return False


async def read_page(client: httpx.AsyncClient, item: dict) -> dict:
    current_url = item.get("url", "")
    max_redirects = 5
    try:
        for redirect_count in range(max_redirects + 1):
            if not is_safe_url(current_url):
                item["fetch_status"] = "rejected_url"
                return item
            hostname = urlparse(current_url).hostname
            if not hostname or not await hostname_resolves_publicly(hostname):
                item["fetch_status"] = "rejected_host"
                return item

            async with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location or redirect_count >= max_redirects:
                        item["fetch_status"] = "redirect_limit"
                        return item
                    from urllib.parse import urljoin
                    current_url = urljoin(str(response.url), location)
                    continue
                if response.status_code >= 400:
                    item["fetch_status"] = f"http_{response.status_code}"
                    return item
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    item["fetch_status"] = "not_html"
                    return item

                chunks = []
                total_size = 0
                max_size = 1_000_000
                async for chunk in response.aiter_bytes():
                    remaining = max_size - total_size
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining]
                    chunks.append(chunk)
                    total_size += len(chunk)

                html = b"".join(chunks).decode(
                    response.encoding or "utf-8", errors="replace"
                )
                parser = PageTextParser()
                parser.feed(html)
                item["page_title"] = parser.page_title.strip()
                item["content"] = parser.get_text()[:6000]
                item["final_url"] = str(response.url)
                item["fetch_status"] = "read" if item["content"] else "empty"
                return item

        item["fetch_status"] = "redirect_limit"
        return item
    except Exception as error:
        item["fetch_status"] = "error"
        item["fetch_error"] = str(error)[:200]
        return item


async def search_web(query: str, limit: int = 5) -> list[dict]:
    api_key = os.getenv("YANDEX_SEARCH_API_KEY")
    folder_id = os.getenv("YANDEX_FOLDER_ID")

    if not api_key or not folder_id:
        raise ValueError(
            "Не настроены Yandex Search API ключ или ID каталога"
        )

    if not query.strip():
        raise ValueError("Поисковый запрос не может быть пустым")

    url = "https://searchapi.api.cloud.yandex.net/v2/web/search"

    payload = {
        "query": {
            "searchType": "SEARCH_TYPE_RU",
            "queryText": query,
            "familyMode": "FAMILY_MODE_STRICT"
        },
        "groupSpec": {
            "groupsOnPage": str(max(5, min(limit, 10)))
        },
        "region": "225",
        "l10n": "LOCALIZATION_RU",
        "folderId": folder_id,
        "responseFormat": "FORMAT_HTML"
    }

    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=False,
        headers={
            "User-Agent": "Mozilla/5.0 AI-Office Research Bot"
        }
    ) as client:

        response = await client.post(
            url,
            json=payload,
            headers=headers
        )

        response.raise_for_status()
        data = response.json()

        raw_data = data.get("rawData")

        if not raw_data:
            return []

        html = base64.b64decode(raw_data).decode(
            "utf-8",
            errors="replace"
        )

        parser = SearchResultParser()
        parser.feed(html)

        excluded_domains = (
            "yandex.ru",
            "yandex.net",
            "ya.ru",
            "yastatic.net"
        )

        filtered_results = []

        for item in parser.results:
            domain = urlparse(item["url"]).netloc.lower()

            if any(
                domain == excluded or domain.endswith("." + excluded)
                for excluded in excluded_domains
            ):
                continue

            if not is_safe_url(item["url"]):
                continue

            filtered_results.append(item)

        filtered_results = filtered_results[:limit]

        semaphore = asyncio.Semaphore(3)

        async def read_limited(item):
            async with semaphore:
                return await read_page(client, item)

        results = await asyncio.gather(
            *(read_limited(item) for item in filtered_results)
        )

        return results

async def read_urls(urls: list[str]) -> list[dict]:
    """
    Открывает и читает конкретные URL без поискового запроса.
    """

    unique_urls = list(dict.fromkeys(
        url.strip() for url in urls if url and url.strip()
    ))

    items = [
        {
            "title": "",
            "url": url
        }
        for url in unique_urls[:10]
    ]

    if not items:
        return []

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=False,
        headers={
            "User-Agent": "Mozilla/5.0 AI-Office Research Bot"
        }
    ) as client:

        semaphore = asyncio.Semaphore(3)

        async def read_limited(item):
            async with semaphore:
                return await read_page(client, item)

        results = await asyncio.gather(
            *(read_limited(item) for item in items)
        )

    return results

