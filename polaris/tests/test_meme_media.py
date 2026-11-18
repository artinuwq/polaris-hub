"""Polaris Meme: определение типа и размеров изображения по байтам, отказ на мусоре и ограничения Telegram."""
from __future__ import annotations

import io
import unittest
from unittest import mock

from polaris.modules.meme import media
from tests.meme_helpers import gif, jpeg, png, webp


class InspectImageTests(unittest.TestCase):
    def test_supported_formats_are_detected_by_content(self):
        cases = [
            (png(40, 30), "image/png", ".png", 40, 30),
            (jpeg(64, 48), "image/jpeg", ".jpg", 64, 48),
            (gif(20, 10), "image/gif", ".gif", 20, 10),
            (webp(32, 16), "image/webp", ".webp", 32, 16),
        ]
        for data, mime, ext, width, height in cases:
            info = media.inspect_image(data)
            self.assertEqual((info.mime, info.ext, info.width, info.height), (mime, ext, width, height), mime)

    def test_declared_type_is_irrelevant_only_bytes_count(self):
        with self.assertRaises(media.InvalidImage):
            media.inspect_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        with self.assertRaises(media.InvalidImage):
            media.inspect_image(b"MZ" + b"\x00" * 200)  # исполняемый файл с расширением .png

    def test_empty_and_oversized(self):
        with self.assertRaisesRegex(media.InvalidImage, "пустой"):
            media.inspect_image(b"")
        with mock.patch.object(media, "MAX_BYTES", 100):
            with self.assertRaisesRegex(media.InvalidImage, "больше"):
                media.inspect_image(png(40, 30))

    def test_truncated_files_are_rejected(self):
        for name, data in (("png", png()), ("jpeg", jpeg()), ("gif", gif()), ("webp", webp())):
            with self.assertRaises(media.InvalidImage, msg=name):
                media.inspect_image(data[:-12])

    def test_animated_webp_is_rejected_with_a_hint(self):
        with self.assertRaisesRegex(media.InvalidImage, "GIF"):
            media.inspect_image(webp(animated=True))

    def test_telegram_dimension_limits(self):
        with self.assertRaisesRegex(media.InvalidImage, "10000"):
            media.inspect_image(png(1, 1)[:16] + (6000).to_bytes(4, "big") + (5000).to_bytes(4, "big") + png(1, 1)[24:])
        with self.assertRaisesRegex(media.InvalidImage, "Соотношение"):
            media.inspect_image(jpeg(1000, 10))

    @unittest.skipUnless(__import__("importlib").util.find_spec("PIL"), "нужен Pillow только для сверки с настоящими файлами")
    def test_agrees_with_pillow_on_real_encoders(self):
        from PIL import Image

        image = Image.new("RGB", (123, 77), (200, 30, 90))
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")):
            buf = io.BytesIO()
            image.save(buf, fmt)
            info = media.inspect_image(buf.getvalue())
            self.assertEqual((info.mime, info.width, info.height), (mime, 123, 77), fmt)


if __name__ == "__main__":
    unittest.main()
