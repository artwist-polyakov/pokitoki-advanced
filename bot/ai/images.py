"""DALL-E model from OpenAI."""

from openai import AsyncOpenAI

from bot.config import config

openai = AsyncOpenAI(api_key=config.openai.api_key, base_url=config.openai.url)


class Model:
    """OpenAI DALL-E wrapper."""

    async def imagine(self, prompt: str, size: str) -> str:
        """Generates an image of the specified size according to the description."""
        resp = await openai.images.generate(
            model=config.openai.image_model, prompt=prompt, size=size, n=1
        )
        if not getattr(resp, "data", None):
            raise Exception("missing image data")
        return resp.data[0].url
