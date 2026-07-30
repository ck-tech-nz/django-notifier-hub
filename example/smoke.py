"""Manual verification against real infrastructure.

The test suite proves behaviour against `locmem` and eager Celery. That cannot
tell you how a real SMTP server treats a Bcc-only envelope, how a real MIME
encoder handles a non-ASCII subject, or what a real worker does when it dies
mid-task. This script exercises exactly those.

    docker compose up -d
    cd example && uv run --project .. python smoke.py

Read the results in MailPit at http://localhost:8025, or let the assertions
below read them for you through its REST API.
"""

import json
import os
import sys
import time
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.mail import get_connection  # noqa: E402

from notifier import send  # noqa: E402
from notifier.models import LogResult, Notification, NotificationTemplate, Status  # noqa: E402

MAILPIT = "http://127.0.0.1:8025"

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{PASS if condition else FAIL}] {label}")
    if detail and not condition:
        print(f"         {detail}")
    if not condition:
        _failures.append(label)


def mailpit(path: str, method: str = "GET"):
    request = urllib.request.Request(f"{MAILPIT}/api/v1{path}", method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # DELETE answers with a plain "ok", not JSON.
        return body.decode("utf-8", "replace")


def reset_mailbox() -> None:
    mailpit("/messages", method="DELETE")


def latest_message() -> dict:
    messages = mailpit("/messages")["messages"]
    if not messages:
        raise AssertionError("MailPit received nothing at all.")
    return mailpit(f"/message/{messages[0]['ID']}")


def raw_source(message_id: str) -> str:
    with urllib.request.urlopen(f"{MAILPIT}/api/v1/message/{message_id}/raw", timeout=10) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------


def test_smtp_reachable() -> None:
    print("\nSMTP reachability")
    try:
        connection = get_connection()
        connection.open()
        connection.close()
        check("Django can open an SMTP connection to MailPit", True)
    except OSError as exc:
        check("Django can open an SMTP connection to MailPit", False, str(exc))
        print("\nIs the stack up?  docker compose up -d")
        sys.exit(1)


def test_bcc_default_over_real_smtp() -> None:
    """The highest-risk default: bcc diverges from send_mail, and until now no
    real SMTP server had ever seen one of these envelopes."""
    print("\nBCC default, three recipients, real SMTP")
    reset_mailbox()

    notification = send(
        channel="email",
        recipients=["a@example.com", "b@example.com", "c@example.com"],
        subject="Bcc envelope check",
        body_text="Body.",
        source="smoke.bcc",
    )
    notification.refresh_from_db()

    check("notification reached sent", notification.status == Status.SENT, notification.last_error)
    check("one log row", notification.logs.count() == 1)

    time.sleep(0.4)
    messages = mailpit("/messages")["messages"]
    check("the server accepted exactly one message", len(messages) == 1, f"got {len(messages)}")

    message = latest_message()
    delivered_to = {a["Address"] for a in message.get("To") or []}
    bcc_listed = {a["Address"] for a in message.get("Bcc") or []}
    check("no recipient appears in the To header", not delivered_to, f"To: {delivered_to}")
    check(
        "all three were delivered as envelope recipients",
        len(bcc_listed) == 3,
        f"Bcc: {bcc_listed}",
    )

    # The decisive check, and the reason this script exists. Assert against what
    # Django transmits, NOT against what MailPit stored: MailPit is a catch-all
    # and helpfully *reconstructs* a Bcc header from the envelope so a developer
    # can see who received a copy. A real MTA does no such thing. Reading its
    # stored copy would report a leak that never went over the wire.
    from notifier.backends.email import DjangoEmailBackend
    from notifier.rendering import RenderedMessage

    wire = DjangoEmailBackend()._build(
        RenderedMessage(subject="Bcc envelope check", text="Body.", html=""), None, None
    )
    wire.bcc = ["a@example.com", "b@example.com", "c@example.com"]
    transmitted = wire.message().as_string()

    check("no Bcc header is transmitted", "Bcc:" not in transmitted)
    check(
        "no recipient address appears anywhere in the transmitted bytes",
        not any(a in transmitted for a in wire.bcc),
        "an address was serialised into the message",
    )
    check(
        "the addresses travel only as envelope recipients",
        set(wire.recipients()) == set(wire.bcc),
        f"RCPT TO was {wire.recipients()}",
    )


def test_non_ascii_subject_and_body() -> None:
    """Never exercised: locmem stores Python strings and never MIME-encodes."""
    print("\nNon-ASCII subject and body through a real encoder")
    reset_mailbox()

    subject = "订单 A-1001 已发货 ✉"
    notification = send(
        channel="email",
        recipients=["to@example.com"],
        subject=subject,
        body_text="您好,您的订单已经发出。",
        source="smoke.i18n",
    )
    notification.refresh_from_db()
    check("sent", notification.status == Status.SENT, notification.last_error)

    time.sleep(0.4)
    message = latest_message()
    check(
        "the subject survives the MIME round trip",
        message["Subject"] == subject,
        f"got {message['Subject']!r}",
    )
    check(
        "the body survives the MIME round trip",
        "您的订单已经发出" in (message.get("Text") or ""),
        f"got {(message.get('Text') or '')[:80]!r}",
    )
    header_block = raw_source(message["ID"]).split("\r\n\r\n")[0]
    check(
        "the subject header is encoded, not sent as raw bytes",
        "=?utf-8?" in header_block.lower(),
        "expected RFC 2047 encoding in the Subject header",
    )


def test_html_email_carries_a_usable_plaintext_part() -> None:
    """The html2text change, verified as a real MIME multipart."""
    print("\nHTML email: multipart and derived plaintext")
    reset_mailbox()

    NotificationTemplate.objects.update_or_create(
        key="smoke-html",
        channel="email",
        defaults={
            "name": "HTML smoke",
            "subject": "Order {{ order }} shipped",
            "body_html": (
                "<h2>Order {{ order }} shipped</h2>"
                '<p>Track it <a href="{{ site_url }}/track/{{ order }}">here</a>.</p>'
                "<ul><li>2x Widget</li><li>1x Gadget</li></ul>"
            ),
        },
    )

    notification = send(
        channel="email",
        recipients=["to@example.com"],
        key="smoke-html",
        context={"order": "A-1001"},
        source="smoke.html",
    )
    notification.refresh_from_db()
    check("sent", notification.status == Status.SENT, notification.last_error)

    time.sleep(0.4)
    message = latest_message()
    text = message.get("Text") or ""
    html = message.get("HTML") or ""

    check("the message has an HTML part", "<h2>" in html)
    check("the message has a plaintext part", bool(text.strip()))
    check(
        "the tracking URL survives into the plaintext part",
        "https://app.example.com/track/A-1001" in text,
        f"plaintext was {text!r}",
    )
    check(
        "list items do not run together",
        "2x Widget" in text and "1x Gadget" in text and "2x Widget1x Gadget" not in text,
        f"plaintext was {text!r}",
    )
    check(
        "the context processor supplied the absolute origin",
        "https://app.example.com" in html,
    )


def test_suppression_reaches_no_server() -> None:
    """Non-production with no DefaultRecipient must not open an SMTP session."""
    print("\nNon-production suppression")
    reset_mailbox()

    from django.test import override_settings

    with override_settings(DJANGO_ENV="staging"):
        notification = send(
            channel="email",
            recipients=["real-customer@example.com"],
            subject="Should never be delivered",
            body_text="Body.",
            source="smoke.suppression",
        )

    notification.refresh_from_db()
    log = notification.logs.first()
    check("log records suppression", log.result == LogResult.SUPPRESSED, log.result)
    check("status stays at ready", notification.status == Status.READY, notification.status)

    time.sleep(0.4)
    check("the SMTP server saw nothing", mailpit("/messages")["total"] == 0)


def test_separate_mode_is_one_message_per_address() -> None:
    print("\nRECIPIENT_MODE=separate")
    reset_mailbox()

    from django.test import override_settings

    with override_settings(NOTIFIER={"USE_CELERY": False, "EMAIL": {"RECIPIENT_MODE": "separate"}}):
        notification = send(
            channel="email",
            recipients=["a@example.com", "b@example.com"],
            subject="Separate mode",
            body_text="Body.",
            source="smoke.separate",
        )

    notification.refresh_from_db()
    time.sleep(0.4)
    check("two messages left the process", mailpit("/messages")["total"] == 2)
    check(
        "still one notification row", Notification.objects.filter(pk=notification.pk).count() == 1
    )
    check("still one log row", notification.logs.count() == 1)


def main() -> int:
    print("=" * 72)
    print("Stage 0: real SMTP verification")
    print("=" * 72)

    test_smtp_reachable()
    test_bcc_default_over_real_smtp()
    test_non_ascii_subject_and_body()
    test_html_email_carries_a_usable_plaintext_part()
    test_suppression_reaches_no_server()
    test_separate_mode_is_one_message_per_address()

    print("\n" + "=" * 72)
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed. Inspect the messages at http://localhost:8025")
    return 0


if __name__ == "__main__":
    sys.exit(main())
