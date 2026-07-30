"""Send through a real, third-party SMTP server.

MailPit is a catch-all that accepts anything. A commercial MTA is the harder
test, and the one that settles the question this package's riskiest default
raises: **does a real provider accept and deliver a Bcc-only envelope?**

Everything comes from the environment. Nothing is hardcoded, because this
repository is public and a real address or credential committed here would be
permanent.

    set -a; . ./.env.smtp; set +a
    cd example && uv run --project .. python smoke_real_smtp.py

Required: SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_TO
"""

import os
import sys

REQUIRED = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")
missing = [name for name in REQUIRED if not os.environ.get(name)]
if missing:
    print(f"Missing environment variables: {', '.join(missing)}")
    print("Load them first:  set -a; . ./.env.smtp; set +a")
    sys.exit(2)

PORT = int(os.environ["SMTP_PORT"])

import django  # noqa: E402
from django.conf import settings  # noqa: E402

settings.configure(
    DEBUG=False,
    SECRET_KEY="real-smtp-smoke",
    USE_TZ=True,
    INSTALLED_APPS=["django.contrib.postgres", "notifier"],
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PGDATABASE", "notifier_example"),
            "USER": os.environ.get("PGUSER", "notifier"),
            "PASSWORD": os.environ.get("PGPASSWORD", "notifier"),
            "HOST": os.environ.get("PGHOST", "127.0.0.1"),
            "PORT": os.environ.get("PGPORT", "45432"),
        }
    },
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "OPTIONS": {}}],
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST=os.environ["SMTP_HOST"],
    EMAIL_PORT=PORT,
    EMAIL_HOST_USER=os.environ["SMTP_USER"],
    EMAIL_HOST_PASSWORD=os.environ["SMTP_PASSWORD"],
    # Port 465 is implicit TLS (SMTPS); 587 is STARTTLS. They are mutually
    # exclusive in Django, and getting this wrong is the classic first failure
    # against a real provider.
    EMAIL_USE_SSL=PORT == 465,
    EMAIL_USE_TLS=PORT == 587,
    EMAIL_TIMEOUT=30,
    DEFAULT_FROM_EMAIL=os.environ["SMTP_FROM"],
    DJANGO_ENV="production",
    NOTIFIER={"USE_CELERY": False},
)
django.setup()

from notifier import send  # noqa: E402
from notifier.models import LogResult, Status  # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{PASS if condition else FAIL}] {label}")
    if detail and not condition:
        print(f"         {detail}")
    if not condition:
        _failures.append(label)


def redacted(address: str) -> str:
    name, _, domain = address.partition("@")
    return f"{name[:2]}***@{domain}"


def main() -> int:
    host, to = os.environ["SMTP_HOST"], os.environ["SMTP_TO"]
    print("=" * 72)
    print("Real SMTP verification")
    print("=" * 72)
    print(f"  server    {host}:{PORT}  (implicit TLS: {PORT == 465})")
    print(f"  recipient {redacted(to)}")

    print("\nSingle recipient through a real MTA (auto -> to)")
    notification = send(
        channel="email",
        recipients=[to],
        subject="django-notifier-hub: 单收件人应显示在收件人栏 ✉",
        body_html=(
            "<h2>Auto recipient mode</h2>"
            "<p>One recipient, so this should arrive with your address visible in the "
            "To field -- unlike the earlier Bcc-only test, which showed a blank "
            'recipient. Track link: <a href="https://app.example.com/track/A-1001">here</a>.</p>'
            "<ul><li>2x Widget</li><li>1x Gadget</li></ul>"
        ),
        source="smoke.real_smtp",
    )
    notification.refresh_from_db()

    log = notification.logs.first()
    check(
        "the provider accepted the message",
        notification.status == Status.SENT,
        (notification.last_error or "").splitlines()[0] if notification.last_error else "",
    )
    check("the log records a send", log is not None and log.result == LogResult.SENT)
    check(
        "auto resolved to `to` for the single recipient",
        log is not None and log.provider_response.get("mode") == "to",
        f"mode was {log.provider_response.get('mode') if log else None!r}",
    )

    if notification.status == Status.SENT:
        print("\n  Derived plaintext part actually transmitted:")
        for line in notification.rendered_text.splitlines():
            print(f"    | {line}")
        check(
            "the tracking URL survived into the plaintext part",
            "https://app.example.com/track/A-1001" in notification.rendered_text,
        )
        check(
            "the non-ASCII subject round-tripped",
            any("\u4e00" <= ch <= "\u9fff" for ch in notification.rendered_subject),
            notification.rendered_subject,
        )

    print("\n" + "=" * 72)
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("Accepted by the provider. Confirm the recipient field now shows your")
    print("address, where the earlier Bcc-only message showed it blank.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
