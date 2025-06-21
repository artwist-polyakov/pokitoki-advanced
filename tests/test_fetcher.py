import unittest

from httpx import Request, Response

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
