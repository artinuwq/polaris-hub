#!/usr/bin/env python3
"""Проверка ключа доступа, созданного в разделе Storage → Access Keys, настоящим AWS SDK (boto3).

    pip install boto3 requests
    # 1) в интерфейсе создайте bucket и ключ (ListBucket, ReadObject, WriteObject, DeleteObject, MultipartUpload),
    #    сохраните Access Key ID и Secret Access Key
    python scripts/s3_key_check.py check   --endpoint https://HOST/s3 --access-key ID --secret-key SECRET --bucket my-bucket
    # 2) в интерфейсе отзовите ключ (Revoke) и убедитесь, что он больше не работает
    python scripts/s3_key_check.py revoked --endpoint https://HOST/s3 --access-key ID --secret-key SECRET --bucket my-bucket

`check` ничего не создаёт вне указанного bucket-а (объекты с префиксом polaris-key-check/ удаляются в конце, если у ключа
есть DeleteObject). Параметры можно задать переменными S3_ENDPOINT, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

MIB = 1024 * 1024


def _base():
    spec = importlib.util.spec_from_file_location("s3_sdk_check", Path(__file__).with_name("s3_sdk_check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_key(endpoint, access_key, secret_key, region, bucket, *, legacy_checksums=False, can_delete=True,
              other_bucket=None, log=print):
    """Сценарий «сервис с выданным ключом»: put/get/head/list/Range, multipart upload_file/download_file, presigned."""
    import requests
    from boto3.s3.transfer import TransferConfig
    from botocore.exceptions import ClientError

    base = _base()
    s3 = base.build_client(endpoint, access_key, secret_key, region, legacy_checksums=legacy_checksums)
    prefix = f"polaris-key-check/{uuid.uuid4().hex[:8]}/"
    results: list[tuple[str, bool, str]] = []
    keys: list[str] = []

    def step(name):
        def deco(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                results.append((name, False, detail))
                log(f"FAIL  {name}: {detail}")
            else:
                results.append((name, True, ""))
                log(f"ok    {name}")
            return fn
        return deco

    def expect(cond, message="проверка не выполнена"):
        if not cond:
            raise AssertionError(message)

    @step("put_object / get_object / head_object")
    def _():
        body = b"hello from a key created in the Polaris UI"
        keys.append(prefix + "small.txt")
        put = s3.put_object(Bucket=bucket, Key=keys[-1], Body=body, ContentType="text/plain", Metadata={"via": "ui-key"})
        expect(put["ETag"] == f'"{hashlib.md5(body).hexdigest()}"', "ETag")
        got = s3.get_object(Bucket=bucket, Key=keys[-1])
        expect(got["Body"].read() == body and got["Metadata"] == {"via": "ui-key"}, "тело/метаданные")
        expect(s3.head_object(Bucket=bucket, Key=keys[-1])["ContentLength"] == len(body), "HEAD")

    @step("list_objects_v2 и Range")
    def _():
        listed = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix)["Contents"]]
        expect(listed == [prefix + "small.txt"], f"листинг: {listed}")
        got = s3.get_object(Bucket=bucket, Key=prefix + "small.txt", Range="bytes=6-9")
        expect(got["Body"].read() == b"from" and got["ResponseMetadata"]["HTTPStatusCode"] == 206, "Range")

    @step("upload_file (multipart, 11 MiB) и download_file")
    def _():
        data = os.urandom(11 * MIB + 5)
        cfg = TransferConfig(multipart_threshold=5 * MIB, multipart_chunksize=5 * MIB, max_concurrency=3)
        keys.append(prefix + "multipart.bin")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
            Path(src).write_bytes(data)
            s3.upload_file(src, bucket, keys[-1], Config=cfg)
            expect(s3.head_object(Bucket=bucket, Key=keys[-1])["ETag"].strip('"').endswith("-3"), "ETag multipart")
            s3.download_file(bucket, keys[-1], dst, Config=cfg)
            expect(hashlib.sha256(Path(dst).read_bytes()).digest() == hashlib.sha256(data).digest(), "содержимое")

    @step("presigned GET и PUT")
    def _():
        keys.append(prefix + "presigned.bin")
        url = s3.generate_presigned_url("put_object", Params={"Bucket": bucket, "Key": keys[-1]}, ExpiresIn=300)
        expect(secret_key not in url, "секрет в URL")
        expect(requests.put(url, data=b"presigned").status_code == 200, "PUT по presigned URL")
        got = requests.get(s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": keys[-1]}, ExpiresIn=300))
        expect(got.status_code == 200 and got.content == b"presigned", "GET по presigned URL")

    if other_bucket:
        @step(f"доступ к чужому bucket «{other_bucket}» запрещён")
        def _():
            for call in (lambda: s3.list_objects_v2(Bucket=other_bucket), lambda: s3.put_object(Bucket=other_bucket, Key="x", Body=b"x")):
                try:
                    call()
                except ClientError as exc:
                    expect(base.error_code(exc) == "AccessDenied", f"код {base.error_code(exc)}")
                else:
                    raise AssertionError("доступ к чужому bucket-у не запрещён")

    @step("создание bucket-ов этим ключом запрещено")
    def _():
        try:
            s3.create_bucket(Bucket="polaris-key-check-" + uuid.uuid4().hex[:6])
        except ClientError as exc:
            expect(base.error_code(exc) == "AccessDenied", f"код {base.error_code(exc)}")
        else:
            raise AssertionError("ключ смог создать bucket")

    if can_delete:
        @step("очистка: delete_object")
        def _():
            for key in keys:
                s3.delete_object(Bucket=bucket, Key=key)
    return results


def check_revoked(endpoint, access_key, secret_key, region, bucket, *, legacy_checksums=False, log=print):
    """После Revoke ключ должен немедленно отвергаться всеми операциями."""
    from botocore.exceptions import ClientError

    base = _base()
    s3 = base.build_client(endpoint, access_key, secret_key, region, legacy_checksums=legacy_checksums)
    results = []
    for name, call in (("list_objects_v2", lambda: s3.list_objects_v2(Bucket=bucket)),
                       ("put_object", lambda: s3.put_object(Bucket=bucket, Key="revoked-check", Body=b"x")),
                       ("list_buckets", s3.list_buckets)):
        try:
            call()
        except ClientError as exc:
            code = base.error_code(exc)
            ok = code == "InvalidAccessKeyId"
            results.append((f"{name}: {code}", ok, "" if ok else "ожидался InvalidAccessKeyId"))
        else:
            results.append((name, False, "отозванный ключ всё ещё работает"))
        log(("ok    " if results[-1][1] else "FAIL  ") + results[-1][0])
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["check", "revoked"])
    parser.add_argument("--endpoint", default=os.getenv("S3_ENDPOINT", "http://127.0.0.1:8000/s3"))
    parser.add_argument("--access-key", default=os.getenv("S3_ACCESS_KEY_ID"))
    parser.add_argument("--secret-key", default=os.getenv("S3_SECRET_ACCESS_KEY"))
    parser.add_argument("--region", default=os.getenv("S3_REGION", "us-east-1"))
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--other-bucket", help="bucket, к которому у ключа доступа быть НЕ должно")
    parser.add_argument("--no-delete", action="store_true", help="у ключа нет DeleteObject: не очищать")
    parser.add_argument("--legacy-checksums", action="store_true")
    args = parser.parse_args()
    if not args.access_key or not args.secret_key:
        parser.error("нужны --access-key и --secret-key")
    try:
        import boto3  # noqa: F401
    except ImportError:
        print("Нужен boto3: pip install boto3 requests", file=sys.stderr)
        return 2
    if args.mode == "check":
        results = check_key(args.endpoint, args.access_key, args.secret_key, args.region, args.bucket,
                            legacy_checksums=args.legacy_checksums, can_delete=not args.no_delete, other_bucket=args.other_bucket)
    else:
        results = check_revoked(args.endpoint, args.access_key, args.secret_key, args.region, args.bucket,
                                legacy_checksums=args.legacy_checksums)
    failed = [r for r in results if not r[1]]
    print(f"\nИтого: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
