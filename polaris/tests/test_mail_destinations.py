import pytest

from polaris.modules.mail.destinations import DestinationError, parse_destination


@pytest.mark.parametrize(
    "raw, chat_id, thread, kind",
    [
        ("123456789", "123456789", None, "user"),
        ("-1003281520729", "-1003281520729", None, "group"),
        ("-1003281520729/1279", "-1003281520729", 1279, "topic"),
        ("3281520729/1279", "-1003281520729", 1279, "topic"),
        ("1003281520729/1279", "-1003281520729", 1279, "topic"),
        ("https://t.me/c/3281520729/1279", "-1003281520729", 1279, "topic"),
        ("t.me/c/3281520729/1279/5555?single", "-1003281520729", 1279, "topic"),
        ("  \u22121003281520729 ", "-1003281520729", None, "group"),
        ("@my_channel", "@my_channel", None, "channel"),
        ("-4012345", "-4012345", None, "group"),
    ],
)
def test_parse_ok(raw, chat_id, thread, kind):
    d = parse_destination(raw)
    assert (d.chat_id, d.thread_id, d.kind) == (chat_id, thread, kind)


@pytest.mark.parametrize("raw", ["", "abc", "0", "12/0", "https://t.me/c/abc/1", "@a", "1.5", "123/45/6"])
def test_parse_rejects(raw):
    with pytest.raises(DestinationError):
        parse_destination(raw)
