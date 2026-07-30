# django-notifier-hub

Notifications as rows. Your code writes a `Notification`; the package delivers it by email, SMS or
in-site, and records every attempt.

```python
from notifier import send

send(
    channel="email",
    recipients=["customer@example.com"],
    key="order-shipped",  # a template stored in the database
    context={"order": order.number},
    source="order.shipped",  # what caused this, for later triage
)
```

There is no `send_email()` to call from a view, no queue to wire up, and no branch for "does this
project have Celery". A row reaching `status="ready"` is what causes delivery.

| | |
| --- | --- |
| Install | `pip install django-notifier-hub` |
| Import path / app label | `notifier` |
| Python | ≥ 3.13 |
| Django | ≥ 5.2.8 (5.2 LTS, 6.0) |
| Database | **PostgreSQL only** |
| Hard dependencies | Django only (`[celery]` extra for async delivery) |
| Licence | MIT |

> **Status: pre-release.** All four milestones are implemented and tested (219 tests, both a full
> and a headless Django configuration), but `0.1.0` is not published yet. The public API is a
> compatibility promise from `0.1.0` onward, not before.

## Requirements, in full

**PostgreSQL is not optional.** `ArrayField`, a GIN index and array-equality check constraints are
load-bearing. There is no SQLite or MySQL fallback, and `manage.py check` will tell you so.

**No user model needed.** There is no foreign key to `AUTH_USER_MODEL` and no dependency on
`django.contrib.auth`, `admin` or `contenttypes`, so this runs on a headless service with no user
accounts at all. A complete install:

```python
INSTALLED_APPS = [
    "django.contrib.postgres",  # required: Django only allows ArrayField when this is installed
    "notifier",
]

TEMPLATES = [
    # Required: bodies render through your template engine, so your own tags and
    # filters work. No context processors are needed -- there is no request.
    {"BACKEND": "django.template.backends.django.DjangoTemplates"},
]
```

Then `python manage.py migrate`. `manage.py check` reports anything missing with an actionable
message rather than leaving you to guess.

## How it works

1. **You create a row.** Either through `send()` / `send_multi()`, or `Notification.objects.create()`
   directly when you want to review something before it goes out.
2. **Reaching `ready` triggers delivery** — on create, or on a transition into `ready` from any other
   status. A `ready` → `ready` re-save does not re-send.
3. **Dispatch waits for the transaction to commit**, so a worker never queries a row your
   transaction has not committed, and a rollback sends nothing.
4. **One `NotificationLog` row per attempt** — success, failure, suppression or skip. The log is a
   history, not a last-known state.

```text
draft ──mark_ready()──> ready ──dispatch──> sending ──┬── ok ────> sent
                          ▲                           │
                          │                           └── error ─> failed
                          └────── mark_ready() / resend() ──────────┘
```

`suppressed` and `skipped` leave the status at `ready`: the notification never claimed to have been
sent, so re-running it in a working environment just works.

## Non-production safety

**Outside production, real recipients are never contacted.** The environment resolves as
`NOTIFIER["ENV"]` → `settings.DJANGO_ENV` → `$DJANGO_ENV` → `"dev"`. Anything not in
`PRODUCTION_ENVS` is non-production — including a project that configures nothing at all, which is
deliberate: the accident this produces is "staging sent nothing", never "staging emailed the
customer list".

In non-production, recipients are replaced by the enabled `DefaultRecipient` rows for that channel.
If there are none, the send is **suppressed** and nothing leaves the process. The log keeps the
original list in `requested_recipients`, so you can see what *would* have gone out.

In-site is exempt: it has no transport, and the row is persisted before dispatch, so gating it would
protect nobody while making local development impossible.

## Templates

`NotificationTemplate` rows hold Django template strings, keyed by `(key, channel)` — one logical
key resolves per channel, which is what lets a single `send_multi()` call reach three channels.

