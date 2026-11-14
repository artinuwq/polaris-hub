"""SigV4: официальные тест-векторы из документации AWS S3, ошибки аутентификации, aws-chunked."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from polaris.s3 import errors
from polaris.s3.chunked import AwsChunkedDecoder
from polaris.s3.credentials import Credential, StaticCredentialProvider
from polaris.s3.sigv4 import (
    EMPTY_SHA256, SigRequest, canonical_query, canonical_uri, presign_url_query, sign, signing_key, verify,
)

AK = "AKIAIOSFODNN7EXAMPLE"
SK = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
NOW = datetime(2013, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
PROVIDER = StaticCredentialProvider([Credential(AK, SK)])
HOST = "examplebucket.s3.amazonaws.com"


def auth(signed: str, signature: str, date="20130524") -> str:
    return (f"AWS4-HMAC-SHA256 Credential={AK}/{date}/us-east-1/s3/aws4_request,"
            f"SignedHeaders={signed},Signature={signature}")


def check(req: SigRequest, now=NOW, **kw):
    return verify(req, PROVIDER, region="us-east-1", now=now, **kw)


class AwsDocumentationVectors(unittest.TestCase):
    def test_get_object_with_range(self):
        req = SigRequest("GET", "/test.txt", "", [
            ("host", HOST), ("range", "bytes=0-9"), ("x-amz-content-sha256", EMPTY_SHA256),
            ("x-amz-date", "20130524T000000Z"),
            ("authorization", auth("host;range;x-amz-content-sha256;x-amz-date",
                                   "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))])
        self.assertEqual(check(req).access_key_id, AK)

    def test_put_object_with_special_characters_in_key(self):
        req = SigRequest("PUT", "/test$file.text", "", [
            ("date", "Fri, 24 May 2013 00:00:00 GMT"), ("host", HOST), ("x-amz-date", "20130524T000000Z"),
            ("x-amz-storage-class", "REDUCED_REDUNDANCY"),
            ("x-amz-content-sha256", "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"),
            ("authorization", auth("date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class",
                                   "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"))])
        check(req)

    def test_get_bucket_lifecycle_query_without_value(self):
        req = SigRequest("GET", "/", "lifecycle", [
            ("host", HOST), ("x-amz-content-sha256", EMPTY_SHA256), ("x-amz-date", "20130524T000000Z"),
            ("authorization", auth("host;x-amz-content-sha256;x-amz-date",
                                   "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"))])
        check(req)

    def test_list_objects_query_parameters(self):
        req = SigRequest("GET", "/", "max-keys=2&prefix=J", [
            ("host", HOST), ("x-amz-content-sha256", EMPTY_SHA256), ("x-amz-date", "20130524T000000Z"),
            ("authorization", auth("host;x-amz-content-sha256;x-amz-date",
                                   "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"))])
        check(req)

    def test_presigned_url(self):
        query = ("X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request"
                 "&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host"
                 "&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
        res = check(SigRequest("GET", "/test.txt", query, [("host", HOST)]), now=NOW + timedelta(hours=1))
        self.assertTrue(res.presigned)
        # собственный построитель даёт ту же подпись
        built = presign_url_query(SK, AK, method="GET", host=HOST, raw_path="/test.txt", extra_query=[],
                                  region="us-east-1", amz_date="20130524T000000Z", expires=86400)
        self.assertTrue(built.endswith("aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"))

    def test_chunked_upload_signature_chain(self):
        req = SigRequest("PUT", "/examplebucket/chunkObject.txt", "", [
            ("host", "s3.amazonaws.com"), ("x-amz-date", "20130524T000000Z"),
            ("x-amz-storage-class", "REDUCED_REDUNDANCY"), ("content-encoding", "aws-chunked"),
            ("x-amz-decoded-content-length", "66560"), ("content-length", "66824"),
            ("x-amz-content-sha256", "STREAMING-AWS4-HMAC-SHA256-PAYLOAD"),
            ("authorization", auth("content-encoding;content-length;host;x-amz-content-sha256;x-amz-date;"
                                   "x-amz-decoded-content-length;x-amz-storage-class",
                                   "4f232c4386841ef735655705268965c44a0e4690baa4adea153f7db9fa80a0a9"))])
        result = check(req)
        body = (b"10000;chunk-signature=ad80c730a21e5b8d04586a2213dd63b9a0e99e0e2307b0ade35a65485a288648\r\n"
                + b"a" * 65536 + b"\r\n"
                + b"400;chunk-signature=0055627c9e194cb4542bae2aa5492e3c1575bbb81b612b7d234b86a503ef5497\r\n"
                + b"a" * 1024 + b"\r\n"
                + b"0;chunk-signature=b6c6ea8a5354eaf15b3cb7646744f4275b71ea724fed81ceb9323e279d449df9\r\n\r\n")
        dec = AwsChunkedDecoder("STREAMING-AWS4-HMAC-SHA256-PAYLOAD", result)
        got = b"".join(p for i in range(0, len(body), 1000) for p in dec.decode(body[i:i + 1000]))
        dec.finish()
        self.assertEqual(got, b"a" * 66560)


class VerifyErrors(unittest.TestCase):
    def base(self, **over):
        headers = [("host", HOST), ("x-amz-content-sha256", EMPTY_SHA256), ("x-amz-date", "20130524T000000Z")]
        sig = "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"
        headers.append(("authorization", over.pop("authorization", auth("host;x-amz-content-sha256;x-amz-date", sig))))
        return SigRequest("GET", "/", over.pop("query", "lifecycle"), headers)

    def code(self, req, **kw):
        with self.assertRaises(errors.S3Error) as cm:
            check(req, **kw)
        return cm.exception.code

    def test_valid(self):
        check(self.base())

    def test_wrong_signature(self):
        self.assertEqual(self.code(self.base(query="lifecycle&x=1")), "SignatureDoesNotMatch")

    def test_unknown_access_key(self):
        a = auth("host;x-amz-content-sha256;x-amz-date", "0" * 64).replace(AK, "AKIAUNKNOWN")
        self.assertEqual(self.code(self.base(authorization=a)), "InvalidAccessKeyId")

    def test_disabled_key_is_unknown(self):
        provider = StaticCredentialProvider([Credential(AK, SK, enabled=False)])
        with self.assertRaises(errors.S3Error) as cm:
            verify(self.base(), provider, region="us-east-1", now=NOW)
        self.assertEqual(cm.exception.code, "InvalidAccessKeyId")

    def test_clock_skew(self):
        self.assertEqual(self.code(self.base(), now=NOW + timedelta(minutes=16)), "RequestTimeTooSkewed")
        self.assertEqual(self.code(self.base(), now=NOW - timedelta(minutes=16)), "RequestTimeTooSkewed")
        check(self.base(), now=NOW + timedelta(minutes=14))
        self.assertEqual(self.code(self.base(), now=NOW + timedelta(minutes=2), skew_seconds=60), "RequestTimeTooSkewed")

    def test_anonymous_and_malformed(self):
        anon = SigRequest("GET", "/", "", [("host", HOST)])
        self.assertEqual(self.code(anon), "AccessDenied")
        self.assertEqual(self.code(self.base(authorization="Basic abc")), "AuthorizationHeaderMalformed")
        self.assertEqual(self.code(self.base(authorization="AWS4-HMAC-SHA256 Credential=x")), "AuthorizationHeaderMalformed")

    def test_wrong_region_and_service(self):
        a = auth("host;x-amz-content-sha256;x-amz-date", "0" * 64).replace("us-east-1", "eu-west-1")
        self.assertEqual(self.code(self.base(authorization=a)), "AuthorizationHeaderMalformed")
        a = auth("host;x-amz-content-sha256;x-amz-date", "0" * 64).replace("/s3/", "/ec2/")
        self.assertEqual(self.code(self.base(authorization=a)), "AuthorizationHeaderMalformed")

    def test_host_must_be_signed(self):
        a = auth("x-amz-content-sha256;x-amz-date", "0" * 64)
        self.assertEqual(self.code(self.base(authorization=a)), "AccessDenied")

    def test_missing_payload_hash_only_allowed_without_body(self):
        headers = [("host", HOST), ("x-amz-date", "20130524T000000Z"), ("content-length", "5"),
                   ("authorization", auth("host;x-amz-date", "0" * 64))]
        self.assertEqual(self.code(SigRequest("PUT", "/b/k", "", headers)), "InvalidRequest")

    def test_tampered_payload_hash_header_breaks_signature(self):
        req = self.base()
        headers = [(k, "f" * 64 if k == "x-amz-content-sha256" else v) for k, v in req.headers]
        self.assertEqual(self.code(SigRequest(req.method, req.raw_path, req.raw_query, headers)), "SignatureDoesNotMatch")

    def test_signature_comparison_case_insensitive_hex(self):
        sig = "FEA454CA298B7DA1C68078A5D1BDBFBBE0D65C699E0F91AC7A200A0136783543"
        check(self.base(authorization=auth("host;x-amz-content-sha256;x-amz-date", sig)))


class PresignedErrors(unittest.TestCase):
    def build(self, **kw):
        params = dict(method="GET", host=HOST, raw_path="/test.txt", extra_query=[], region="us-east-1",
                      amz_date="20130524T000000Z", expires=60)
        params.update(kw)
        return SigRequest(params["method"], params["raw_path"],
                          presign_url_query(SK, AK, **params), [("host", params["host"])])

    def code(self, req, now):
        with self.assertRaises(errors.S3Error) as cm:
            check(req, now=now)
        return cm.exception.code

    def test_valid_and_expired(self):
        req = self.build()
        check(req, now=NOW + timedelta(seconds=59))
        self.assertEqual(self.code(req, NOW + timedelta(seconds=61)), "AccessDenied")

    def test_not_yet_valid(self):
        self.assertEqual(self.code(self.build(), NOW - timedelta(hours=2)), "AccessDenied")

    def test_tampering_breaks_signature(self):
        req = self.build()
        for changed in (SigRequest("PUT", req.raw_path, req.raw_query, req.headers),
                        SigRequest("GET", "/other.txt", req.raw_query, req.headers),
                        SigRequest("GET", req.raw_path, req.raw_query, [("host", "evil.example.com")]),
                        SigRequest("GET", req.raw_path, req.raw_query + "&extra=1", req.headers)):
            self.assertEqual(self.code(changed, NOW + timedelta(seconds=5)), "SignatureDoesNotMatch")

    def test_expires_limits_and_missing_params(self):
        for exp in (0, 604801):
            q = presign_url_query(SK, AK, method="GET", host=HOST, raw_path="/k", extra_query=[], region="us-east-1",
                                  amz_date="20130524T000000Z", expires=exp)
            self.assertEqual(self.code(SigRequest("GET", "/k", q, [("host", HOST)]), NOW),
                             "AuthorizationQueryParametersError")
        q = self.build().raw_query.replace("X-Amz-Expires=60&", "")
        self.assertEqual(self.code(SigRequest("GET", "/test.txt", q, [("host", HOST)]), NOW),
                         "AuthorizationQueryParametersError")

    def test_unknown_key_in_presigned(self):
        req = self.build()
        q = req.raw_query.replace("AKIAIOSFODNN7EXAMPLE", "AKIAUNKNOWN0000000000")
        self.assertEqual(self.code(SigRequest("GET", "/test.txt", q, req.headers), NOW), "InvalidAccessKeyId")

    def test_extra_query_params_are_signed(self):
        req = self.build(extra_query=[("response-content-type", "text/plain"), ("versionId", "a b+c")])
        check(req, now=NOW)
        q = req.raw_query.replace("text%2Fplain", "text%2Fhtml")
        self.assertEqual(self.code(SigRequest("GET", req.raw_path, q, req.headers), NOW), "SignatureDoesNotMatch")


class Canonicalisation(unittest.TestCase):
    def test_uri_is_encoded_once_without_normalisation(self):
        self.assertEqual(canonical_uri("/b/a b/ü/%7Efoo+bar/../x//y"), "/b/a%20b/%C3%BC/~foo%2Bbar/../x//y")
        self.assertEqual(canonical_uri(""), "/")
        self.assertEqual(canonical_uri("/b/%E4%B8%AD"), "/b/%E4%B8%AD")

    def test_different_client_encodings_canonicalise_equally(self):
        self.assertEqual(canonical_uri("/b/k!*'()"), canonical_uri("/b/k%21%2A%27%28%29"))

    def test_query_sorted_and_encoded(self):
        pairs = [("b", "2"), ("a", "1 2"), ("a", "1"), ("empty", ""), ("k+", "v/")]
        self.assertEqual(canonical_query(pairs), "a=1&a=1%202&b=2&empty=&k%2B=v%2F")

    def test_invalid_utf8_path(self):
        with self.assertRaises(errors.S3Error):
            canonical_uri("/b/%ff%fe")

    def test_signing_key_vector(self):
        key = signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830", "us-east-1", "iam")
        self.assertEqual(key.hex(), "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9")


class ChunkedDecoder(unittest.TestCase):
    def test_unsigned_trailer(self):
        body = b"5\r\nhello\r\n6\r\n world\r\n0\r\nx-amz-checksum-crc32:abcd==\r\n\r\n"
        dec = AwsChunkedDecoder("STREAMING-UNSIGNED-PAYLOAD-TRAILER")
        out = b"".join(p for b in (body[i:i + 3] for i in range(0, len(body), 3)) for p in dec.decode(b))
        dec.finish()
        self.assertEqual((out, dec.trailers, dec.decoded_bytes), (b"hello world", {"x-amz-checksum-crc32": "abcd=="}, 11))

    def test_truncated_body_detected(self):
        dec = AwsChunkedDecoder("STREAMING-UNSIGNED-PAYLOAD-TRAILER")
        dec.decode(b"5\r\nhel")
        with self.assertRaises(errors.S3Error):
            dec.finish()

    def test_malformed(self):
        for bad in (b"zz\r\nabc", b"3\r\nabcXX", b"3\r\nabc\r\n0\r\n\r\ngarbage"):
            dec = AwsChunkedDecoder("STREAMING-UNSIGNED-PAYLOAD-TRAILER")
            with self.assertRaises(errors.S3Error):
                dec.decode(bad)
                dec.finish()

    def test_signed_chunk_tampering_is_detected(self):
        req = SigRequest("PUT", "/examplebucket/chunkObject.txt", "", [
            ("host", "s3.amazonaws.com"), ("x-amz-date", "20130524T000000Z"),
            ("x-amz-storage-class", "REDUCED_REDUNDANCY"), ("content-encoding", "aws-chunked"),
            ("x-amz-decoded-content-length", "66560"), ("content-length", "66824"),
            ("x-amz-content-sha256", "STREAMING-AWS4-HMAC-SHA256-PAYLOAD"),
            ("authorization", auth("content-encoding;content-length;host;x-amz-content-sha256;x-amz-date;"
                                   "x-amz-decoded-content-length;x-amz-storage-class",
                                   "4f232c4386841ef735655705268965c44a0e4690baa4adea153f7db9fa80a0a9"))])
        dec = AwsChunkedDecoder("STREAMING-AWS4-HMAC-SHA256-PAYLOAD", check(req))
        body = (b"10000;chunk-signature=ad80c730a21e5b8d04586a2213dd63b9a0e99e0e2307b0ade35a65485a288648\r\n"
                + b"b" + b"a" * 65535 + b"\r\n")  # изменён первый байт
        with self.assertRaises(errors.S3Error) as cm:
            dec.decode(body)
        self.assertEqual(cm.exception.code, "SignatureDoesNotMatch")

    def test_signed_trailer_roundtrip(self):
        """Клиентская сторона собрана здесь же по описанию AWS: цепочка подписей + подписанный trailer."""
        import hashlib
        req = SigRequest("PUT", "/b/k", "", [("host", "h"), ("x-amz-date", "20130524T000000Z"),
                         ("x-amz-content-sha256", "STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER"),
                         ("x-amz-trailer", "x-amz-checksum-crc32")])
        signed = ("host", "x-amz-content-sha256", "x-amz-date", "x-amz-trailer")
        from polaris.s3.sigv4 import canonical_request, string_to_sign
        key = signing_key(SK, "20130524", "us-east-1")
        scope = "20130524/us-east-1/s3/aws4_request"
        seed = sign(key, string_to_sign("20130524T000000Z", scope,
                                        canonical_request(req, signed, "STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER", presigned=False)))
        auth_header = auth(";".join(signed), seed)
        req = SigRequest(req.method, req.raw_path, "", req.headers + [("authorization", auth_header)])
        result = check(req)
        prev, body = seed, b""
        for data in (b"hello", b""):
            sts = "\n".join(["AWS4-HMAC-SHA256-PAYLOAD", "20130524T000000Z", scope, prev, EMPTY_SHA256,
                             hashlib.sha256(data).hexdigest()])
            prev = sign(key, sts)
            body += f"{len(data):x};chunk-signature={prev}\r\n".encode() + data + (b"\r\n" if data else b"")
        trailer = "x-amz-checksum-crc32:NhCmhg==\n"
        tsig = sign(key, "\n".join(["AWS4-HMAC-SHA256-TRAILER", "20130524T000000Z", scope, prev,
                                    hashlib.sha256(trailer.encode()).hexdigest()]))
        body += f"x-amz-checksum-crc32:NhCmhg==\r\nx-amz-trailer-signature:{tsig}\r\n\r\n".encode()
        dec = AwsChunkedDecoder("STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER", result)
        self.assertEqual(b"".join(dec.decode(body)), b"hello")
        dec.finish()
        self.assertEqual(dec.trailers, {"x-amz-checksum-crc32": "NhCmhg=="})
        bad = AwsChunkedDecoder("STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER", result)
        with self.assertRaises(errors.S3Error):
            bad.decode(body.replace(b"NhCmhg==\r\nx-amz-trailer", b"NhCmhg=X\r\nx-amz-trailer"))


if __name__ == "__main__":
    unittest.main()
