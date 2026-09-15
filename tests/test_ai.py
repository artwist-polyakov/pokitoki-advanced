import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


from bot.ai import chat, images
from bot.config import config
from bot.models import UserMessage


class ModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = chat.Model()

    def test_generate_messages(self):
        history = [UserMessage("Hello", "Hi"), UserMessage("Is it cold today?", "Yep!")]
        messages = self.model._generate_messages(
            prompt_role="system", prompt="", question="What's your name?", history=history
        )
        self.assertEqual(len(messages), 6)

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], config.openai.prompt)

        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hello")

        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Hi")

        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "Is it cold today?")

        self.assertEqual(messages[4]["role"], "assistant")
        self.assertEqual(messages[4]["content"], "Yep!")

        self.assertEqual(messages[5]["role"], "user")
        self.assertEqual(messages[5]["content"], "What's your name?")


class ShortenTest(unittest.TestCase):
    def test_do_not_shorten(self):
        messages = [
            {"role": "system", "content": "You are an AI assistant."},
            {"role": "user", "content": "Hello"},
        ]
        shortened = chat.shorten(messages, length=10)
        self.assertEqual(shortened, messages)

    def test_remove_messages_1(self):
        messages = [
            {"role": "system", "content": "You are an AI assistant."},
            {"role": "user", "content": "What is your name?"},
            {"role": "assistant", "content": "My name is Alice."},
            {"role": "user", "content": "Is it cold today?"},
        ]
        shortened = chat.shorten(messages, length=10)
        self.assertEqual(
            shortened,
            [
                {"role": "system", "content": "You are an AI assistant."},
                {"role": "user", "content": "Is it cold today?"},
            ],
        )

    def test_remove_messages_2(self):
        messages = [
            {"role": "system", "content": "You are an AI assistant."},
            {"role": "user", "content": "What is your name?"},
            {"role": "assistant", "content": "My name is Alice."},
            {"role": "user", "content": "Is it cold today?"},
        ]
        shortened = chat.shorten(messages, length=15)
        self.assertEqual(
            shortened,
            [
                {"role": "system", "content": "You are an AI assistant."},
                {"role": "assistant", "content": "My name is Alice."},
                {"role": "user", "content": "Is it cold today?"},
            ],
        )

    def test_shorten_question(self):
        messages = [
            {"role": "system", "content": "You are an AI assistant."},
            {"role": "user", "content": "Is it cold today? I think it's rather cold"},
        ]
        shortened = chat.shorten(messages, length=10)
        self.assertEqual(
            shortened,
            [
                {"role": "system", "content": "You are an AI assistant."},
                {"role": "user", "content": "Is it cold today?"},
            ],
        )


class ChatRequestTest(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, model, params, content=" Answer ", finish_reason="stop", refusal=None):
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content, refusal=refusal),
                finish_reason=finish_reason,
            )],
            usage=None,
        )
        with patch.object(config.openai, "params", params), patch.object(
            chat.openai.chat.completions, "create", AsyncMock(return_value=response)
        ) as create:
            answer = await chat.Model(model).ask("Prompt", "Question", [])
        return answer, create.call_args.kwargs

    async def test_reasoning_models_keep_limits_and_custom_parameters(self):
        for name in ("gpt-5", "gpt-5.1", "gpt-5.2", "gpt-5.4-mini", "gpt-5.4-nano",
                     "gpt-5.5", "gpt-5.6", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
                     "gpt-6-astra", "o3", "o4-mini", "openai/gpt-5.6-luna",
                     "gpt-5-2025-08-07"):
            with self.subTest(model=name):
                params = {"max_tokens": 8192, "temperature": 0.7, "top_p": 0.9,
                          "reasoning_effort": "low", "response_format": {"type": "json_object"}}
                original = params.copy()
                answer, request = await self._ask(name, params)
                self.assertEqual(answer, "Answer")
                self.assertEqual(request["model"], name)
                self.assertEqual(request["max_completion_tokens"], 8192)
                self.assertEqual(request["reasoning_effort"], "low")
                self.assertEqual(request["response_format"], {"type": "json_object"})
                self.assertNotIn("max_tokens", request)
                self.assertNotIn("temperature", request)
                self.assertNotIn("top_p", request)
                self.assertEqual(params, original)

    async def test_legacy_reasoning_models_preserve_request_compatibility(self):
        for name in ("o1-pro", "o4", "openai/o1-pro-2025-03-19"):
            with self.subTest(model=name):
                _, request = await self._ask(name, {"max_tokens": 4096, "temperature": 0.7})
                self.assertEqual(request["max_completion_tokens"], 4096)
                self.assertEqual(request["messages"][0]["role"], "user")
                self.assertNotIn("temperature", request)
                self.assertNotIn("max_tokens", request)
                self.assertEqual(chat._calc_n_input(name, 4096), 200000 - 4096)

    async def test_non_reasoning_and_unknown_providers_keep_parameters(self):
        for name in ("gpt-4o-mini", "gemini-3.8-flash", "custom-model"):
            with self.subTest(model=name):
                _, request = await self._ask(name, {"max_tokens": 512, "temperature": 0.7})
                self.assertEqual(request["max_tokens"], 512)
                self.assertEqual(request["temperature"], 0.7)
                self.assertNotIn("reasoning_effort", request)

    async def test_explicit_completion_budget_controls_history_truncation(self):
        with patch.object(chat, "shorten", wraps=chat.shorten) as shorten:
            _, request = await self._ask("gpt-5.2", {
                "max_tokens": 4096, "max_completion_tokens": 120000,
            })
        self.assertEqual(request["max_completion_tokens"], 120000)
        self.assertNotIn("max_tokens", request)
        self.assertEqual(shorten.call_args.kwargs["length"], 280000)

    async def test_astra_uses_supported_reasoning_effort(self):
        for effort in (None, "none", "minimal", "high"):
            with self.subTest(effort=effort):
                _, request = await self._ask("gpt-6-astra", {
                    "max_tokens": 4096, "reasoning_effort": effort,
                    "logprobs": True, "top_logprobs": 2,
                })
                self.assertEqual(request["reasoning_effort"], "high" if effort == "high" else "low")
                self.assertNotIn("logprobs", request)
                self.assertNotIn("top_logprobs", request)

    async def test_default_chat_model_uses_no_reasoning_for_low_latency(self):
        _, request = await self._ask("gpt-5.6-luna", {"max_tokens": 4096})
        self.assertEqual(request["reasoning_effort"], "none")

    async def test_empty_reasoning_answer_explains_output_limit(self):
        with self.assertRaisesRegex(ValueError, "increase max_tokens"):
            await self._ask("gpt-6-astra", {"max_tokens": 4096}, "", "length")

    async def test_refusal_is_reported(self):
        with self.assertRaisesRegex(ValueError, "Cannot help"):
            await self._ask("gpt-5.6-luna", {"max_tokens": 4096}, None, refusal="Cannot help")


