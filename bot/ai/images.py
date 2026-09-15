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
        model_name = model.removeprefix("openai/")
        if model_name.startswith("gpt-image-"):
            # Keep old DALL-E dimensions usable across GPT Image models.
            size = {
                "256x256": "1024x1024",
                "512x512": "1024x1024",
                "1792x1024": "1536x1024",
                "1024x1792": "1024x1536",
            }.get(size, size)
        elif model_name == "dall-e-3":
            size = {
                "256x256": "1024x1024",
                "512x512": "1024x1024",
                "1536x1024": "1792x1024",
                "1024x1536": "1024x1792",
            }.get(size, size)
        elif model_name == "dall-e-2" and size not in {"256x256", "512x512", "1024x1024"}:
            size = "1024x1024"
        resp = await openai.images.generate(model=model, prompt=prompt, size=size, n=1)
        if not resp.data:
            raise ValueError("missing image data")
        image = resp.data[0]
        if image.b64_json:
            return base64.b64decode(image.b64_json, validate=True)
        if image.url:
            return image.url
        raise ValueError("missing image data")
