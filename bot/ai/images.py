"""Image generation through an OpenAI-compatible API."""

import base64

from openai import AsyncOpenAI

from bot.config import config

openai = AsyncOpenAI(api_key=config.openai.api_key, base_url=config.openai.url)


class Model:
    """Returns generated image bytes or a provider's image URL."""

    async def imagine(self, prompt: str, size: str) -> bytes | str:
        """Generates an image and decodes GPT Image's base64 response."""
        model = config.openai.image_model
        if model.removeprefix("openai/").startswith("gpt-image-"):
            # Keep old square-size shortcuts usable with GPT Image.
            size = {"256x256": "1024x1024", "512x512": "1024x1024"}.get(size, size)
        resp = await openai.images.generate(model=model, prompt=prompt, size=size, n=1)
        if not resp.data:
            raise ValueError("missing image data")
        image = resp.data[0]
        if image.b64_json:
            return base64.b64decode(image.b64_json, validate=True)
        if image.url:
            return image.url
        raise ValueError("missing image data")
