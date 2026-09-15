import unittest
from unittest.mock import patch

import httpx

from httpx import Request, Response

from bot.config import config
from bot.fetcher import Content, Fetcher


class FakeClient:
    def __init__(self, responses: dict[str, Response | Exception]) -> None:
        self.responses = responses

    async def get(self, url: str) -> Response:
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        request = Request(method="GET", url=url)
        template = value
        return Response(
            status_code=template.status_code,
            headers=template.headers,
            text=template.text,
            request=request,
        )


class FetcherTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fetcher = Fetcher()

    async def test_substitute_urls(self):
        resp_1 = Response(status_code=200, headers={"content-type": "text/plain"}, text="first")
        resp_2 = Response(status_code=200, headers={"content-type": "text/plain"}, text="second")
        self.fetcher.client = FakeClient(
            {
                "https://example.org/first": resp_1,
                "https://example.org/second": resp_2,
            }
        )
        text = "Compare https://example.org/first and https://example.org/second"
        text = await self.fetcher.substitute_urls(text)
        self.assertEqual(
            text,
            """Compare https://example.org/first and https://example.org/second

---
https://example.org/first contents:

first
---

---
https://example.org/second contents:

second
---""",
        )

    async def test_ignore_quoted(self):
        src = "What is 'https://example.org/first'?"
        text = await self.fetcher.substitute_urls(src)
        self.assertEqual(text, src)

    async def test_nothing_to_substitute(self):
        src = "How are you?"
        text = await self.fetcher.substitute_urls(src)
        self.assertEqual(text, src)

    def test_extract_urls(self):
        text = "Compare https://example.org/first and https://example.org/second"
        urls = self.fetcher._extract_urls(text)
        self.assertEqual(urls, ["https://example.org/first", "https://example.org/second"])

        text = "Extract https://example.org/first."
        urls = self.fetcher._extract_urls(text)
        self.assertEqual(urls, ["https://example.org/first"])

        text = 'Extract "https://example.org/first"'
        urls = self.fetcher._extract_urls(text)
        self.assertEqual(urls, [])

    def test_ignore_local_urls(self):
        text = "Check http://localhost:8000/ and http://127.0.0.1/foo"
        urls = self.fetcher._extract_urls(text)
        self.assertEqual(urls, [])

    async def test_fetch_url(self):
        self.fetcher.client = FakeClient({"https://example.org/boom": RuntimeError("boom")})
        result = await self.fetcher._fetch_url("https://example.org/boom")
        self.assertEqual(result, "Failed to fetch (builtins.RuntimeError)")


class ContentTest(unittest.TestCase):
    def test_extract_as_is(self):
        resp = Response(
            status_code=200, headers={"content-type": "application/sql"}, text="select 42;"
        )
        content = Content(resp)
        text = content.extract_text()
        self.assertEqual(text, "select 42;")

    def test_extract_html(self):
        html = "<html><head></head><body><main>hello</main></body></html>"
        resp = Response(status_code=200, headers={"content-type": "text/html"}, text=html)
        content = Content(resp)
        text = content.extract_text()
        self.assertEqual(text, "hello")

    def test_extract_unknown(self):
        resp = Response(status_code=200, headers={"content-type": "application/pdf"}, text="...")
        content = Content(resp)
        text = content.extract_text()
        self.assertEqual(text, "Unknown binary content")


class FetcherFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fetcher = Fetcher()
        await self.fetcher.close()
        self.token_patch = patch.object(config.scrapdo, "token", "test-token")
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    async def test_direct_and_fallback_extract_the_same_content(self):
        cases = [
            ("text/html; charset=utf-8", "<main>Привет</main>".encode(), "Привет"),
            ("text/plain; charset=windows-1251", "Привет".encode("cp1251"), "Привет"),
            ("application/json", b'{"value": 42}', '{"value": 42}'),
            ("application/pdf", b"%PDF", "Unknown binary content"),
        ]
        url = "https://example.org/page?a=1&b=hello%20world"
        for content_type, body, expected in cases:
            for direct_status in (200, 401, 403):
                with self.subTest(content_type=content_type, direct_status=direct_status):
                    requests = []

                    def handler(request):
                        requests.append(request)
                        if request.url.host == "example.org" and direct_status != 200:
                            return Response(direct_status)
                        return Response(200, headers={"Content-Type": content_type}, content=body)

                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        self.fetcher.client = client
                        self.assertEqual(await self.fetcher._fetch_url(url), expected)
                    self.assertEqual(len(requests), 1 if direct_status == 200 else 2)
                    if direct_status != 200:
                        self.assertEqual(requests[1].url.scheme, "https")
                        self.assertEqual(requests[1].url.host, "api.scrape.do")
                        self.assertEqual(
                            dict(requests[1].url.params),
                            {"token": "test-token", "url": url},
                        )

    async def test_network_errors_use_fallback(self):
        for error_type in (
            httpx.ReadTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.TooManyRedirects,
            httpx.RequestError,
        ):
            with self.subTest(error_type=error_type):
                def handler(request):
                    if request.url.host == "example.org":
                        raise error_type("network error", request=request)
                    return Response(200, headers={"content-type": "text/plain"}, text="recovered")

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    self.fetcher.client = client
                    self.assertEqual(
                        await self.fetcher._fetch_url("https://example.org"), "recovered"
                    )

    async def test_no_token_preserves_original_errors(self):
        for token in (None, "", "   "):
            for failure in (Response(403), httpx.ConnectError("offline")):
                with self.subTest(token=token, failure=failure):
                    requests = []

                    def handler(request):
                        requests.append(request)
                        if isinstance(failure, Exception):
                            raise failure
                        return failure

                    with patch.object(config.scrapdo, "token", token):
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                            self.fetcher.client = client
                            with self.assertRaises((httpx.HTTPStatusError, httpx.ConnectError)) as caught:
                                await self.fetcher._fetch_url("https://example.org")
                    self.assertEqual(len(requests), 1)
                    if isinstance(failure, Exception):
                        self.assertIs(caught.exception, failure)
                    else:
                        self.assertIs(caught.exception.response, failure)

    async def test_other_http_errors_do_not_use_fallback(self):
        for status in (404, 429, 500):
            with self.subTest(status=status):
                requests = []

                def handler(request):
                    requests.append(request)
                    return Response(status)

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    self.fetcher.client = client
                    with self.assertRaises(httpx.HTTPStatusError) as caught:
                        await self.fetcher._fetch_url("https://example.org")
                self.assertEqual(caught.exception.response.status_code, status)
                self.assertEqual(len(requests), 1)

    async def test_fallback_errors_use_httpx_exceptions_without_retry(self):
        for failure in (Response(401), Response(429), Response(500), httpx.ReadTimeout("timeout")):
            with self.subTest(failure=failure):
                requests = []

                def handler(request):
                    requests.append(request)
                    if request.url.host == "example.org":
                        return Response(403)
                    if isinstance(failure, Exception):
                        raise failure
                    return failure

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    self.fetcher.client = client
                    with self.assertRaises((httpx.HTTPStatusError, httpx.ReadTimeout)) as caught:
                        await self.fetcher._fetch_url("https://example.org")
                self.assertEqual(len(requests), 2)
                if isinstance(failure, Exception):
                    self.assertIs(caught.exception, failure)
                else:
                    self.assertIs(caught.exception.response, failure)
