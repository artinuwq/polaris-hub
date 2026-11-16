"""Unit-тесты S3: Range, кодирование ключей, XML, токены, заголовки, конфигурация, сопоставление ошибок."""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from polaris.infra.settings import Settings
from polaris.s3 import create_s3_app, errors, util, xmlio
from polaris.s3.config import S3Config
from polaris.s3.credentials import Credential, StaticCredentialProvider
from polaris.s3.service import (
    BodyIntegrity, decode_token, encode_token, map_exception, parse_object_headers,
)
from polaris.storage import errors as se
from polaris.storage.backends.base import PermanentBackendError, TransientBackendError


class RangeParsing(unittest.TestCase):
    def test_valid(self):
        for header, size, expected in [("bytes=0-9", 100, (0, 9)), ("bytes=5-", 100, (5, 99)), ("bytes=-10", 100, (90, 99)),
                                       ("bytes=0-1000", 100, (0, 99)), ("bytes=-1000", 100, (0, 99)),
                                       (" bytes = 3 - 4 ", 100, (3, 4)), ("bytes=99-99", 100, (99, 99))]:
            self.assertEqual(util.parse_range(header, size), expected, header)

    def test_ignored(self):
        for header in (None, "", "bytes=5-2", "bytes=0-1,3-4", "bytes=", "bytes=-", "chars=1-2", "bytes=a-b", "junk"):
            self.assertIsNone(util.parse_range(header, 100), header)

    def test_unsatisfiable(self):
        for header, size in [("bytes=100-", 100), ("bytes=100-200", 100), ("bytes=-0", 100), ("bytes=0-0", 0), ("bytes=-5", 0)]:
            with self.assertRaises(errors.S3Error) as cm:
                util.parse_range(header, size)
            self.assertEqual((cm.exception.code, cm.exception.status), ("InvalidRange", 416), header)


class KeysAndNames(unittest.TestCase):
    def test_split_bucket_key(self):
        self.assertEqual(util.split_bucket_key("/"), ("", None))
        self.assertEqual(util.split_bucket_key(""), ("", None))
        self.assertEqual(util.split_bucket_key("/b"), ("b", None))
        self.assertEqual(util.split_bucket_key("/b/"), ("b", None))
        self.assertEqual(util.split_bucket_key("/b/k"), ("b", "k"))
        self.assertEqual(util.split_bucket_key("/b/dir/"), ("b", "dir/"))
        self.assertEqual(util.split_bucket_key("/b//x"), ("b", "/x"))

    def test_decode_and_encode(self):
        self.assertEqual(util.decode_path("/b/a%20b+c%2Fd/%D0%BF"), "/b/a b+c/d/п")
        self.assertEqual(util.aws_quote("a b/ü~", keep_slash=True), "a%20b/%C3%BC~")
        self.assertEqual(util.url_encode_for_listing("a b+c/é"), "a%20b%2Bc/%C3%A9")
        self.assertEqual(util.parse_query("a=1&b=x%20y+z&c&d="), [("a", "1"), ("b", "x y z"), ("c", ""), ("d", "")])

    def test_bucket_names(self):
        for ok in ("abc", "my-bucket.v2", "a1b", "x" * 63):
            util.validate_bucket_name(ok)
        for bad in ("ab", "x" * 64, "Abc", "a_b", "-ab", "ab-", ".ab", "a..b", "a.-b", "1.2.3.4", "ab c"):
            with self.assertRaises(errors.S3Error, msg=bad):
                util.validate_bucket_name(bad)

    def test_key_validation(self):
        util.validate_key("ok/ключ/😀")
        for bad in ("a\x00b", "a\x01b", "\ud800", "x" * 1025):
            with self.assertRaises(errors.S3Error):
                util.validate_key(bad)
        util.validate_key("x" * 1024)
        util.validate_key("я" * 512)
        with self.assertRaises(errors.S3Error):
            util.validate_key("я" * 513)

    def test_time_formats(self):
        self.assertEqual(util.iso8601("2026-11-14T10:20:30.123456Z"), "2026-11-14T10:20:30.123Z")
        self.assertEqual(util.http_date("2026-11-14T10:20:30.123456Z"), "Sat, 14 Nov 2026 10:20:30 GMT")
        self.assertEqual(util.quote_etag("abc"), '"abc"')