```python
NotificationTemplate.objects.create(
    key="order-shipped",
    channel="email",
    name="Order shipped",
    subject="Order {{ order }} has shipped",
    body_text="Hello, order {{ order }} is on its way.",
    body_html="<p>Hello, order <strong>{{ order }}</strong> is on its way.</p>",
)
```

Rendering is the **full Django engine** — loops, filters, your own template tags, autoescaping — so
it behaves as a `TemplateResponse` does, with one difference: **there is no request.**
`{{ request }}`, `{{ user }}` and `{% csrf_token %}` render empty, and `{% url %}` gives a path
rather than an absolute URL. Pass anything else the template needs in `context`, and supply your
site's origin through `TEMPLATE_CONTEXT_PROCESSOR`.

What was actually sent is snapshotted onto the notification (`rendered_subject`, `rendered_text`,
`rendered_html`) at send time. Editing a template later never rewrites history.

> **Security:** a full template engine means anyone who can edit a `NotificationTemplate` can
> execute template tags in your server process. Treat `change_notificationtemplate` as a privileged
> permission.

## Email

A notification with several addresses is **one message**, one SMTP transaction, one log row.

**By default the header depends on how many recipients there are:** one goes in `To:`, two or more
go in `Bcc:`.

```python
NOTIFIER = {"EMAIL": {"RECIPIENT_MODE": "auto"}}  # "auto" (default) | "to" | "bcc" | "separate"
```

With two or more recipients, putting them all in `To:` leaks one customer's address to another and
cannot be undone, so the list is hidden. With exactly one recipient there is nobody to leak to, so
the mail is sent normally — a Bcc-only envelope arrives with a blank recipient field, which looks
wrong for a receipt or a password reset and scores worse with some spam filters.

The count is the *effective* one, after non-production redirection: three recipients collapsing to
one QA mailbox in staging send as `to`.

Use an explicit `to` for group threads where replies should reach everyone, `bcc` to hide the list
unconditionally, and `separate` when each recipient needs their own message.

SMTP settings are Django's: `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`,
`mail.outbox` in tests, and any third-party email backend all work unchanged. The package defines
none of its own. `NOTIFIER["EMAIL"]["CONNECTION"]` exists only to send over a *different* server.

### The plaintext part

An HTML email always gets a `text/plain` alternative. If you write `body_text`, that is used
verbatim. If you write only HTML, it is derived — keeping link targets, paragraphs and list bullets:

```text
<p>Track it <a href="https://app.example.com/track/A-1001">here</a>.</p>
<ul><li>2x Widget</li><li>1x Gadget</li></ul>

  Track it here (https://app.example.com/track/A-1001).

  - 2x Widget
  - 1x Gadget
```

The derivation is stdlib-only and deliberately modest: no text wrapping, no table layout, no CSS.
For exact plaintext, write `body_text` — it always wins.

## Verifying against real infrastructure

The test suite runs against `locmem` and eager Celery, which cannot show you how a real MTA treats
your envelope or what a real worker does when it dies. `example/` is a real Django project wired to
a throwaway stack for exactly that:

```bash
docker compose up -d          # PostgreSQL, MailPit (SMTP + web UI), Redis
cd example
python manage.py migrate

python smoke.py               # real SMTP: envelopes, encoding, multipart, suppression

NOTIFIER_USE_CELERY=1 celery -A config worker -l info &
python smoke_celery.py        # real worker: the on_commit handoff, rollback
python smoke_celery_stuck.py  # the two ways a row can be stranded
```

Read the actual messages at <http://localhost:8025>.

