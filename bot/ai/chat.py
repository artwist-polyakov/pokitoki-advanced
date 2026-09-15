"""Chat Completions client for OpenAI-compatible providers."""

import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from bot.config import config

openai = AsyncOpenAI(api_key=config.openai.api_key, base_url=config.openai.url)

logger = logging.getLogger(__name__)

# Verified 2026-09-15 against https://developers.openai.com/api/docs/models
# and https://ai.google.dev/gemini-api/docs/models.
# OpenAI values are context windows; Gemini values are input limits.
MODELS = {
    "gemini-3.8-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3.1-pro-preview": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gpt-6-astra": 1_050_000,
    "gpt-5.6": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "gpt-5.5": 1_050_000,
    "gpt-5.4": 1_050_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.4-nano": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.1": 400_000,
    "gpt-5": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    # Legacy entries remain for compatible providers and existing configurations.
    "o1-pro": 200_000,
    "o4": 200_000,
    "gemini-2.0-flash": 1_048_576,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.5-flash-8b": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gpt-4-turbo": 128_000,
    "gpt-4-turbo-preview": 128_000,
    "gpt-4-vision-preview": 128_000,
    "gpt-4": 8_192,
    "gpt-4-32k": 32_768,
    "gpt-3.5-turbo": 16_385,
}

# Some models have a separate input ceiling in addition to the context window.
INPUT_LIMITS = {
    "gpt-5": 272_000,
    "gpt-5-mini": 272_000,
    "gpt-5-nano": 272_000,
    "gpt-5.4-mini": 272_000,
    "gpt-5.4-nano": 272_000,
    "gpt-5.6": 922_000,
    "gpt-5.6-sol": 922_000,
    "gpt-5.6-terra": 922_000,
    "gpt-5.6-luna": 922_000,
    "gpt-6-astra": 922_000,
}


def _model_name(name: str) -> str:
    """Resolves provider prefixes and dated snapshots for local metadata only."""
    for prefix in ("openai/", "google/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)


def _prepare_params(name: str, params: dict) -> dict:
    """Keeps the output budget and adapts parameters for reasoning models."""
    name = _model_name(name)
    result = params.copy()
    if name not in MODELS or not name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        # An explicitly configured modern limit takes precedence over the default.
        if "max_completion_tokens" in result:
            result.pop("max_tokens", None)
        return result

    max_tokens = result.pop("max_tokens", None)
    if "max_completion_tokens" not in result and max_tokens is not None:
        result["max_completion_tokens"] = max_tokens
    # These controls are not portable across reasoning models/effort levels.
    for key in ("temperature", "top_p", "logprobs", "top_logprobs",
                "presence_penalty", "frequency_penalty"):
        result.pop(key, None)
    if name.startswith("gpt-5."):
        result.setdefault("reasoning_effort", "none")
    elif name == "gpt-6-astra" and result.get("reasoning_effort") in (None, "none", "minimal"):
        result["reasoning_effort"] = "low"
    return result


class Model:
    """OpenAI API wrapper."""

    def __init__(self, name: Optional[str] = None) -> None:
        """Creates a wrapper for a given OpenAI large language model."""
        self.name = name

    async def ask(
        self, prompt: str, question: str, history: list[tuple[str, str]]
    ) -> str:
        """Asks the language model a question and returns an answer."""
        model = self.name or config.openai.model
        prompt_role = (
            "user" if _model_name(model) in {
                "o1", "o1-pro", "o1-mini", "o3", "o3-mini", "o4", "o4-mini"
            }
            else "system"
        )
        params = _prepare_params(model, config.openai.params)
        n_output = params.get("max_completion_tokens", params.get("max_tokens", 4096))
        n_input = _calc_n_input(model, n_output=n_output)
        messages = self._generate_messages(prompt_role, prompt, question, history)
        messages = shorten(messages, length=n_input)

        logger.debug(
            "> chat request: model=%s, params=%s, messages=%s",
            model,
            params,
            messages,
        )
        resp = await openai.chat.completions.create(
            model=model,
            messages=messages,
            **params,
        )
        if resp.usage is not None:
            logger.debug("< chat response: usage=%s", resp.usage)
        answer = self._prepare_answer(resp)
        return answer

    def _generate_messages(
        self,
        prompt_role: str,
        prompt: str,
        question: str,
        history: list[tuple[str, str]],
    ) -> list[dict]:
        """Builds message history to provide context for the language model."""
        messages = [{"role": prompt_role, "content": prompt or config.openai.prompt}]
        for prev_question, prev_answer in history:
            messages.append({"role": "user", "content": prev_question})
            messages.append({"role": "assistant", "content": prev_answer})
        messages.append({"role": "user", "content": question})
        return messages

    def _prepare_answer(self, resp) -> str:
        """Post-processes an answer from the language model."""
        if len(resp.choices) == 0:
            raise ValueError("received an empty answer")

        choice = resp.choices[0]
        answer = choice.message.content
        if not answer or not answer.strip():
            if choice.finish_reason == "length":
                raise ValueError(
                    "output limit reached; increase max_tokens or lower reasoning_effort"
                )
            raise ValueError(choice.message.refusal or "received an empty answer")
        return answer.strip()


def _calc_tokens(s: str) -> int:
    """Calculates the number of tokens in a string."""
    return int(len(s.split()) * 1.2)


def shorten(messages: list[dict], length: int) -> list[dict]:
    """
    Truncates messages so that the total number or tokens
    does not exceed the specified length.
    """
    lengths = [_calc_tokens(m["content"]) for m in messages]
    total_len = sum(lengths)
    if total_len <= length:
        return messages

    # exclude older messages to fit into the desired length
    # can't exclude the prompt though
    prompt_msg, messages = messages[0], messages[1:]
    prompt_len, lengths = lengths[0], lengths[1:]
    while len(messages) > 1 and total_len > length:
        messages = messages[1:]
        first_len, lengths = lengths[0], lengths[1:]
        total_len -= first_len
    messages = [prompt_msg] + messages
    if total_len <= length:
        return messages

    # there is only one message left, and it's still longer than allowed
    # so we have to shorten it
    maxlen = length - prompt_len
    tokens = messages[1]["content"].split()[:maxlen]
    messages[1]["content"] = " ".join(tokens)
    return messages


def _calc_n_input(name: str, n_output: int) -> int:
    """
    Calculates the maximum number of input tokens
    according to the model and the maximum number of output tokens.
    """
    # OpenAI counts length in tokens, not characters.
    # We need to leave some tokens reserved for the output.
    name = _model_name(name)
    n_total = MODELS.get(name) or config.openai.window
    if not isinstance(n_output, int) or isinstance(n_output, bool) or n_output <= 0:
        raise ValueError("output token limit must be a positive integer")
    if name.startswith("gemini-") and name in MODELS:
        return n_total
    n_input = min(n_total - n_output, INPUT_LIMITS.get(name, n_total))
    if n_input <= 0:
        raise ValueError("output token limit must be smaller than the context window")
    return n_input
