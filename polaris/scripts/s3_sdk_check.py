#!/usr/bin/env python3
"""Проверка S3-совместимости Polaris настоящим AWS SDK (boto3/botocore).

Запуск против работающего Polaris:

    pip install boto3 requests
    python scripts/s3_sdk_check.py --endpoint http://127.0.0.1:8000/s3 \
        --access-key AKID --secret-key SECRET [--region us-east-1] [--keep]

Параметры можно задать переменными S3_ENDPOINT, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION.
Создаёт временный bucket polaris-check-<hex>, прогоняет сценарии и удаляет всё за собой (если не --keep).
Код возврата 0 — все проверки пройдены. Этот же модуль использует tests/test_s3_boto3.py.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tempfile
import traceback
import uuid

MIB = 1024 * 1024


class SkipVariant(Exception):
    pass


def build_client(endpoint: str, access_key: str, secret_key: str, region: str, *, legacy_checksums: bool = False):
    import boto3
    from botocore.config import Config

    kwargs = {}
    if legacy_checksums:  # старое поведение SDK: без автоматических CRC32/trailer-ов
        kwargs.update(request_checksum_calculation="when_required", response_checksum_validation="when_required")
    try:
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}, **kwargs)
    except TypeError:
        if legacy_checksums:
            raise SkipVariant("эта версия botocore не знает request_checksum_calculation") from None
        raise
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                        region_name=region, config=config)


def error_code(exc) -> str:
    return exc.response.get("Error", {}).get("Code", "")


def run_all(endpoint: str, access_key: str, secret_key: str, region: str = "us-east-1", *, keep: bool = False,
            legacy_checksums: bool = False, log=print) -> list[tuple[str, bool, str]]:
    import requests
    from boto3.s3.transfer import TransferConfig
    from botocore.exceptions import ClientError

    s3 = build_client(endpoint, access_key, secret_key, region, legacy_checksums=legacy_checksums)
    bucket = "polaris-check-" + uuid.uuid4().hex[:10]
    results: list[tuple[str, bool, str]] = []
    created: list[str] = []

    def check(name):
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

    def expect(condition, message="проверка не выполнена"):
        if not condition:
            raise AssertionError(message)

    def expect_error(code, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except ClientError as exc:
            expect(error_code(exc) == code, f"ожидался код {code}, получен {error_code(exc)}")
        else:
            raise AssertionError(f"ожидалась ошибка {code}")

    @check("1. create_bucket / list_buckets / head_bucket")
    def _():
        s3.create_bucket(Bucket=bucket)
        created.append(bucket)
        expect(bucket in [b["Name"] for b in s3.list_buckets()["Buckets"]])
        s3.head_bucket(Bucket=bucket)
        expect_error("BucketAlreadyOwnedByYou", s3.create_bucket, Bucket=bucket)

    @check("2. put_object / get_object (Content-Type, Metadata, ETag)")
    def _():
        body = b"hello polaris"
        put = s3.put_object(Bucket=bucket, Key="small.txt", Body=body, ContentType="text/plain", Metadata={"origin": "check"})
        expect(put["ETag"] == f'"{hashlib.md5(body).hexdigest()}"', "ETag")
        got = s3.get_object(Bucket=bucket, Key="small.txt")
        expect(got["Body"].read() == body, "тело")
        expect(got["ContentType"] == "text/plain" and got["Metadata"] == {"origin": "check"}, "заголовки/метаданные")
        expect(got["ContentLength"] == len(body) and "LastModified" in got, "размер/дата")

    @check("3. head_object")
    def _():
        head = s3.head_object(Bucket=bucket, Key="small.txt")
        expect(head["ContentLength"] == 13 and head["ETag"].startswith('"'), "HEAD")
        expect_error("404", s3.head_object, Bucket=bucket, Key="missing")

    @check("4. list_objects_v2: prefix, delimiter, пагинация")
    def _():
        for key in ("dir/a.txt", "dir/b.txt", "dir/sub/c.txt", "top.txt"):
            s3.put_object(Bucket=bucket, Key=key, Body=key.encode())
        listing = s3.list_objects_v2(Bucket=bucket, Delimiter="/")
        expect(sorted(o["Key"] for o in listing["Contents"]) == ["small.txt", "top.txt"], "Contents")
        expect([p["Prefix"] for p in listing["CommonPrefixes"]] == ["dir/"], "CommonPrefixes")
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix="dir/")["Contents"]]
        expect(keys == ["dir/a.txt", "dir/b.txt", "dir/sub/c.txt"], f"Prefix: {keys}")
        paged = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, PaginationConfig={"PageSize": 2}):
            paged += [o["Key"] for o in page.get("Contents", [])]
        expect(paged == sorted(paged) and len(paged) == 5, f"пагинация: {paged}")
        after = s3.list_objects_v2(Bucket=bucket, StartAfter="dir/b.txt")["Contents"]
        expect(after[0]["Key"] == "dir/sub/c.txt", "StartAfter")

    @check("5. Range-запрос (206, Content-Range)")
    def _():
        got = s3.get_object(Bucket=bucket, Key="small.txt", Range="bytes=6-11")
        expect(got["Body"].read() == b"polari" and got["ContentRange"] == "bytes 6-11/13", "Range")
        expect(got["ResponseMetadata"]["HTTPStatusCode"] == 206, "статус 206")
        expect_error("InvalidRange", s3.get_object, Bucket=bucket, Key="small.txt", Range="bytes=100-200")

    @check("6. Unicode-ключи и пробелы")
    def _():
        key = "папка/файл с пробелом+плюс.txt"
        s3.put_object(Bucket=bucket, Key=key, Body=b"u")
        expect(s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"u", "get")
        expect(key in [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix="папка/")["Contents"]], "list")
        s3.delete_object(Bucket=bucket, Key=key)

    @check("7. пустой объект и перезапись")
    def _():
        s3.put_object(Bucket=bucket, Key="empty", Body=b"")
        expect(s3.get_object(Bucket=bucket, Key="empty")["Body"].read() == b"", "empty")
        s3.put_object(Bucket=bucket, Key="over", Body=b"one")
        s3.put_object(Bucket=bucket, Key="over", Body=b"two")
        expect(s3.get_object(Bucket=bucket, Key="over")["Body"].read() == b"two", "overwrite")

    @check("8. upload_file: multipart (12 MiB), download_file, upload_fileobj")
    def _():
        data = os.urandom(12 * MIB + 321)
        cfg = TransferConfig(multipart_threshold=5 * MIB, multipart_chunksize=5 * MIB, max_concurrency=4)
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "src.bin"), os.path.join(tmp, "dst.bin")
            with open(src, "wb") as f:
                f.write(data)
            s3.upload_file(src, bucket, "big/multipart.bin", Config=cfg, ExtraArgs={"ContentType": "application/x-test"})
            head = s3.head_object(Bucket=bucket, Key="big/multipart.bin")
            expect(head["ContentLength"] == len(data) and head["ETag"].strip('"').endswith("-3"), f"HEAD: {head['ETag']}")
            expect(head["ContentType"] == "application/x-test", "ContentType")
            s3.download_file(bucket, "big/multipart.bin", dst, Config=cfg)
            with open(dst, "rb") as f:
                expect(hashlib.sha256(f.read()).digest() == hashlib.sha256(data).digest(), "содержимое после download_file")
        s3.upload_fileobj(io.BytesIO(data[:6 * MIB]), bucket, "big/fileobj.bin", Config=cfg)
        expect(s3.get_object(Bucket=bucket, Key="big/fileobj.bin")["ContentLength"] == 6 * MIB, "upload_fileobj")

    @check("9. presigned GET и PUT (без секрета в запросе)")
    def _():
        put_url = s3.generate_presigned_url("put_object", Params={"Bucket": bucket, "Key": "presigned/up.bin"}, ExpiresIn=300)
        expect(secret_key not in put_url, "секрет в URL")
        r = requests.put(put_url, data=b"via presigned")
        expect(r.status_code == 200, f"PUT: {r.status_code} {r.text[:200]}")
        get_url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "presigned/up.bin"}, ExpiresIn=300)
        r = requests.get(get_url)
        expect(r.status_code == 200 and r.content == b"via presigned", f"GET: {r.status_code}")
        bad = requests.get(get_url.replace("up.bin", "other.bin"))
        expect(bad.status_code == 403, "подмена ключа должна давать 403")

    @check("10. неверные credentials")
    def _():
        wrong_secret = build_client(endpoint, access_key, secret_key + "x", region, legacy_checksums=legacy_checksums)
        expect_error("SignatureDoesNotMatch", wrong_secret.list_buckets)
        wrong_id = build_client(endpoint, access_key + "X", secret_key, region, legacy_checksums=legacy_checksums)
        expect_error("InvalidAccessKeyId", wrong_id.list_buckets)

    @check("11. delete_objects и delete_object")
    def _():
        out = s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": "dir/a.txt"}, {"Key": "dir/b.txt"}, {"Key": "nope"}]})
        expect(len(out.get("Deleted", [])) == 3 and not out.get("Errors"), f"DeleteObjects: {out}")
        s3.delete_object(Bucket=bucket, Key="top.txt")
        expect_error("NoSuchKey", s3.get_object, Bucket=bucket, Key="top.txt")

    @check("12. multipart вручную: create/upload_part/list_parts/abort")
    def _():
        up = s3.create_multipart_upload(Bucket=bucket, Key="manual.bin")
        uid = up["UploadId"]
        s3.upload_part(Bucket=bucket, Key="manual.bin", UploadId=uid, PartNumber=1, Body=os.urandom(5 * MIB))
        parts = s3.list_parts(Bucket=bucket, Key="manual.bin", UploadId=uid)["Parts"]
        expect([p["PartNumber"] for p in parts] == [1], "list_parts")
        expect(any(u["UploadId"] == uid for u in s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])), "list_multipart_uploads")
        s3.abort_multipart_upload(Bucket=bucket, Key="manual.bin", UploadId=uid)
        expect(not s3.list_multipart_uploads(Bucket=bucket).get("Uploads"), "после abort загрузок нет")
        expect_error("NoSuchUpload", s3.abort_multipart_upload, Bucket=bucket, Key="manual.bin", UploadId=uid)

    @check("13. NoSuchBucket / BucketNotEmpty")
    def _():
        expect_error("NoSuchBucket", s3.list_objects_v2, Bucket="polaris-check-absent-bucket")
        expect_error("BucketNotEmpty", s3.delete_bucket, Bucket=bucket)

    if not keep and created:
        @check("14. очистка и delete_bucket")
        def _():
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=obj["Key"])
            s3.delete_bucket(Bucket=bucket)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default=os.getenv("S3_ENDPOINT", "http://127.0.0.1:8000/s3"))
    parser.add_argument("--access-key", default=os.getenv("S3_ACCESS_KEY_ID"))
    parser.add_argument("--secret-key", default=os.getenv("S3_SECRET_ACCESS_KEY"))
    parser.add_argument("--region", default=os.getenv("S3_REGION", "us-east-1"))
    parser.add_argument("--keep", action="store_true", help="не удалять тестовый bucket")
    parser.add_argument("--legacy-checksums", action="store_true", help="отключить автоматические контрольные суммы SDK")
    args = parser.parse_args()
    if not args.access_key or not args.secret_key:
        parser.error("нужны --access-key и --secret-key (или S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY)")
    try:
        import boto3  # noqa: F401
    except ImportError:
        print("Нужен boto3: pip install boto3 requests", file=sys.stderr)
        return 2
    results = run_all(args.endpoint, args.access_key, args.secret_key, args.region, keep=args.keep,
                      legacy_checksums=args.legacy_checksums)
    failed = [r for r in results if not r[1]]
    print(f"\nИтого: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