`smoke_real_smtp.py` sends through a real third-party provider instead of MailPit. It takes
everything from the environment (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM`, `SMTP_TO`) and hardcodes nothing — this repository is public, so a real address or
credential committed here would be permanent.

These scripts have already earned their keep: the `auto` recipient mode and the corrected
no-worker status both came out of running them.

## SMS

The package ships the abstraction plus console and locmem backends. **No vendor SDK is or becomes a
dependency** — integrating a provider is a subclass:

```python
# myproject/sms.py
import requests
from notifier.backends.sms import BaseSmsBackend


class HttpGatewaySmsBackend(BaseSmsBackend):
    # Transport errors worth retrying. Anything else is treated as permanent.
    retryable_exceptions = (requests.RequestException,)

    def send_one(self, number, message):
        """Deliver to one number. Return a provider reference, or raise."""
        response = requests.post(
            "https://gateway.example.com/v1/sms",
            json={"to": number, "from": self.sender, "text": message.text},
            headers={"Authorization": f"Bearer {self.provider_options['api_key']}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["message_id"]
```

```python
NOTIFIER = {
    "BACKENDS": {"sms": "myproject.sms.HttpGatewaySmsBackend"},
    "SMS": {"FROM": "MYAPP", "OPTIONS": {"api_key": os.environ["SMS_API_KEY"]}},
}
```

`recipients` may hold one number or many. Providers are per-number, so the base class fans out and
records a per-number outcome in `provider_response` — while staying **one notification and one log
row**, because the unit of work is the send, not the number.

Two failure rules the base class applies for you:

- **Nothing got through and every failure was retryable** → the exception is re-raised, so retries
  engage.
- **Some got through** → reported as a failure but *not* retried, because retrying would send a
  second message to the numbers that worked.

## In-site

`InsiteBackend` performs no transport; the rendered snapshot is what your app reads.
`recipients` holds user ids as strings, and read state is `read_by`, an integer array your project
owns:

```python
notification.mark_read(request.user.pk)
notification.is_read_by(request.user.pk)

unread = Notification.objects.filter(channel="insite", recipients__contains=[str(user.pk)]).exclude(
    read_by__contains=[user.pk]
)
```

This is deliberately a convenience rather than an optimised feature: `read_by` is unindexed, and
marking read is a read-modify-write on a shared row. Fine for a secondary channel at low volume; if
in-site becomes primary, the answer is a join table, which is a v2 decision.

## Several channels at once

One message over email, SMS and in-site creates **three rows** sharing a `group_id` — not one row
with three channels, because a row has one status and three channels have three outcomes.

```python
from notifier import send_multi

group_id = send_multi(
    key="stock-alert",
    recipients={
        "email": ["ops@example.com"],
        "sms": ["+6421000001"],
        "insite": [str(user.pk)],
    },
    context={"sku": sku},
)

Notification.objects.filter(group_id=group_id)  # the whole group API
```

All rows are validated and created together, so a missing template creates nothing. After that
delivery is independent: one channel failing neither blocks nor rolls back the others, and outside
production a group can legitimately end up with mixed results.

## Async delivery

With `[celery]` installed and a broker configured, delivery is queued; otherwise it runs inline. The
same `deliver()` runs either way — only `NotificationLog.is_async` differs.

```python
NOTIFIER = {
    "USE_CELERY": True,  # None (default) auto-detects
    "CELERY_MAX_RETRIES": 3,
    "CELERY_RETRY_BACKOFF": 30,  # seconds, exponential: 30, 60, 120
}
```

> **Set `USE_CELERY` explicitly in production.** Auto-detection asks whether Celery is importable
> and has a broker — which is not the same as a worker being alive. A project with Celery installed
> and no worker running will enqueue tasks nobody consumes, leaving notifications at `ready` until a
> worker appears. A worker that dies *inside* a send is the different, worse case: that strands the
> row at `sending` with no log row, and `notifier_retry_failed --include-sending --older-than 30m`
> is what reclaims it.

Every attempt writes its own log row. When the retry budget is spent, `notification_exhausted`
fires once — "attempt 3 of 3 failed" and "we have stopped trying" are different events, and only
the second is worth paging someone about.

## Signals

All are sent with `sender=Notification`.

| Signal | Fired when | Extra kwargs |
| --- | --- | --- |
| `pre_send` | after render, before the backend runs | `notification`, `recipients`, `message` |
| `notification_sent` | the backend accepted the message | `notification`, `log` |
| `notification_failed` | the backend raised or rejected | `notification`, `log`, `exception` |
| `notification_suppressed` | non-production, no default recipient | `notification`, `log` |
| `notification_exhausted` | the final retry failed | `notification`, `log`, `attempts` |

## Settings

```python
NOTIFIER = {
    # environment gating
    "ENV": None,  # None -> settings.DJANGO_ENV -> $DJANGO_ENV -> "dev"
    "PRODUCTION_ENVS": ("prod", "production"),
    "NON_PROD_SUBJECT_PREFIX": "[{env}] ",  # "" disables
    "SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT": True,
    # backends
    "BACKENDS": {
        "email": "notifier.backends.email.DjangoEmailBackend",
        "sms": "notifier.backends.sms.ConsoleSmsBackend",
        "insite": "notifier.backends.insite.InsiteBackend",
    },
    "EMAIL": {
        "FROM": None,  # None -> DEFAULT_FROM_EMAIL
        "CONNECTION": None,  # dict of get_connection() kwargs
        "FAIL_SILENTLY": False,
        "RECIPIENT_MODE": "auto",
    },
    "SMS": {"FROM": None, "OPTIONS": {}},
    # delivery
    "USE_CELERY": None,
    "CELERY_QUEUE": None,
    "CELERY_MAX_RETRIES": 3,
    "CELERY_RETRY_BACKOFF": 30,
    "DISPATCH_ON_COMMIT": True,  # you almost certainly want this on
    # rendering
    "TEMPLATE_CONTEXT_PROCESSOR": None,  # dotted path -> dict, merged under context
    # housekeeping
    "LOG_RETENTION_DAYS": 90,
}
```

## Management commands

| Command | Purpose |
| --- | --- |
| `notifier_send_test --channel email --to a@example.com` | Smoke-test the configuration without touching business data. |
| `notifier_retry_failed [--older-than 2h] [--limit N] [--channel sms] [--dry-run]` | Re-dispatch failed notifications. |
| `notifier_prune_logs [--days N] [--dry-run]` | Delete logs past `LOG_RETENTION_DAYS`. |

> **`LOG_RETENTION_DAYS` is a policy, not a mechanism.** Nothing deletes anything on a timer. Schedule
> `notifier_prune_logs` yourself (cron, Celery beat, a systemd timer) — otherwise the log table grows
> forever while the setting claims 90 days.

## Testing your own project

```python
from django.core import mail
from notifier.backends import sms  # sms.outbox, the SMS analogue of mail.outbox

NOTIFIER = {
    "BACKENDS": {"sms": "notifier.backends.sms.LocmemSmsBackend"},
    "USE_CELERY": False,
}
```

If your tests run inside a transaction that rolls back — pytest-django's default, and Django's
`TestCase` — `transaction.on_commit` callbacks never fire, so nothing is dispatched. Either use
`django_capture_on_commit_callbacks` / `TestCase.captureOnCommitCallbacks`, or set
`NOTIFIER["DISPATCH_ON_COMMIT"] = False` in your test settings.

## Deliberately out of scope

Each of these is a decision, not an oversight:

- **Deduplication.** `ready → draft → ready` sends twice. No idempotency key.
- **Per-recipient delivery status.** A log row records the batch; per-address detail, where a
  provider returns it, lands in `provider_response`.
- **Scheduled send, bounce webhooks, attachments, digests, throttling.**
- **Fallback between channels.** "SMS only if the email fails" is not expressible; channels in a
  group are independent and simultaneous.

## Development

```bash
uv sync --all-extras --group dev
uv run ruff check . && uv run ruff format .
uv run pytest                              # requires a reachable PostgreSQL
uv run pytest --ds=tests.settings_minimal  # the headless configuration
```

Database parameters come from `NOTIFIER_TEST_DB_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_NAME`.

[`PRD.md`](PRD.md) is the design record: it states why each alternative was rejected, which is
usually more useful than the code when deciding whether to change something.

## Licence

MIT. Published for reuse on a best-effort basis, with no support commitment — issues and pull
requests are welcome but carry no response-time promise, and changes that do not serve this
project's own use may be declined.
