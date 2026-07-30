"""The HTML-to-text fallback.

Only used when an HTML body has no authored `body_text`. The bar is "a customer
could act on this", not "byte-perfect" -- which `strip_tags` fails, because it
drops link targets entirely.
"""

import pytest

from notifier.html2text import html_to_text


@pytest.mark.parametrize("value", ["", None])
def test_empty_input_gives_empty_output(value):
    assert html_to_text(value) == ""


def test_link_targets_survive():
    """The failure that motivated this module: strip_tags loses the URL."""
    html = '<p>Track it <a href="https://x.example/1">here</a>.</p>'

    assert html_to_text(html) == "Track it here (https://x.example/1)."


def test_a_link_whose_label_is_already_the_url_is_not_doubled():
    html = '<p>Visit <a href="https://x.example">https://x.example</a> today.</p>'

    assert html_to_text(html) == "Visit https://x.example today."


def test_a_link_whose_label_contains_the_host_is_not_doubled():
    html = '<p><a href="https://x.example/a/b">x.example/a/b</a></p>'

    assert html_to_text(html) == "x.example/a/b"


def test_mailto_keeps_the_address_and_drops_the_scheme():
    html = '<p><a href="mailto:help@example.com">Email support</a></p>'

    assert html_to_text(html) == "Email support (help@example.com)"


def test_an_empty_label_falls_back_to_the_target():
    html = '<p>See <a href="https://x.example/doc"></a></p>'

    assert "https://x.example/doc" in html_to_text(html)


@pytest.mark.parametrize("href", ["#section", "javascript:void(0)", "data:text/plain,x"])
def test_hrefs_a_reader_cannot_act_on_are_omitted(href):
    html = f'<p>Click <a href="{href}">here</a>.</p>'

    assert html_to_text(html) == "Click here."


def test_list_items_do_not_run_together():
    """strip_tags produces "2x Widget1x Gadget"."""
    html = "<ul><li>2x Widget</li><li>1x Gadget</li></ul>"

    assert html_to_text(html) == "- 2x Widget\n- 1x Gadget"


def test_paragraphs_are_separated_by_a_blank_line():
    html = "<p>First.</p><p>Second.</p>"

    assert html_to_text(html) == "First.\n\nSecond."


def test_br_is_a_single_line_break():
    html = "<p>Line one<br>Line two</p>"

    assert html_to_text(html) == "Line one\nLine two"


def test_headings_separate_from_following_text():
    html = "<h2>Title</h2><p>Body.</p>"

    assert html_to_text(html) == "Title\n\nBody."


def test_source_line_breaks_do_not_become_output_breaks():
    """HTML collapses whitespace, so the plaintext should too."""
    html = "<p>Hello Ann,\n   your order is\n   on its way.</p>"

    assert html_to_text(html) == "Hello Ann, your order is on its way."


def test_style_and_script_content_is_dropped():
    html = "<style>p { color: red }</style><p>Visible.</p><script>alert(1)</script>"

    assert html_to_text(html) == "Visible."


def test_entities_are_unescaped():
    html = "<p>Ann &amp; Bo &lt;3 &quot;widgets&quot; &nbsp;now</p>"

    result = html_to_text(html)
    assert "Ann & Bo" in result
    assert "<3" in result
    assert '"widgets"' in result


def test_image_alt_text_is_kept():
    html = '<p><img src="logo.png" alt="Acme logo"> Welcome.</p>'

    assert html_to_text(html) == "[Acme logo] Welcome."


def test_an_image_without_alt_text_leaves_nothing_behind():
    html = '<p><img src="spacer.gif"> Welcome.</p>'

    assert html_to_text(html) == "Welcome."


def test_table_cells_are_tab_separated_and_rows_are_lines():
    html = "<table><tr><td>Widget</td><td>2</td></tr><tr><td>Gadget</td><td>1</td></tr></table>"

    assert html_to_text(html) == "Widget\t2\nGadget\t1"


def test_hr_becomes_a_visible_rule():
    html = "<p>Above.</p><hr><p>Below.</p>"

    assert html_to_text(html) == "Above.\n\n---\n\nBelow."


def test_pre_keeps_its_own_whitespace():
    html = "<pre>line 1\n  indented\n</pre>"

    assert "  indented" in html_to_text(html)


def test_never_more_than_one_blank_line():
    html = "<div><p>A.</p></div><div><br><br><p>B.</p></div>"

    assert "\n\n\n" not in html_to_text(html)


def test_output_has_no_trailing_whitespace_on_any_line():
    html = "<p>Trailing   </p><p>   Leading</p>"

    result = html_to_text(html)
    assert all(line == line.rstrip() for line in result.splitlines())


def test_malformed_html_does_not_raise():
    """Templates are author-edited; a stray tag must not break a send."""
    html = "<p>Unclosed <b>bold <a href='https://x.example'>link</p>"

    assert "Unclosed" in html_to_text(html)
    assert "https://x.example" in html_to_text(html)


def test_self_closing_tags_are_handled():
    html = "<p>One<br/>Two<img alt='pic'/></p>"

    assert html_to_text(html) == "One\nTwo[pic]"


def test_a_realistic_transactional_email():
    html = """
    <html><body>
      <h2>Order A-1001 shipped</h2>
      <p>Hello Ann, your order is on its way.
         Track it <a href="https://app.example.com/track/A-1001">here</a>.</p>
      <ul><li>2x Widget</li><li>1x Gadget</li></ul>
      <p>Questions? <a href="mailto:help@example.com">Email support</a>.</p>
    </body></html>
    """

    assert html_to_text(html) == (
        "Order A-1001 shipped\n"
        "\n"
        "Hello Ann, your order is on its way. Track it here "
        "(https://app.example.com/track/A-1001).\n"
        "\n"
        "- 2x Widget\n"
        "- 1x Gadget\n"
        "\n"
        "Questions? Email support (help@example.com)."
    )
