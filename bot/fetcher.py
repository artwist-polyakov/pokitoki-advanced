import json
import re
import urllib.parse
import ipaddress

import httpx
from bs4 import BeautifulSoup
from httpx import HTTPStatusError, RequestError

from bot.config import config


class Fetcher:
    """Retrieves remote content over HTTP."""

    # Matches non-quoted URLs in text
    url_re = re.compile(r"(?:[^'\"]|^)\b(https?://\S+)\b(?:[^'\"]|$)")
    timeout = 5  # seconds (you can increase if needed)

    def __init__(self):
        """
        By default, we use an httpx.AsyncClient with browser-like headers.
        If the server returns 403, or we face network issues,
        we'll attempt to use Scrape.do as a fallback (if a token is provided).
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/108.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.client = httpx.AsyncClient(
            follow_redirects=True, timeout=self.timeout, headers=headers
        )

    async def substitute_urls(self, text: str) -> str:
        """
        Extracts URLs from the given text, fetches their contents,
        and appends the content to the text in a separated block.
        """
        urls = self._extract_urls(text)
        for url in urls:
            content_str = await self._fetch_url(url)
            text += f"\n\n---\n{url} contents:\n\n{content_str}\n---"
        return text

    async def close(self) -> None:
        """Closes the underlying httpx client."""
        await self.client.aclose()

    def _extract_urls(self, text: str) -> list[str]:
        """Finds all URLs in the text by regex and filters local addresses."""
        urls = self.url_re.findall(text)
        return [url for url in urls if not self._is_local_url(url)]

    def _is_local_url(self, url: str) -> bool:
        """Returns True if the URL points to a localhost or private address."""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return True
        host = host.lower()
        if host == "localhost":
            return True
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False

    async def _fetch_url(self, url: str) -> str:
        """Fetches a resource and extracts text using either transport."""
        try:
            response = await self._fetch_response(url)
            return Content(response).extract_text()
        except (HTTPStatusError, RequestError):
            raise
        except Exception as exc:
            return f"Failed to fetch ({exc.__class__.__module__}.{exc.__class__.__qualname__})"

    async def _fetch_response(self, url: str) -> httpx.Response:
        """Tries a direct request, then Scrape.do for access or network errors."""
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response
        except HTTPStatusError as exc:
            if exc.response.status_code not in (401, 403):
                raise
            token = config.scrapdo.token
            if not token or not token.strip():
                raise
        except RequestError:
            token = config.scrapdo.token
            if not token or not token.strip():
                raise

        return await self._fetch_via_scrapdo(url, token)

    async def _fetch_via_scrapdo(self, url: str, token: str) -> httpx.Response:
        """Returns a Scrape.do response with the same interface as a direct request."""
        response = await self.client.get(
            "https://api.scrape.do",
            params={"token": token, "url": url},
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        response.raise_for_status()
        return response


class Content:
    """Extracts resource content as human-readable text."""

    allowed_content_types = {
        "application/json",
        "application/sql",
        "application/xml",
    }

    def __init__(self, response: httpx.Response):
        content_type = response.headers.get("content-type", "")
        content_type, _, _ = content_type.partition(";")
        self.content_type = content_type.strip().lower()
        self.response = response

    def extract_text(self) -> str:
        """
        If the response is not text/html, return its text as is.
        Otherwise, parse the HTML with BeautifulSoup and extract text from
        <main> or <body>.
        """
        if not self.is_text():
            return "Unknown binary content"

        if self.content_type != "text/html":
            return self.response.text

        html = BeautifulSoup(self.response.text, "html.parser")

        json_scripts = html.find_all("script", type="application/ld+json")
        for script in json_scripts:
            try:
                data = json.loads(script.string)
                # Ищем articleBody в JSON
                if "articleBody" in data:
                    return data["articleBody"]
            except (json.JSONDecodeError, AttributeError):
                continue

        content = (
            html.find("main")
            or html.find("article")
            or html.find("div", class_="content")
            or html.find("div", class_="article")
            or html.find("div", {"id": "content"})
            or html.find("div", {"id": "main"})
            or html.find("body")
        )

        if content:
            for tag in content.find_all(["script", "style", "nav", "header", "footer"]):
                if tag.get("type") != "application/ld+json":
                    tag.decompose()

            text = content.get_text(separator="\n", strip=True)

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

        text = html.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def is_text(self) -> bool:
        if not self.content_type:
            return False
        if self.content_type.startswith("text/"):
            return True
        return self.content_type in self.allowed_content_types
