from polaris.modules.mail.tgformat import MAX_MESSAGE_LENGTH, build_fallback_html, build_rich_markdown


def test_body_is_collapsible_and_attachments_stay_visible():
    md = build_rich_markdown("Gmail", "Тема", "Иван <i@x.ru>", "me@x.ru", "25.10.2026", "Привет", ["a.pdf"])
    assert "<details><summary>Текст письма</summary>" in md
    assert md.index("</details>") < md.index("**Вложения:**")
    assert md.startswith("📩")


def test_markup_in_mail_cannot_break_message():
    md = build_rich_markdown("A", "Счёт $100 == ok", "x", "y", "d",
                             "Цена $5 <script>alert(1)</script>\n# не заголовок\n||не спойлер||", [])
    assert "<script>" not in md and "&lt;script" in md
    assert "\\$5" in md and "\\#" in md and "\\|\\|" in md and "\\=\\=" in md


def test_limits():
    big = build_rich_markdown("A", "S", "F", "T", "D", "x" * 100000, [f"f{i}" for i in range(50)])
    assert len(big) <= MAX_MESSAGE_LENGTH and big.count("<details>") == 1 and "</details>" in big
    heads = build_rich_markdown("A", "S" * 99999, "F" * 99999, "T" * 99999, "D", "body", [])
    assert len(heads) <= MAX_MESSAGE_LENGTH


def test_fallback_html():
    fb = build_fallback_html("Gmail", "<b>", "a&b", "t", "d", "тест <x> & " + "я" * 9000, ["a"])
    assert len(fb) <= 4096 and "<blockquote expandable>" in fb and "&lt;x&gt;" in fb
