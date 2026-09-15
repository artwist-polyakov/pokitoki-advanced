"""Exercise the real SDK's request serialization without external API calls."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2
from openai import AsyncOpenAI

from bot.ai import chat, images
from bot.config import config
from bot.voice import VoiceProcessor


class APIRequestTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if request.url.path.endswith("/chat/completions"):
                return httpx2.Response(200, json={
                    "id": "test", "object": "chat.completion", "created": 0,
                    "model": "gpt-5.6-luna",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "Hello"}}],
                })
            if request.url.path.endswith("/images/generations"):
                return httpx2.Response(200, json={
                    "created": 0,
                    "data": [{"b64_json": base64.b64encode(b"image bytes").decode()}],
                })
            if request.url.path.endswith("/audio/transcriptions"):
                return httpx2.Response(200, json={"text": "Hello from voice"})
            if request.url.path.endswith("/audio/speech"):
                return httpx2.Response(200, content=b"audio bytes")
            raise AssertionError(f"Unexpected API request: {request.url.path}")

        self.client = AsyncOpenAI(
            api_key="test-key", base_url="https://provider.example/v1",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        self.addAsyncCleanup(self.client.close)

    async def test_chat_request_uses_provider_and_preserves_model_id(self):
        with patch.object(chat, "openai", self.client), patch.object(
            config.openai, "params", {"max_tokens": 4096, "temperature": 0.7}
        ):
            answer = await chat.Model("openai/gpt-5.6-luna").ask("Be helpful", "Hi", [])
        self.assertEqual(answer, "Hello")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://provider.example/v1/chat/completions")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "openai/gpt-5.6-luna")
        self.assertEqual(body["max_completion_tokens"], 4096)
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("temperature", body)
        self.assertNotIn("max_tokens", body)

    async def test_image_response_decodes_with_real_sdk(self):
        with patch.object(images, "openai", self.client), patch.object(
            config.openai, "image_model", "gpt-image-2.5-flare"
        ):
            result = await images.Model().imagine("A cat", "1024x1536")
        self.assertEqual(result, b"image bytes")
        self.assertEqual(json.loads(self.requests[0].content), {
            "model": "gpt-image-2.5-flare", "prompt": "A cat", "size": "1024x1536", "n": 1,
        })

    async def test_voice_models_work_with_updated_sdk(self):
        with patch("bot.voice.AsyncOpenAI", return_value=self.client), patch.object(
            config.voice, "model", "gpt-4o-mini-transcribe"
        ), patch.object(config.voice, "tts", {"model": "gpt-4o-mini-tts", "voice": "alloy"}):
            processor = VoiceProcessor()
            with tempfile.TemporaryDirectory() as directory:
                voice_file = Path(directory) / "voice.ogg"
                voice_file.write_bytes(b"audio bytes")
                self.assertEqual(await processor.transcribe(voice_file), "Hello from voice")
            speech_file = await processor.text_to_speech("Hello")
            self.assertIsNotNone(speech_file)
            try:
                self.assertEqual(speech_file.read_bytes(), b"audio bytes")
            finally:
                speech_file.unlink()
        self.assertIn(b"gpt-4o-mini-transcribe", self.requests[0].content)
        self.assertEqual(json.loads(self.requests[1].content)["model"], "gpt-4o-mini-tts")
