import os
import unittest
from unittest.mock import AsyncMock, patch

# Ensure config loads example file
os.environ.setdefault("CONFIG", "config.example.yml")

from telegram import Document, PhotoSize

from bot.file_processor import FileProcessor


class _DummyFile:
    async def download_to_drive(self, path, *args, **kwargs):
        with open(path, "wb") as f:
            f.write(b"data")


class _DummyResult:
    def __init__(self, text):
        self.text_content = text


class FileProcessorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor = FileProcessor()
        # patch markdown convert method
        self.processor.md.convert = lambda *a, **kw: _DummyResult("text")
        self.doc_patch = patch.object(Document, "get_file", AsyncMock(return_value=_DummyFile()))
        self.photo_patch = patch.object(PhotoSize, "get_file", AsyncMock(return_value=_DummyFile()))
        self.doc_patch.start()
        self.photo_patch.start()

    def tearDown(self):
        self.doc_patch.stop()
        self.photo_patch.stop()

    async def test_unsupported_extension(self):
        doc = Document(
            file_id="f1",
            file_unique_id="u1",
            file_name="file.exe",
            file_size=10,
        )
        result = await self.processor.process_files([doc], [])
        self.assertIsNone(result)

    async def test_oversized_file(self):
        big_size = self.processor.max_file_size + 1
        doc = Document(
            file_id="f2",
            file_unique_id="u2",
            file_name="file.txt",
            file_size=big_size,
        )
        result = await self.processor.process_files([doc], [])
        self.assertIsNone(result)

        photo = PhotoSize(
            file_id="p1",
            file_unique_id="pu1",
            width=100,
            height=100,
            file_size=big_size,
        )
        result = await self.processor.process_files([], [photo])
        self.assertIsNone(result)

    async def test_success(self):
        doc = Document(
            file_id="f3",
            file_unique_id="u3",
            file_name="file.txt",
            file_size=10,
        )
        result = await self.processor.process_files([doc], [])
        self.assertEqual(result, "<file_file.txt>text</file_file.txt>")

        photo = PhotoSize(
            file_id="p2",
            file_unique_id="pu2",
            width=10,
            height=10,
            file_size=10,
        )
        result = await self.processor.process_files([], [photo])
        self.assertEqual(result, "<file_image_pu2>text</file_image_pu2>")

    async def test_multiple_photos(self):
        photo1 = PhotoSize(
            file_id="p3",
            file_unique_id="pu3",
            width=10,
            height=10,
            file_size=10,
        )
        photo2 = PhotoSize(
            file_id="p4",
            file_unique_id="pu4",
            width=20,
            height=20,
            file_size=20,
        )
        result = await self.processor.process_files([], [photo1, photo2])
        self.assertIn("<file_image_pu3>text</file_image_pu3>", result)
        self.assertIn("<file_image_pu4>text</file_image_pu4>", result)

