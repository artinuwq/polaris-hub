"""XML-ответы и разбор XML-тел запросов S3 (stdlib ElementTree)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from polaris.s3 import errors
from polaris.s3.util import iso8601, quote_etag, url_encode_for_listing

NS = "http://s3.amazonaws.com/doc/2006-03-01/"
MAX_XML_BODY = 4 * 1024 * 1024


def _dump(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def _el(parent: ET.Element, tag: str, text: str | int | None = None) -> ET.Element:
    e = ET.SubElement(parent, tag)
    if text is not None:
        e.text = _XML_BAD.sub("?", str(text))  # символы, недопустимые в XML 1.0, не должны ломать ответ
    return e


def error_xml(err: errors.S3Error, resource: str = "", request_id: str = "") -> bytes:
    root = ET.Element("Error")
    _el(root, "Code", err.code)
    _el(root, "Message", err.message)
    for k, v in err.extra.items():
        _el(root, k, v)
    _el(root, "Resource", resource)
    _el(root, "RequestId", request_id)
    return _dump(root)


def list_buckets_xml(owner_id: str, buckets: list[tuple[str, str]]) -> bytes:
    root = ET.Element("ListAllMyBucketsResult", xmlns=NS)
    owner = _el(root, "Owner")
    _el(owner, "ID", owner_id)
    _el(owner, "DisplayName", owner_id)
    container = _el(root, "Buckets")
    for name, created in buckets:
        b = _el(container, "Bucket")
        _el(b, "Name", name)
        _el(b, "CreationDate", iso8601(created))
    return _dump(root)


@dataclass
class ListObjectsResult:
    bucket: str
    prefix: str
    delimiter: str
    max_keys: int
    encoding_url: bool
    is_truncated: bool
    contents: list[tuple[str, str, str, int]] = field(default_factory=list)  # key, last_modified, etag, size
    common_prefixes: list[str] = field(default_factory=list)
    continuation_token: str = ""
    next_token: str = ""
    start_after: str = ""


def list_objects_v2_xml(r: ListObjectsResult) -> bytes:
    enc = url_encode_for_listing if r.encoding_url else (lambda v: v)
    root = ET.Element("ListBucketResult", xmlns=NS)
    _el(root, "Name", r.bucket)
    _el(root, "Prefix", enc(r.prefix))
    if r.delimiter:
        _el(root, "Delimiter", enc(r.delimiter))
    _el(root, "MaxKeys", r.max_keys)
    if r.encoding_url:
        _el(root, "EncodingType", "url")
    _el(root, "KeyCount", len(r.contents) + len(r.common_prefixes))
    _el(root, "IsTruncated", "true" if r.is_truncated else "false")
    if r.continuation_token:
        _el(root, "ContinuationToken", r.continuation_token)
    if r.next_token:
        _el(root, "NextContinuationToken", r.next_token)
    if r.start_after:
        _el(root, "StartAfter", enc(r.start_after))
    for key, modified, etag, size in r.contents:
        c = _el(root, "Contents")
        _el(c, "Key", enc(key))
        _el(c, "LastModified", iso8601(modified))
        _el(c, "ETag", quote_etag(etag))
        _el(c, "Size", size)
        _el(c, "StorageClass", "STANDARD")
    for p in r.common_prefixes:
        _el(_el(root, "CommonPrefixes"), "Prefix", enc(p))
    return _dump(root)


def initiate_multipart_xml(bucket: str, key: str, upload_id: str) -> bytes:
    root = ET.Element("InitiateMultipartUploadResult", xmlns=NS)
    _el(root, "Bucket", bucket)
    _el(root, "Key", key)
    _el(root, "UploadId", upload_id)
    return _dump(root)


def complete_multipart_xml(location: str, bucket: str, key: str, etag: str) -> bytes:
    root = ET.Element("CompleteMultipartUploadResult", xmlns=NS)
    _el(root, "Location", location)
    _el(root, "Bucket", bucket)
    _el(root, "Key", key)
    _el(root, "ETag", quote_etag(etag))
    return _dump(root)


def list_parts_xml(bucket: str, key: str, upload_id: str, parts: list[tuple[int, str, str, int]], *,
                   marker: int, max_parts: int, truncated: bool) -> bytes:
    root = ET.Element("ListPartsResult", xmlns=NS)
    _el(root, "Bucket", bucket)
    _el(root, "Key", key)
    _el(root, "UploadId", upload_id)
    _el(root, "PartNumberMarker", marker)
    if truncated and parts:
        _el(root, "NextPartNumberMarker", parts[-1][0])
    _el(root, "MaxParts", max_parts)
    _el(root, "IsTruncated", "true" if truncated else "false")
    _el(root, "StorageClass", "STANDARD")
    for number, modified, etag, size in parts:
        p = _el(root, "Part")
        _el(p, "PartNumber", number)
        _el(p, "LastModified", iso8601(modified))
        _el(p, "ETag", quote_etag(etag))
        _el(p, "Size", size)
    return _dump(root)


def list_uploads_xml(bucket: str, uploads: list[tuple[str, str, str]], *, prefix: str, key_marker: str,
                     upload_id_marker: str, max_uploads: int, truncated: bool, encoding_url: bool) -> bytes:
    enc = url_encode_for_listing if encoding_url else (lambda v: v)
    root = ET.Element("ListMultipartUploadsResult", xmlns=NS)
    _el(root, "Bucket", bucket)
    _el(root, "KeyMarker", enc(key_marker))
    _el(root, "UploadIdMarker", upload_id_marker)
    if truncated and uploads:
        _el(root, "NextKeyMarker", enc(uploads[-1][0]))
        _el(root, "NextUploadIdMarker", uploads[-1][1])
    _el(root, "Prefix", enc(prefix))
    _el(root, "MaxUploads", max_uploads)
    _el(root, "IsTruncated", "true" if truncated else "false")
    if encoding_url:
        _el(root, "EncodingType", "url")
    for key, upload_id, initiated in uploads:
        u = _el(root, "Upload")
        _el(u, "Key", enc(key))
        _el(u, "UploadId", upload_id)
        _el(u, "StorageClass", "STANDARD")
        _el(u, "Initiated", iso8601(initiated))
    return _dump(root)


def delete_result_xml(deleted: list[str], failed: list[tuple[str, str, str]], quiet: bool) -> bytes:
    root = ET.Element("DeleteResult", xmlns=NS)
    if not quiet:
        for key in deleted:
            _el(_el(root, "Deleted"), "Key", key)
    for key, code, message in failed:
        e = _el(root, "Error")
        _el(e, "Key", key)
        _el(e, "Code", code)
        _el(e, "Message", message)
    return _dump(root)


def location_xml(region: str) -> bytes:
    root = ET.Element("LocationConstraint", xmlns=NS)
    root.text = "" if region == "us-east-1" else region
    return _dump(root)


# ─── разбор тел запросов ───

def _parse(body: bytes) -> ET.Element:
    if len(body) > MAX_XML_BODY:
        raise errors.EntityTooLarge("XML-тело слишком велико")
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body.lower():
        raise errors.MalformedXML()  # внешние сущности и DTD не принимаются
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise errors.MalformedXML() from None
    for el in root.iter():  # убрать пространства имён
        el.tag = el.tag.rsplit("}", 1)[-1]
    return root


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_complete_multipart(body: bytes) -> list[tuple[int, str]]:
    root = _parse(body)
    if root.tag != "CompleteMultipartUpload":
        raise errors.MalformedXML()
    parts = []
    for part in root.findall("Part"):
        number, etag = _text(part.find("PartNumber")), _text(part.find("ETag"))
        if not number.isdigit() or not etag:
            raise errors.MalformedXML()
        parts.append((int(number), etag))
    if not parts:
        raise errors.MalformedXML()
    return parts


def parse_delete_objects(body: bytes) -> tuple[bool, list[str]]:
    root = _parse(body)
    if root.tag != "Delete":
        raise errors.MalformedXML()
    keys = [_text(o.find("Key")) for o in root.findall("Object")]
    if not keys or any(not k for k in keys) or len(keys) > 1000:
        raise errors.MalformedXML()
    return _text(root.find("Quiet")).lower() == "true", keys


def parse_create_bucket(body: bytes) -> str:
    """Вернуть LocationConstraint из тела CreateBucket ('' если тела или поля нет)."""
    if not body.strip():
        return ""
    root = _parse(body)
    if root.tag != "CreateBucketConfiguration":
        raise errors.MalformedXML()
    return _text(root.find("LocationConstraint"))