class XmlTests(unittest.TestCase):
    @staticmethod
    def parse(data: bytes):
        root = ET.fromstring(data)
        for el in root.iter():
            el.tag = el.tag.rsplit("}", 1)[-1]
        return root

    def test_error_xml_is_escaped_and_sanitised(self):
        err = errors.NoSuchKey("bad <&> \x01 message")
        root = self.parse(xmlio.error_xml(err, "/b/<key>&\x02", "REQ1"))
        self.assertEqual((root.findtext("Code"), root.findtext("RequestId")), ("NoSuchKey", "REQ1"))
        self.assertEqual(root.findtext("Resource"), "/b/<key>&?")
        self.assertEqual(root.findtext("Message"), "bad <&> ? message")

    def test_error_extra_fields(self):
        err = errors.RequestTimeTooSkewed(extra={"ServerTime": "t"})
        self.assertEqual(self.parse(xmlio.error_xml(err)).findtext("ServerTime"), "t")

    def test_list_objects_serialisation(self):
        r = xmlio.ListObjectsResult("bkt", "p/", "/", 2, True, True, [("p/ключ 1", "2026-01-02T03:04:05.678901Z", "abc", 5)],
                                    ["p/dir/"], "tok", "next", "p/a")
        root = self.parse(xmlio.list_objects_v2_xml(r))
        self.assertEqual(root.findtext("KeyCount"), "2")
        self.assertEqual(root.findtext("Contents/Key"), "p/%D0%BA%D0%BB%D1%8E%D1%87%201")
        self.assertEqual(root.findtext("Contents/ETag"), '"abc"')
        self.assertEqual(root.findtext("Contents/LastModified"), "2026-01-02T03:04:05.678Z")
        self.assertEqual(root.findtext("CommonPrefixes/Prefix"), "p/dir/")
        self.assertEqual((root.findtext("NextContinuationToken"), root.findtext("ContinuationToken"), root.findtext("StartAfter")),
                         ("next", "tok", "p/a"))
        self.assertEqual(root.findtext("EncodingType"), "url")
        self.assertTrue(xmlio.list_objects_v2_xml(r).startswith(b'<?xml version="1.0" encoding="UTF-8"?>'))

    def test_parse_complete_multipart_with_namespace(self):
        body = (b'<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b'<Part><PartNumber>1</PartNumber><ETag>"a"</ETag></Part><Part><PartNumber>2</PartNumber><ETag>b</ETag></Part>'
                b'</CompleteMultipartUpload>')
        self.assertEqual(xmlio.parse_complete_multipart(body), [(1, '"a"'), (2, "b")])

    def test_parsers_reject_garbage_and_entities(self):
        bombs = [b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><CompleteMultipartUpload>&lol;</CompleteMultipartUpload>',
                 b"<!doctype x><Delete/>"]
        for body in bombs:
            for fn in (xmlio.parse_complete_multipart, xmlio.parse_delete_objects, xmlio.parse_create_bucket):
                with self.assertRaises(errors.S3Error):
                    fn(body)
        for body in (b"", b"<a>", b"<Wrong/>", b"<CompleteMultipartUpload><Part><PartNumber>x</PartNumber><ETag>e</ETag></Part></CompleteMultipartUpload>"):
            with self.assertRaises(errors.S3Error):
                xmlio.parse_complete_multipart(body)
        with self.assertRaises(errors.S3Error):
            xmlio.parse_complete_multipart(b"<CompleteMultipartUpload>" + b"x" * (5 * 1024 * 1024))

    def test_delete_and_create_bucket_parsers(self):
        self.assertEqual(xmlio.parse_delete_objects(b"<Delete><Quiet>true</Quiet><Object><Key>a</Key></Object></Delete>"), (True, ["a"]))
        with self.assertRaises(errors.S3Error):
            xmlio.parse_delete_objects(b"<Delete/>")
        self.assertEqual(xmlio.parse_create_bucket(b""), "")
        self.assertEqual(xmlio.parse_create_bucket(b"<CreateBucketConfiguration><LocationConstraint>x</LocationConstraint></CreateBucketConfiguration>"), "x")

    def test_other_documents_are_wellformed(self):
        for data in (xmlio.list_buckets_xml("o", [("b", "2026-01-01T00:00:00.000000Z")]),
                     xmlio.initiate_multipart_xml("b", "k", "u"), xmlio.complete_multipart_xml("loc", "b", "k", "e-2"),
                     xmlio.list_parts_xml("b", "k", "u", [(1, "2026-01-01T00:00:00.000000Z", "e", 3)], marker=0, max_parts=1000, truncated=False),
                     xmlio.list_uploads_xml("b", [("k", "u", "2026-01-01T00:00:00.000000Z")], prefix="", key_marker="", upload_id_marker="", max_uploads=1, truncated=True, encoding_url=False),
                     xmlio.delete_result_xml(["a"], [("b", "AccessDenied", "no")], False), xmlio.location_xml("us-east-1")):
            self.parse(data)
        self.assertEqual(self.parse(xmlio.location_xml("us-east-1")).text or "", "")
        self.assertEqual(self.parse(xmlio.location_xml("eu-west-1")).text, "eu-west-1")


