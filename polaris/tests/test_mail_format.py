import re

from polaris.modules.mail.tgformat import (
    FALLBACK_MAX_LENGTH,
    MAX_MESSAGE_LENGTH,
    build_fallback_html,
    build_rich_markdown,
    normalize_style,
    parse_body,
    render_markdown,
)

NOTION_BODY = """Workspace fundamentals, done right.
( https://academy.notion.com/building-basics/323676/scorm/yz9o79x912bs?utm_source=customer.io )
Navigating your workspace and sidebar
( https://academy.notion.com/collaborating-in-notion/323934/scorm/d8o1c1v7l1fv?utm_source=customer.io )
Share and publish your work
Notion Consultant ( https://notion.so?utm_campaign=1 )
Don't want this kind of email? Unsubscribe from Product Education and How-To's ( https://track.customer.io/unsubscribe/abc ). Manage preferences ( https://track.customer.io/manage/abc )."""


def test_header_title_and_no_mailbox_line():
    md = build_rich_markdown("Gmail", "Тема", "Иван <i@x.ru>", "me@x.ru", "25.10.2026", "Привет", ["a.pdf"])
    assert md.startswith("📩 **Письмо**")
    assert "**Кому:** me@x\\.ru" in md and "**Тема:** Тема" in md
    assert "**Ящик:**" not in md and "Gmail" not in md
    assert md.index("Привет") < md.index("**Вложения:**")


def test_body_is_a_collapsible_block_with_plain_text_inside_not_a_quote():
    md = build_rich_markdown("A", "S", "F", "T", "D", "Первая строка\n\nВторой абзац\nтретья", [])
    assert "<details><summary>Текст письма</summary>" in md and md.rstrip().endswith("</details>")
    inner = md[md.index("</summary>") + len("</summary>"): md.index("</details>")]
    assert "\n> " not in inner and not inner.lstrip("\n").startswith(">")      # без цитаты
    assert "Второй абзац" in inner and "третья" in inner
    # внутри блока нет пустых строк (кроме обязательной после summary и перед </details>):
    # иначе разметка сочла бы блок законченным
    assert "\n\n" not in inner.strip("\n")


def test_plain_style_has_no_block():
    md = build_rich_markdown("A", "S", "F", "T", "D", "Привет", [], style="plain")
    assert "<details>" not in md and "Привет" in md


def test_style_names():
    assert normalize_style("QUOTE") == "quote" and normalize_style(" plain ") == "plain"
    assert normalize_style("") == "details" and normalize_style(None) == "details" and normalize_style("что-то") == "details"


def test_markup_in_mail_cannot_break_message():
    md = build_rich_markdown("A", "Счёт $100 == ok", "x", "y", "d",
                             "Цена $5 <script>alert(1)</script>\n# не заголовок\n||не спойлер||", [])
    assert "<script>" not in md and "&lt;script" in md
    assert "\\$5" in md and "\\#" in md and "\\|\\|" in md and "\\=\\=" in md


def test_card_links_use_the_caption_below_as_clickable_title():
    md = build_rich_markdown("A", "S", "F", "T", "D", NOTION_BODY, [])
    assert "[Navigating your workspace and sidebar](https://academy.notion.com/building-basics/" in md
    assert "[Share and publish your work](https://academy.notion.com/collaborating-in-notion/" in md
    assert "( https://" not in md and "(  https" not in md      # никаких ссылок «в скобочках»


def test_inline_links_take_the_words_before_the_url_as_title():
    md = build_rich_markdown("A", "S", "F", "T", "D", NOTION_BODY, [])
    assert "[Notion Consultant](https://notion.so?utm_campaign=1)" in md
    assert "[Unsubscribe from Product Education and How\\-To's](https://track.customer.io/unsubscribe/abc)" in md
    assert "[Manage preferences](https://track.customer.io/manage/abc)" in md
    assert "Don't want this kind of email?" in md and "[Don't want" not in md   # лишний текст в ссылку не попал


def test_standalone_link_without_caption_gets_host_title_and_bare_urls_are_clickable():
    text = render_markdown(parse_body("( https://www.a.example/x )"))
    assert text == "[🔗 a\\.example](https://www.a.example/x)"
    text = render_markdown(parse_body("Смотри https://www.example.com/path/to/page/?a=1&b=2, потом дальше"))
    assert "[example\\.com/path/to/page](https://www.example.com/path/to/page/?a=1&b=2), потом дальше" in text


def test_urls_with_parentheses_do_not_break_markdown_links():
    text = render_markdown(parse_body("См. https://en.wikipedia.org/wiki/Mail_(disambiguation)."))
    assert "(https://en.wikipedia.org/wiki/Mail_%28disambiguation%29)" in text


def test_only_http_links_become_clickable():
    text = render_markdown(parse_body("Нажми ( javascript:alert(1) ) или ( ftp://x.example/a ) или (https://ok.example)"))
    assert "javascript" in text and "](javascript" not in text and "](ftp" not in text
    assert "](https://ok.example)" in text


def test_limits():
    big = build_rich_markdown("A", "S", "F", "T", "D", "x" * 100000, [f"f{i}" for i in range(50)])
    assert len(big) <= MAX_MESSAGE_LENGTH and "</details>" in big      # блок остаётся закрытым
    heads = build_rich_markdown("A", "S" * 99999, "F" * 99999, "T" * 99999, "D", "body", [])
    assert len(heads) <= MAX_MESSAGE_LENGTH


def test_long_body_is_cut_on_a_line_boundary_and_keeps_links_whole():
    body = "\n".join(f"Ссылка {i} ( https://example.com/page/{i} )" for i in range(2000))
    md = build_rich_markdown("A", "S", "F", "T", "D", body, [])
    assert len(md) <= MAX_MESSAGE_LENGTH and "… \\(текст сокращён\\)" in md and md.rstrip().endswith("</details>")
    for dest in re.findall(r"\]\(([^)]*)\)", md):
        assert re.fullmatch(r"https://example\.com/page/\d+", dest), dest   # ни одной оборванной ссылки


def test_fallback_html():
    fb = build_fallback_html("Gmail", "<b>", "a&b", "t", "d", "тест <x> & " + "я" * 9000, ["a"])
    assert len(fb) <= 4096 and "&lt;x&gt;" in fb
    assert fb.startswith("📩 <b>Письмо</b>") and "Ящик" not in fb
    assert "<blockquote expandable>" in fb and fb.count("<blockquote") == fb.count("</blockquote>")
    plain = build_fallback_html("Gmail", "S", "F", "T", "D", "текст", [], style="plain")
    assert "<blockquote" not in plain and "текст" in plain


def test_fallback_html_links_are_clickable_and_never_cut_in_half():
    fb = build_fallback_html("A", "S", "F", "T", "D", NOTION_BODY, [])
    assert '<a href="https://notion.so?utm_campaign=1">Notion Consultant</a>' in fb
    assert '<a href="https://academy.notion.com/building-basics/323676/scorm/yz9o79x912bs?utm_source=customer.io">Navigating your workspace and sidebar</a>' in fb
    long_body = "\n".join(f"Ссылка {i} ( https://example.com/page/{i} )" for i in range(2000))
    fb = build_fallback_html("A", "S", "F", "T", "D", long_body, [])
    assert len(fb) <= FALLBACK_MAX_LENGTH and fb.count("<a ") == fb.count("</a>")
