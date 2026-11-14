"""Совместимость с настоящим AWS SDK (boto3/botocore) против локального Polaris S3 endpoint.

Пропускается, если boto3 не установлен. Те же проверки можно запустить против живого экземпляра:
python scripts/s3_sdk_check.py --endpoint http://HOST:PORT/s3 --access-key ... --secret-key ..."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests.s3_client import AK, SK
from tests.test_s3_api import S3TestCase

try:
    import boto3  # noqa: F401
    import botocore  # noqa: F401
except ImportError:
    boto3 = None

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "s3_sdk_check.py"


def load_checks():
    spec = importlib.util.spec_from_file_location("s3_sdk_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(boto3 is None, "boto3 не установлен: pip install boto3")
class Boto3CompatibilityTests(S3TestCase):
    def run_variant(self, **kwargs):
        checks = load_checks()
        try:
            results = checks.run_all(self.server.url + "/s3", AK, SK, "us-east-1", log=lambda *_: None, **kwargs)
        except checks.SkipVariant as exc:
            self.skipTest(str(exc))
        failed = [f"{name}: {detail}" for name, ok, detail in results if not ok]
        self.assertEqual(failed, [], "\n".join(failed))
        self.assertGreaterEqual(len(results), 14)

    def test_default_sdk_behaviour(self):
        self.run_variant()

    def test_sdk_without_automatic_checksums(self):
        self.run_variant(legacy_checksums=True)


if __name__ == "__main__":
    unittest.main()