class ServiceHelpers(unittest.TestCase):
    def test_tokens_roundtrip_and_reject_garbage(self):
        for kind, value in (("K", "a/ключ b"), ("P", "dir/")):
            self.assertEqual(decode_token(encode_token(kind, value)), (kind, value))
        for bad in ("%%%", "", "Zm9v", encode_token("X", "v")[:-2], "ünïcode"):
            with self.assertRaises(errors.S3Error):
                decode_token(bad)

    def test_parse_object_headers(self):
        h = parse_object_headers({"content-type": "text/plain", "x-amz-meta-a": "1", "cache-control": "no-cache",
                                  "content-encoding": "aws-chunked", "content-language": "ru"})
        self.assertEqual(h.content_type, "text/plain")
        self.assertEqual(h.metadata, {"s3": {"meta": {"a": "1"}, "headers": {"cache-control": "no-cache", "content-language": "ru"}}})
        h = parse_object_headers({"content-encoding": "gzip, aws-chunked"})
        self.assertEqual(h.metadata["s3"]["headers"]["content-encoding"], "gzip")
        self.assertEqual(parse_object_headers({}).content_type, "binary/octet-stream")

    def test_rejected_headers(self):
        for hdr in ({"x-amz-acl": "public-read"}, {"x-amz-server-side-encryption": "aws:kms"}, {"x-amz-tagging": "a=b"},
                    {"x-amz-website-redirect-location": "/x"}, {"x-amz-grant-read": "id=1"}, {"x-amz-object-lock-mode": "GOVERNANCE"}):
            with self.assertRaises(errors.S3Error) as cm:
                parse_object_headers(hdr)
            self.assertEqual(cm.exception.status, 501, hdr)
        with self.assertRaises(errors.S3Error):
            parse_object_headers({"x-amz-meta-x": "y" * 2100})
        parse_object_headers({"x-amz-acl": "private", "x-amz-storage-class": "STANDARD"})

    def test_body_integrity(self):
        import base64, hashlib, zlib
        data = b"hello"
        headers = {"content-md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
                   "x-amz-checksum-sha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
                   "x-amz-checksum-crc32": base64.b64encode(zlib.crc32(data).to_bytes(4, "big")).decode()}
        i = BodyIntegrity(headers)
        i.update(data[:2]); i.update(data[2:])
        i.verify()
        self.assertEqual(i.size, 5)
        j = BodyIntegrity({})
        j.update(data)
        with self.assertRaises(errors.S3Error):
            j.verify({"x-amz-checksum-crc32": base64.b64encode(b"\0\0\0\0").decode()})
        j.verify({"x-amz-checksum-crc32c": "unchecked=="})  # crc32c не проверяется — не ошибка
        with self.assertRaises(errors.S3Error):
            BodyIntegrity({"content-md5": "!!"})
        with self.assertRaises(errors.S3Error):
            BodyIntegrity({"content-md5": base64.b64encode(b"short").decode()})

    def test_map_exception_hides_internals(self):
        table = [(se.BucketNotFound("x"), "NoSuchBucket", 404), (se.ObjectNotFound("x"), "NoSuchKey", 404),
                 (se.ObjectNotReady("x"), "NoSuchKey", 404), (se.BucketExists("x"), "BucketAlreadyOwnedByYou", 409),
                 (se.BucketNotEmpty("x"), "BucketNotEmpty", 409), (se.ChecksumMismatch("sha"), "XAmzContentSHA256Mismatch", 400),
                 (se.StorageBusy("x"), "SlowDown", 503), (se.StorageUnavailable("token 1:AB"), "ServiceUnavailable", 503),
                 (TransientBackendError("telegram 502 chat -100"), "ServiceUnavailable", 503),
                 (PermanentBackendError("telegram 403 bot token"), "InternalError", 500),
                 (se.IntegrityError("sha mismatch chunk 3"), "InternalError", 500), (se.StorageError("sql: boom"), "InternalError", 500),
                 (RuntimeError("secret"), "InternalError", 500), (se.MultipartError("NoSuchUpload", "x"), "NoSuchUpload", 404),
                 (se.MultipartError("EntityTooSmall", "x"), "EntityTooSmall", 400), (se.ObjectConflict("x"), "OperationAborted", 409),
                 (se.InvalidRange("x"), "InvalidRange", 416)]
        for exc, expected_code, status in table:
            err = map_exception(exc)
            self.assertEqual((err.code, err.status), (expected_code, status), exc)
            for leak in ("telegram", "token", "chat", "sql", "secret", "chunk", "502"):
                self.assertNotIn(leak, err.message.lower(), (exc, leak))