class ModelLimitsTest(unittest.TestCase):
    def test_input_limits(self):
        self.assertEqual(chat._calc_n_input("gpt-5", 4096), 272000)
        self.assertEqual(chat._calc_n_input("gpt-6-astra", 4096), 922000)
        self.assertEqual(chat._calc_n_input("openai/gpt-5.6-luna", 4096), 922000)
        self.assertEqual(chat._calc_n_input("gpt-5-2025-08-07", 4096), 272000)
        self.assertEqual(chat._calc_n_input("gpt-4o-mini", 4096), 128000 - 4096)
        self.assertEqual(chat._calc_n_input("google/gemini-3.8-flash", 4096), 1048576)
        with patch.object(config.openai, "window", 32000):
            self.assertEqual(chat._calc_n_input("custom-model", 4096), 32000 - 4096)

    def test_invalid_output_budget(self):
        for budget in (0, -1, None, True, "4096", 500000):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                chat._calc_n_input("gpt-5", budget)


class ImageRequestTest(unittest.IsolatedAsyncioTestCase):
    async def _generate(self, data, model="gpt-image-2.5-flare", size="1024x1024"):
        with patch.object(config.openai, "image_model", model), patch.object(
            images.openai.images, "generate", AsyncMock(return_value=SimpleNamespace(data=data))
        ) as generate:
            result = await images.Model().imagine("A cat", size)
        return result, generate.call_args.kwargs

    async def test_gpt_image_decodes_bytes_and_maps_old_sizes(self):
        image = SimpleNamespace(b64_json=base64.b64encode(b"image bytes").decode(), url=None)
        for size in ("256x256", "512x512", "1024x1024", "1536x1024", "1024x1536"):
            with self.subTest(size=size):
                result, request = await self._generate([image], size=size)
                self.assertEqual(result, b"image bytes")
                self.assertEqual(request["model"], "gpt-image-2.5-flare")
                expected = "1024x1024" if size in ("256x256", "512x512") else size
                self.assertEqual(request["size"], expected)
                self.assertNotIn("response_format", request)

    async def test_legacy_landscape_and_portrait_sizes_for_gpt_image(self):
        image = SimpleNamespace(b64_json=base64.b64encode(b"image bytes").decode(), url=None)
        for model in ("gpt-image-1", "gpt-image-2.5-flare", "openai/gpt-image-2.5-flare"):
            for old, new in (("1792x1024", "1536x1024"), ("1024x1792", "1024x1536")):
                with self.subTest(model=model, size=old):
                    _, request = await self._generate([image], model=model, size=old)
                    self.assertEqual(request["size"], new)

    async def test_provider_url_response_remains_supported(self):
        image = SimpleNamespace(b64_json=None, url="https://example.org/cat.png")
        result, request = await self._generate([image], model="custom-image", size="512x512")
        self.assertEqual(result, image.url)
        self.assertEqual(request["size"], "512x512")

    async def test_missing_or_invalid_image_is_rejected(self):
        for data in (None, [], [SimpleNamespace(b64_json=None, url=None)],
                     [SimpleNamespace(b64_json="not base64!", url=None)]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                await self._generate(data)
