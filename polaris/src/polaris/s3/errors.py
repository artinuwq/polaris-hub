"""S3-ошибки: код, HTTP-статус и безопасное сообщение (без внутренних деталей)."""

from __future__ import annotations


class S3Error(Exception):
    def __init__(self, code: str, message: str, status: int, *, extra: dict[str, str] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}
        self.headers = headers or {}


def _e(code: str, status: int, default_message: str):
    def factory(message: str | None = None, **kw) -> S3Error:
        return S3Error(code, message or default_message, status, **kw)

    return factory


AccessDenied = _e("AccessDenied", 403, "Access Denied")
InvalidAccessKeyId = _e("InvalidAccessKeyId", 403, "The AWS Access Key Id you provided does not exist in our records.")
SignatureDoesNotMatch = _e("SignatureDoesNotMatch", 403,
                           "The request signature we calculated does not match the signature you provided.")
RequestTimeTooSkewed = _e("RequestTimeTooSkewed", 403,
                          "The difference between the request time and the current time is too large.")
AuthorizationHeaderMalformed = _e("AuthorizationHeaderMalformed", 400, "The authorization header is malformed.")
AuthorizationQueryParametersError = _e("AuthorizationQueryParametersError", 400,
                                       "Error parsing the X-Amz-Credential parameter.")
MissingSecurityHeader = _e("MissingSecurityHeader", 400, "Your request was missing a required header.")
NoSuchBucket = _e("NoSuchBucket", 404, "The specified bucket does not exist.")
NoSuchKey = _e("NoSuchKey", 404, "The specified key does not exist.")
NoSuchUpload = _e("NoSuchUpload", 404, "The specified multipart upload does not exist.")
BucketAlreadyExists = _e("BucketAlreadyExists", 409, "The requested bucket name is not available.")
BucketAlreadyOwnedByYou = _e("BucketAlreadyOwnedByYou", 409,
                             "Your previous request to create the named bucket succeeded and you already own it.")
BucketNotEmpty = _e("BucketNotEmpty", 409, "The bucket you tried to delete is not empty.")
InvalidBucketName = _e("InvalidBucketName", 400, "The specified bucket is not valid.")
InvalidRequest = _e("InvalidRequest", 400, "Invalid request.")
InvalidArgument = _e("InvalidArgument", 400, "Invalid argument.")
InvalidRange = _e("InvalidRange", 416, "The requested range is not satisfiable")
InvalidPart = _e("InvalidPart", 400, "One or more of the specified parts could not be found.")
InvalidPartOrder = _e("InvalidPartOrder", 400, "The list of parts was not in ascending order.")
EntityTooLarge = _e("EntityTooLarge", 400, "Your proposed upload exceeds the maximum allowed size.")
EntityTooSmall = _e("EntityTooSmall", 400, "Your proposed upload is smaller than the minimum allowed size.")
MalformedXML = _e("MalformedXML", 400, "The XML you provided was not well-formed or did not validate against our published schema.")
InvalidDigest = _e("InvalidDigest", 400, "The Content-MD5 you specified is not valid.")
BadDigest = _e("BadDigest", 400, "The Content-MD5 or checksum value you specified did not match what we received.")
XAmzContentSHA256Mismatch = _e("XAmzContentSHA256Mismatch", 400,
                               "The provided 'x-amz-content-sha256' header does not match what was computed.")
IncompleteBody = _e("IncompleteBody", 400, "You did not provide the number of bytes specified by the Content-Length HTTP header.")
MissingContentLength = _e("MissingContentLength", 411, "You must provide the Content-Length HTTP header.")
KeyTooLongError = _e("KeyTooLongError", 400, "Your key is too long.")
MetadataTooLarge = _e("MetadataTooLarge", 400, "Your metadata headers exceed the maximum allowed metadata size.")
InvalidURI = _e("InvalidURI", 400, "Couldn't parse the specified URI.")
MethodNotAllowed = _e("MethodNotAllowed", 405, "The specified method is not allowed against this resource.")
NotImplementedS3 = _e("NotImplemented", 501, "A header you provided implies functionality that is not implemented.")
PreconditionFailed = _e("PreconditionFailed", 412, "At least one of the pre-conditions you specified did not hold.")
RequestTimeout = _e("RequestTimeout", 400, "Your socket connection to the server was not read from or written to within the timeout period.")
InternalError = _e("InternalError", 500, "We encountered an internal error. Please try again.")
ServiceUnavailable = _e("ServiceUnavailable", 503, "Service is unavailable. Please try again later.")
SlowDown = _e("SlowDown", 503, "Please reduce your request rate.")