class ConfigTests(unittest.TestCase):
    def settings(self, **kw):
        return Settings(s3_enabled=True, s3_access_key_id="AK", s3_secret_access_key="SECRET", **kw)

    def test_validation(self):
        self.assertEqual(S3Config().validate(), [])
        self.assertTrue(S3Config(path_prefix="/").validate())
        self.assertTrue(S3Config(path_prefix="s3").validate())
        self.assertTrue(S3Config(path_prefix="/s3/").validate())
        self.assertTrue(S3Config(region="mars").validate())
        self.assertTrue(S3Config(clock_skew_seconds=0).validate())
        self.assertEqual(S3Config.from_settings(self.settings(s3_path_prefix="/files/")).path_prefix, "/files")
        self.assertEqual(S3Config.from_settings(self.settings(s3_max_request_size_mib=7)).max_request_size, 7 * 1024 * 1024)

    def test_credentials_from_settings(self):
        s = self.settings(s3_credentials="id2:sec2, id3:se:c3 ,broken,:x,id4:", s3_disabled_keys="id3")
        provider = StaticCredentialProvider.from_settings(s)
        self.assertEqual(provider.lookup("AK").secret_access_key, "SECRET")
        self.assertEqual(provider.lookup("id2").secret_access_key, "sec2")
        self.assertIsNone(provider.lookup("id3"))  # отключён
        self.assertIsNone(provider.lookup("broken"))
        self.assertIsNone(provider.lookup("id4"))
        self.assertIsNone(provider.lookup("nope"))
        self.assertFalse(StaticCredentialProvider.from_settings(Settings()))

    def test_secrets_not_in_repr(self):
        s = self.settings(s3_credentials="id2:sec2")
        text = repr(s) + repr(StaticCredentialProvider.from_settings(s).lookup("AK")) + repr(S3Config.from_settings(s))
        self.assertNotIn("SECRET", text)
        self.assertNotIn("sec2", text)

    def test_create_s3_app_gating(self):
        self.assertIsNone(create_s3_app(Settings()))  # выключено
        self.assertIsNotNone(create_s3_app(Settings(s3_enabled=True), get_core=lambda: None, get_store=lambda: None))  # ключи можно завести в UI
        self.assertIsNone(create_s3_app(self.settings(s3_path_prefix="/")))  # неверный префикс
        app = create_s3_app(self.settings(), get_core=lambda: None, get_store=lambda: None)
        self.assertEqual(app.config.path_prefix, "/s3")
        app.close()


if __name__ == "__main__":
    unittest.main()
