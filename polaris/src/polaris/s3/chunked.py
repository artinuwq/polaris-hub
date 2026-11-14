"""Декодер aws-chunked тела (потоковые загрузки SDK).

Поддержаны режимы x-amz-content-sha256:
  STREAMING-AWS4-HMAC-SHA256-PAYLOAD            — подписанные чанки;
  STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER    — подписанные чанки + подписанный trailer;
  STREAMING-UNSIGNED-PAYLOAD-TRAILER            — без подписей, контрольные суммы в trailer.
Данные не накапливаются: decode() отдаёт полезные байты по мере разбора.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from polaris.s3 import errors
from polaris.s3.sigv4 import EMPTY_SHA256, AuthResult, sign

STREAMING_MODES = {
    "STREAMING-AWS4-HMAC-SHA256-PAYLOAD": (True, False),
    "STREAMING-AWS4-HMAC-SHA256-PAYLOAD-TRAILER": (True, True),
    "STREAMING-UNSIGNED-PAYLOAD-TRAILER": (False, True),
}
_MAX_LINE = 8192
_HEX = re.compile(r"^[0-9a-fA-F]+$")


class AwsChunkedDecoder:
    def __init__(self, mode: str, auth: AuthResult | None = None) -> None:
        self.signed, self.has_trailer = STREAMING_MODES[mode]
        if self.signed and auth is None:
            raise ValueError("для подписанных чанков нужен результат аутентификации")
        self._auth = auth
        self._prev_sig = auth.signature if auth else ""
        self._buf = bytearray()
        self._state = "header"
        self._remaining = 0
        self._chunk_sig = ""
        self._hasher = None
        self._trailer_lines: list[tuple[str, str]] = []
        self._trailer_sig: str | None = None
        self.trailers: dict[str, str] = {}
        self.decoded_bytes = 0

    # ─── вспомогательное ───

    def _bad(self, msg: str = "некорректное aws-chunked тело") -> errors.S3Error:
        return errors.IncompleteBody(msg)

    def _verify_chunk(self, digest: str) -> None:
        assert self._auth is not None
        to_sign = "\n".join(["AWS4-HMAC-SHA256-PAYLOAD", self._auth.amz_date, self._auth.scope, self._prev_sig,
                             EMPTY_SHA256, digest])
        expected = sign(self._auth.signing_key, to_sign)
        if not hmac.compare_digest(expected, self._chunk_sig.lower()):
            raise errors.SignatureDoesNotMatch("подпись чанка не совпала")
        self._prev_sig = expected

    def _verify_trailer(self) -> None:
        assert self._auth is not None
        canonical = "".join(f"{k}:{v}\n" for k, v in self._trailer_lines)
        to_sign = "\n".join(["AWS4-HMAC-SHA256-TRAILER", self._auth.amz_date, self._auth.scope, self._prev_sig,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        expected = sign(self._auth.signing_key, to_sign)
        if not self._trailer_sig or not hmac.compare_digest(expected, self._trailer_sig.lower()):
            raise errors.SignatureDoesNotMatch("подпись trailer не совпала")

    def _line(self) -> bytes | None:
        idx = self._buf.find(b"\r\n")
        if idx < 0:
            if len(self._buf) > _MAX_LINE:
                raise self._bad("слишком длинная служебная строка")
            return None
        line = bytes(self._buf[:idx])
        del self._buf[:idx + 2]
        if len(line) > _MAX_LINE:
            raise self._bad("слишком длинная служебная строка")
        return line

    # ─── разбор ───

    def decode(self, data: bytes) -> list[bytes]:
        """Принять очередную порцию тела; вернуть расшифрованные куски (возможно, пустой список)."""
        out: list[bytes] = []
        self._buf += data
        while True:
            if self._state == "header":
                line = self._line()
                if line is None:
                    break
                size_text, _, ext = line.decode("ascii", "replace").partition(";")
                if not _HEX.match(size_text) or len(size_text) > 15:
                    raise self._bad("некорректный размер чанка")
                self._chunk_sig = ""
                for item in ext.split(";"):
                    name, _, value = item.partition("=")
                    if name.strip() == "chunk-signature":
                        self._chunk_sig = value.strip()
                if self.signed and not re.fullmatch(r"[0-9a-fA-F]{64}", self._chunk_sig):
                    raise errors.SignatureDoesNotMatch("у чанка нет корректной подписи")
                self._remaining = int(size_text, 16)
                self._hasher = hashlib.sha256() if self.signed else None
                if self._remaining == 0:
                    if self.signed:
                        self._verify_chunk(EMPTY_SHA256)
                    self._state = "trailer" if self.has_trailer else "end"
                else:
                    self._state = "data"
            elif self._state == "data":
                if not self._buf:
                    break
                take = bytes(self._buf[:self._remaining])
                del self._buf[:len(take)]
                self._remaining -= len(take)
                if self._hasher is not None:
                    self._hasher.update(take)
                self.decoded_bytes += len(take)
                out.append(take)
                if self._remaining == 0:
                    if self._hasher is not None:
                        self._verify_chunk(self._hasher.hexdigest())
                    self._state = "data_crlf"
            elif self._state == "data_crlf":
                if len(self._buf) < 2:
                    break
                if bytes(self._buf[:2]) != b"\r\n":
                    raise self._bad("после данных чанка нет CRLF")
                del self._buf[:2]
                self._state = "header"
            elif self._state == "trailer":
                line = self._line()
                if line is None:
                    break
                if not line:
                    self._finish_trailer()
                    continue
                name, sep, value = line.decode("utf-8", "replace").partition(":")
                if not sep:
                    raise self._bad("некорректная строка trailer")
                name = name.strip().lower()
                if name == "x-amz-trailer-signature":
                    self._trailer_sig = value.strip()
                else:
                    self._trailer_lines.append((name, value.strip()))
                    self.trailers[name] = value.strip()
            else:  # end
                if self._buf.strip(b"\r\n"):
                    raise self._bad("лишние данные после последнего чанка")
                self._buf.clear()
                break
        return out

    def _finish_trailer(self) -> None:
        if self.signed:
            self._verify_trailer()
        self._state = "end"

    def finish(self) -> None:
        """Конец потока: тело должно быть полностью разобрано."""
        if self._state == "trailer" and not self._buf:
            self._finish_trailer()  # блок trailer мог закончиться без пустой строки
        if self._state != "end" or self._buf.strip(b"\r\n"):
            raise errors.IncompleteBody("aws-chunked тело оборвано")
