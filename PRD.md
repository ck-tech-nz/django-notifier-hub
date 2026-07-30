# django-notifier-hub — PRD

A reusable Django app that turns a persisted `Notification` row into an actually-delivered
message, keeps a record of every send attempt, and stays out of the way when the host project has
no Celery.

**It is primarily an email and SMS sending tool: the job is to get the message out.** In-site is a
third channel for the cases where a message should also land inside the app, deliberately kept
minimal — see [§6.4](#64-in-site).

| | |
| --- | --- |
| Distribution name | `django-notifier-hub` — see [note](#on-the-distribution-name) |
| Import path / app label | `notifier` |
| Settings namespace | `NOTIFIER` (single dict) |
| Repository | `https://github.com/ck-tech-nz/django-notifier-hub` (public) |
| Release | Public on PyPI, via Trusted Publishing — see [§9](#9-packaging--repo-layout) |
| Python | ≥ 3.13 (tested on 3.13, 3.14) |
| Django | ≥ 5.2.8 (5.2 LTS, 6.0) |
| Database | **PostgreSQL only** — see [§2.9](#29-postgresql-only) |
| Hard dependencies | Django only |
| Optional extras | `celery` |
| Status | Draft — see [Open questions](#open-questions) |

**Version policy.** Support only what upstream still supports, and drop a version the release
after it goes EOL. As of 2026-07-30 that is exactly Django 5.2 LTS (extended support to
2028-04) and Django 6.0 (to 2027-04) — every release ≤ 5.1 is already unsupported upstream — and
Python 3.13/3.14, the only two branches still in `bugfix` status.

**Why not Python 3.14 / Django 6.0 only?** For an internal-first package, dropping to a single
supported combination would be defensible — it halves the CI matrix and removes the discipline of
avoiding newer APIs. It was checked feature by feature and **nothing in this spec is constrained by
the 5.2 / 3.13 floor.** The one candidate was Django 6.0's `django.tasks`, which has no retries, no
Celery backend, and no production worker — evaluated and rejected in [§5.0](#50-why-not-djangotasks).
Nothing in the design needs Python 3.14 either.

The floor also runs *longer*, which is the counter-intuitive part: **Django 6.0's extended support
ends 2027-04, while 5.2 LTS runs to 2028-04.** Requiring the newer version would have shortened the
runway, not extended it. The next LTS is 6.2 (expected 2027-04), and that — not 6.0 — is the
upgrade worth planning for.

Two consequences worth stating, because they are easy to get wrong:

- **The Django floor is 5.2, not 5.0.** Django 5.0 and 5.1 reached EOL in 2024-12 and 2025-12
  respectively, so `Django>=5.0` would permit installing a release that no longer receives
  security fixes.
- **The floor is `5.2.8`, not `5.2`.** Python 3.14 support was added to the 5.2 series in
  **5.2.8**. Since 3.14 is a supported Python here, pinning `>=5.2` would allow the unsupported
  Django 5.2.0 + Python 3.14 combination. (Django 6.0 needs no such qualifier — it requires
  Python 3.12+ from 6.0.0.)

## On the distribution name

The natural name, `django-notifier`, **is taken on PyPI** — registered 2013 by an unrelated
"User and Group Notifications for Django" project, six releases, last upload 2013-10-27. PyPI
namespaces are global and first-come; an abandoned-but-released project is not reclaimable in
practice. A [PEP 541](https://peps.python.org/pep-0541/) transfer request was considered and
rejected as the primary path: it runs for months with an uncertain outcome, and PyPI generally
declines to transfer names that have actual releases behind them. Blocking the `0.1.0` release on
that is not a trade worth making.

So the distribution is `django-notifier-hub` and the repository matches it. **This renames nothing
else.** The import path, the app label, the settings namespace and the entire public API are
unchanged:

```python
# pip install django-notifier-hub
INSTALLED_APPS = [..., "notifier"]
from notifier.models import Notification
```

The one residual wrinkle: a dead 2015 distribution literally named `notifier` (an in-memory
pub-sub) also installs a top-level `notifier` module, so the two cannot coexist in one
environment. This is accepted — that package has been unmaintained for over a decade, and the
short import path is worth more than defending against it.

---

## 1. Goals

1. **Declarative send.** The host project creates a `Notification` row; the package delivers it.
   No imperative `send_email()` calls sprinkled through business code.
2. **Status-driven trigger.** Delivery is a side effect of a row reaching `ready`.
3. **Full audit trail.** Every attempt — success, failure, or suppression — writes a
   `NotificationLog` row.
4. **Zero-config async.** Use Celery when the project has it, run inline when it doesn't, with
   identical semantics either way.
5. **Safe non-production.** Outside prod, real recipients are never contacted.
6. **Installs on a service that has no users at all.** No FK to `AUTH_USER_MODEL`, and no dependency
   on `django.contrib.auth`, `admin`, or `contenttypes` — a headless microservice with
   `INSTALLED_APPS = ["notifier"]` is a supported configuration ([§2.10](#210-runs-on-a-service-with-no-users-no-auth-and-no-admin)).
   **Recipients are addresses, not users** — an email address or a phone number needs no account,
   and notifying an external party who will never log in must work
   ([§2.8](#28-recipients-are-addresses-readers-are-users)). The only place a Django user id appears
   anywhere is `read_by`, and it is a raw integer array, not a relation.

### Non-goals

Explicitly out of scope for v1. Each is listed with the consequence the host project must accept.

| Non-goal | Consequence |
| --- | --- |
| **Deduplication / idempotency** | `ready → draft → ready` sends twice. `Notification` carries no idempotency key and no unique constraint prevents a second delivery. Owner's decision — documented in [§4.3](#43-duplicate-sends). |
| **Per-recipient delivery status** | `recipients` is a list of strings, not a table. A log row records the whole batch, not one row per address. Per-address detail, when the provider returns it, lands in `NotificationLog.provider_response`. |
| **Scheduled / delayed send** | No `send_at`. The host project schedules the status flip itself. |
| **Bounce & delivery webhooks** | No inbound endpoints. `sent` means "handed to the provider", not "landed in an inbox". |
| **Concrete SMS providers** | Only the abstraction plus a console backend ship in the package. See [§6.3](#63-sms). |
| **Attachments** | Email body only. Deferred to v1.1. |
| **In-site read/unread UI, API, or performance work** | The package stores `read_by`; rendering and mutation belong to the host project, and the field is deliberately left unindexed and unoptimised ([§6.4.1](#641-scope-read-state-is-explicitly-not-optimised)). |
| **Digest / batching / throttling** | One notification, one send. |
| **Fallback / escalation between channels** | Channels in a multi-channel group are independent and simultaneous. "SMS only if email fails" is not expressible; `group_id` is the hook a v2 would build it on. See [§2.7](#27-sending-one-message-over-several-channels). |

---

## 2. Data model

Four models, all in `notifier/models.py`.

### 2.1 Enums

```python
class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    INSITE = "insite", "In-site"


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    READY = "ready", "Ready"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    ARCHIVED = "archived", "Archived"
```

> **Addition to the source notes.** The draft listed `draft / ready / sent / archived`.
> `failed` and `sending` are added:
>
> - **`failed`** — without it, a send that raised still has to be recorded as `sent`, which makes
>   the status field a lie and makes "what needs retrying?" an unanswerable query.
> - **`sending`** — the row the async worker sees while in flight. It also happens to make an
>   accidental double-dispatch from two concurrent saves cheap to avoid, but it is *not* a
>   dedup mechanism (see [§4.3](#43-duplicate-sends)).

#### Why `TextChoices` and not `IntegerChoices`

Storing `1` instead of `"ready"` was considered and rejected.

- **The settings dict is keyed by channel name.** `NOTIFIER["BACKENDS"]` maps `"email"` →
  a dotted path, and the public API is `send(channel="email")`. With integer values the settings
  file and the database would disagree about what a channel *is*, and every lookup would need a
  translation table. This alone decides it.
- **This is an audit table.** Its whole job is answering "what happened, and to whom" — usually
  via a direct `SELECT` during an incident, or a CSV export. `status = 'failed'` is self-describing;
  `status = 4` requires the reader to have the enum handy.
- **Adding a member stays free.** Inserting `sending` between `ready` and `sent` — exactly what
  [§2.1](#21-enums) just did — is a no-op for text. With integers it forces either a gap-numbering
  scheme or a value that sorts wrongly against its neighbours.
- **The storage win is noise here.** `'ready'` costs 6 bytes against `smallint`'s 2, on rows that
  also carry `context`, `body_text`, `body_html` and three `rendered_*` fields — i.e. rows measured
  in kilobytes. Postgres alignment padding frequently absorbs the difference entirely. The
  `(status, channel)` index has ~6 distinct keys and is dominated by tuple pointers, not key width.

The integer version only becomes the right answer at a scale where `status` is a hot index on
hundreds of millions of rows — and at that scale the actual fix is partitioning or archival
(`notifier_prune_logs`), not four bytes per row. Native Postgres `ENUM` types are also rejected:
Django does not map to them, and altering them in migrations is painful.

### 2.2 `Notification`

The unit of work. Input is either a template + context, or an inline body.

| Field | Type | Notes |
| --- | --- | --- |
| `channel` | `CharField(choices=Channel)` | Indexed. Selects the backend. |
| `status` | `CharField(choices=Status, default=DRAFT)` | Indexed. Flipping to `ready` triggers delivery. |
| `group_id` | `UUIDField(null=True, blank=True, db_index=True)` | Links the sibling rows of one multi-channel send. See [§2.7](#27-sending-one-message-over-several-channels). |
| `source` | `CharField(max_length=100, blank=True, db_index=True)` | Free-text cause: `"order.shipped"`, `"cron:daily-digest"`, `"webhook:stripe"`. See [§2.11](#211-source-what-caused-this-send). |
| `template` | `FK(NotificationTemplate, null=True, on_delete=SET_NULL)` | Optional. `SET_NULL` so deleting a template never deletes history. |
| `context` | `JSONField(default=dict)` | Render context for the template. |
| `subject` | `CharField(max_length=255, blank=True)` | Inline subject / in-site title. Overrides the template's subject when non-empty. |
| `body_text` | `TextField(blank=True)` | Inline plain-text body, and the `text/plain` part of an HTML email. Derived from the HTML only when left empty ([§2.3](#the-plaintext-alternative)). |
| `body_html` | `TextField(blank=True)` | Inline HTML body. Used when `template` is null. |
| `recipients` | `ArrayField(CharField(max_length=255), default=list)` | Emails for `email`, E.164 numbers for `sms`, user ids for `insite`. |
| `read_by` | `ArrayField(IntegerField(), default=list)` | In-site only. Django user ids, written by the host project. Deliberately not indexed or optimised — [§6.4.1](#641-scope-read-state-is-explicitly-not-optimised). |
| `rendered_subject` | `CharField(max_length=255, blank=True)` | Snapshot, written at send time. |
| `rendered_text` | `TextField(blank=True)` | Snapshot. |
| `rendered_html` | `TextField(blank=True)` | Snapshot. |
| `sent_at` | `DateTimeField(null=True, blank=True)` | Set on first success. |
| `send_attempts` | `PositiveIntegerField(default=0)` | Incremented per attempt, including failures. |
| `last_error` | `TextField(blank=True)` | Latest failure, mirrored from the log for quick admin triage. |
| `created_at` / `updated_at` | `DateTimeField(auto_now_add / auto_now)` | |

**Meta:** `ordering = ("-created_at",)`, the check constraints in [Validation](#validation) below,
and these indexes:

```python
indexes = [
    models.Index(fields=["status", "channel"]),
    models.Index(fields=["channel", "status", "-created_at"]),
    GinIndex(fields=["recipients"], name="notifier_recipients_gin"),
]
```

The `GinIndex` on `recipients` serves `recipients__contains=["a@example.com"]` — "every
notification ever addressed to this address". That is a support query for the primary email and SMS
channels ("did we actually email this customer?"), not an in-site feature, which is why it earns a
GIN index while `read_by` does not ([§6.4.1](#641-scope-read-state-is-explicitly-not-optimised)).

**Why snapshot fields exist.** Templates change. Without `rendered_*`, "what did we actually send
that customer in March?" is unanswerable. The snapshot is the record; `template` + `context` are
merely the inputs.

#### Validation

Two layers, because they catch different things.

**`clean()`** — for anything needing a query or a friendly message:

- `recipients` must be a list of non-empty strings, at most **500** of them — the friendly-error
  twin of the `notifier_max_recipients` constraint below.
- `email`: needs a resolvable subject and at least one body (inline, or via template).
- `sms`: needs resolvable `body_text`.
- `insite`: needs a subject or a body.
- `template.channel`, when set, must equal `self.channel`.

**`CheckConstraint`** — for the per-channel field rules, enforced by the database:

```python
constraints = [
    models.CheckConstraint(
        condition=~Q(channel=Channel.SMS) | Q(body_html="", rendered_html=""),
        name="notifier_sms_has_no_html",
    ),
    models.CheckConstraint(
        condition=Q(channel=Channel.INSITE) | Q(read_by=[]),
        name="notifier_read_by_insite_only",
    ),
    models.CheckConstraint(
        condition=~Q(status=Status.SENT) | Q(sent_at__isnull=False),
        name="notifier_sent_requires_sent_at",
    ),
    models.CheckConstraint(
        condition=Q(recipients__len__lte=500),
        name="notifier_max_recipients",
    ),
]
```

`clean()` alone is not enough for these: it is not called by `save()`, and it is bypassed entirely
by `bulk_create()`, `QuerySet.update()`, and data migrations. A check constraint holds on every
write path.

> `condition=` (not the older `check=`) is correct here — `CheckConstraint.check` was deprecated in
> Django 5.1 and **removed in 6.0**. The 5.2 floor makes `condition=` unconditionally available.
>
> `Q(read_by=[])` is a plain array equality against `'{}'`. An earlier draft needed
> `Value([], JSONField())` here and flagged it as backend-sensitive and unverified; the move to
> `ArrayField` ([§2.9](#29-postgresql-only)) removes both the awkwardness and the doubt.
>
> **The 500 cap is a literal in the constraint, not a setting.** `CheckConstraint` conditions are
> frozen into the migration, so they cannot read `settings` at runtime — a `NOTIFIER["MAX_RECIPIENTS"]`
> knob would drift out of sync with what the database actually enforces, which is worse than having
> no knob. Changing the cap means a migration. `Q(recipients__len__lte=500)` compiles to
> `array_length(recipients, 1) <= 500`, which is `NULL` for an empty array and therefore passes —
> the empty case stays legal and is handled as `skipped` at dispatch.

#### Methods

```python
def mark_ready(self, *, save=True) -> None      # DRAFT/FAILED -> READY, triggers dispatch
def resend(self) -> None                        # explicit re-dispatch; bypasses the transition check
def render(self) -> RenderedMessage             # pure; no DB write, safe to call in tests
def is_read_by(self, user_id) -> bool           # convenience over read_by
def mark_read(self, user_id) -> None            # convenience over read_by; see §6.3 caveat
```

### 2.3 `NotificationTemplate`

| Field | Type | Notes |
| --- | --- | --- |
| `key` | `SlugField()` | Logical lookup key, e.g. `order-shipped`. **Not** unique on its own — see below. |
| `name` | `CharField(max_length=128)` | Human label for admin. |
| `channel` | `CharField(choices=Channel)` | One row per channel. `order-shipped` for email and for SMS are two rows sharing one `key`. |
| `subject` | `CharField(max_length=255, blank=True)` | Django template string. |
| `body_text` | `TextField(blank=True)` | Django template string. |
| `body_html` | `TextField(blank=True)` | Django template string. |
| `is_active` | `BooleanField(default=True)` | Inactive templates fail validation on new notifications but keep working for history. |
| `created_at` / `updated_at` | `DateTimeField(auto_now_add / auto_now)` | |

**Meta:** `constraints = [UniqueConstraint(fields=["key", "channel"], name="uniq_template_key_channel")]`.

> **Corrected from the earlier draft**, which had `key = SlugField(unique=True)` *and* the note
> "a template that must serve email and SMS is two rows" — those two statements contradict each
> other. A globally-unique `key` makes the second row impossible and forces keys like
> `order-shipped-email` / `order-shipped-sms`, which then breaks the multi-channel send in
> [§2.7](#27-sending-one-message-over-several-channels): a caller would have to know each channel's
> mangled key instead of one logical name. Uniqueness belongs on `("key", "channel")`.

#### Rendering semantics

The **full Django template engine**, `django.template.Engine.from_string` — the same engine a
`TemplateResponse` uses. `{{ }}`, `{% %}`, filters, `{% for %}` / `{% if %}`, custom template tags
from installed apps, and **autoescaping on by default** all behave exactly as they do in a normal
Django template. Bodies render against `Notification.context`, plus whatever
`NOTIFIER["TEMPLATE_CONTEXT_PROCESSOR"]` injects (default: nothing).

> **The one real difference from a `TemplateResponse`: there is no request.** Rendering happens in
> a Celery worker or a `post_save` receiver, not in a view. So there is no `RequestContext` and no
> request-dependent context processors — `{{ request }}`, `{{ user }}`, `{{ perms }}` and
> `{% csrf_token %}` are unavailable and silently render empty. Anything a template needs about the
> recipient must be passed explicitly in `context`.
>
> Also: relative URLs do not resolve in an email client. `{% url %}` produces a path, not an
> absolute URL, so templates need a full origin — supply it through
> `TEMPLATE_CONTEXT_PROCESSOR` (e.g. `{"site_url": "https://app.example.com"}`) rather than
> hardcoding it per template.

**Security consequence, accepted:** a full engine means anyone who can edit a `NotificationTemplate`
row can execute template tags in the server process. This is inherent to templates-in-the-database
and is fine *provided template editing is an admin-only privilege* — so the admin registration
guards `NotificationTemplate` behind its own `change_notificationtemplate` permission, and the
README says plainly that granting it is equivalent to granting code execution.

#### The plaintext alternative

An HTML email is always given a `text/plain` part. An authored `body_text` **always wins**; the
derivation below only fills the gap when a template carries HTML and no text.

`django.utils.html.strip_tags` was the first implementation and is not adequate — it removes tags
without replacing them:

```text
<p>Track it <a href="https://app.example.com/track/A-1001">here</a>.</p>
<ul><li>2x Widget</li><li>1x Gadget</li></ul>

strip_tags       Track it here.
                 2x Widget1x Gadget

notifier.html2text
                 Track it here (https://app.example.com/track/A-1001).

                 - 2x Widget
                 - 1x Gadget
```

The first output is not merely ugly: **the URL is gone**, so a plaintext reader cannot track their
order at all. That is a delivered-but-useless message, which is worse than a visibly broken one.

`notifier/html2text.py` is a small stdlib-only converter (`html.parser`, no dependency — Django
stays the only requirement). It keeps link targets, block structure, list bullets, `alt` text and
table rows; `mailto:` links show just the address; a label that already contains the URL is not
doubled; an unclosed `<a>` still yields its target, because template bodies are hand-edited.

It is deliberately **not** a general-purpose renderer: no text wrapping, no column layout, no CSS.
Authors who need exact plaintext write `body_text`.

> Note this concerns **email only**. SMS never reaches the derivation: `clean()` requires a text
> body for that channel and a check constraint forbids HTML on it, so a stripped-tag SMS is
> impossible by construction rather than by care.

`clean()` mirrors the per-channel body requirements above, and additionally compiles each body with
`Engine.from_string` so a syntax error surfaces at edit time in the admin rather than at send time
in a worker.

### 2.4 `NotificationLog`

Append-only. One row per **attempt**, never updated after `finished_at` is written.

| Field | Type | Notes |
| --- | --- | --- |
| `notification` | `FK(Notification, on_delete=CASCADE, related_name="logs")` | |
| `result` | `CharField(choices=LogResult)` | `sent` / `failed` / `suppressed` / `skipped`. |
| `backend` | `CharField(max_length=255)` | Dotted path of the backend that ran. |
| `requested_recipients` | `ArrayField(CharField(max_length=255), default=list)` | What the notification asked for. |
| `effective_recipients` | `ArrayField(CharField(max_length=255), default=list)` | What was actually targeted after the non-prod override. |
| `env` | `CharField(max_length=32)` | Resolved environment name at send time. |
| `is_async` | `BooleanField(default=False)` | `True` when a Celery worker ran it. |
| `error` | `TextField(blank=True)` | Exception repr + traceback tail. |
| `provider_response` | `JSONField(default=dict, blank=True)` | Backend-specific payload (message ids, per-number results). |
| `started_at` / `finished_at` | `DateTimeField` | |
| `duration_ms` | `PositiveIntegerField(null=True)` | |

`LogResult` semantics:

- **`sent`** — the backend accepted the message.
- **`failed`** — the backend raised or reported rejection.
- **`suppressed`** — non-prod, and no enabled `DefaultRecipient` existed to redirect to. Nothing
  left the process. This is a distinct outcome from `failed`; it is not an error.
- **`skipped`** — nothing to do: empty recipient list, or channel backend explicitly disabled.

**Meta:** `ordering = ("-created_at",)`, index on `(notification, created_at)`.

### 2.5 `DefaultRecipient`

The non-prod safety net.

| Field | Type | Notes |
| --- | --- | --- |
| `channel` | `CharField(choices=Channel)` | |
| `address` | `CharField(max_length=255)` | Email / phone number. |
| `enabled` | `BooleanField(default=True)` | Matches the draft's `enable`. |
| `note` | `CharField(max_length=255, blank=True)` | e.g. "QA mailbox". |
| `created_at` / `updated_at` | `DateTimeField(auto_now_add / auto_now)` | |

**Meta:** `constraints = [UniqueConstraint(fields=["channel", "address"], name="uniq_default_recipient")]`.

### 2.6 Why one concrete table, not per-channel models

Making `Notification` abstract and inheriting `EmailNotification` / `SmsNotification` /
`InsiteNotification` was considered and rejected. Recorded here because it is the most tempting
wrong turn in this design.

**The blocking reason: `NotificationLog` needs one FK target.**
An audit trail that answers "everything that failed in the last hour, across all channels" with a
single indexed query is the core value of this package. With an abstract base there are three
unrelated tables and no way to point one FK at them. The alternatives are all worse:

| Option | Why it fails |
| --- | --- |
| Three log tables | Cross-channel queries become a three-way `UNION`; retention/pruning triples. |
| `GenericForeignKey` | No referential integrity, no `JOIN`, no `CASCADE`, and a `contenttypes` dependency — to model a closed set of three known types. |
| Three nullable FKs | Every query needs `COALESCE`; "exactly one is non-null" needs its own constraint. |

**The secondary reasons:**

- **The shared/divergent ratio is wrong for inheritance.** `status`, `template`, `context`,
  `recipients`, `rendered_*`, `sent_at`, `send_attempts`, `last_error` and both timestamps are
  common to all three channels. Exactly **one** field is channel-specific: `read_by`. Inheriting
  three classes to isolate one field is not a trade worth making.
- **The polymorphism already exists, one layer down.** What actually differs per channel is *how
  the message leaves the process* — and that is `BaseBackend` ([§6.1](#61-interface)). `deliver()`
  is identical for all three; only the backend swaps. Duplicating that axis in the model layer
  would model the same variation twice.
- **Multi-table inheritance is not a fix either.** MTI keeps the single FK target, but charges a
  `JOIN` on every read and two `INSERT`s on every write, forever, on the hottest path — and
  polymorphic `create()` still needs `django-polymorphic` or manual downcasting.

**What the instinct is right about.** `body_html` really is meaningless for SMS and `read_by`
really is meaningless for email, and leaving that to `clean()` is too weak. That is now enforced by
the check constraints in [§2.2](#validation) — which is *stronger* than inheritance on the write
paths that matter, since `bulk_create()` and `QuerySet.update()` bypass model-layer validation but
cannot bypass the database.

**Where ergonomics are wanted, use proxy models.** They give channel-scoped managers and
channel-specific helpers with zero schema change, zero joins, and the single FK target intact:

```python
class EmailNotification(Notification):
    class Meta:
        proxy = True

    objects = ChannelManager(Channel.EMAIL)  # filters, and defaults channel on create()
```

Deferred to v1.1 — worth adding only if calling code actually reads better for it.

**The revisit trigger.** If a channel arrives with substantial private data — push notifications
(device tokens, TTL, collapse key, priority) are the realistic case — the answer is a single
`channel_data = JSONField(default=dict)`, or a per-channel side table keyed to `Notification`.
Not MTI, and not an abstract base.

### 2.7 Sending one message over several channels

"Send this text by email **and** SMS **and** in-site" creates **three `Notification` rows**, one
per channel, sharing a `group_id`. This is the design, not a workaround.

#### Why not one row with a list of channels

Because a row has one `status`, and three channels have three outcomes. Concretely: email is
accepted, SMS is rejected by the carrier. A single row must then be `sent` or `failed` — either
answer is a lie about one of the channels. Everything downstream inherits the lie:

- **Retry re-sends what already succeeded.** `notifier_retry_failed` on that row would re-mail a
  customer who already got the email, to get the SMS out.
- **`sent_at` / `send_attempts` / `last_error` are single-valued** and cannot describe three
  independent lifecycles.
- **One backend per row** is what `NOTIFIER["BACKENDS"]` and `deliver()` assume ([§6.1](#61-interface)).

Per-channel rows keep each lifecycle independent, which is exactly what the audit-trail goal
([§1](#1-goals)) requires. So: three rows, and no `channels` list field.

#### What was missing: the rows were not linked

The earlier draft had no way to answer "did this alert reach the user by *any* channel?" or
"re-send everything for this event". `group_id` fixes it — a nullable indexed `UUIDField`, set to
the same value on all siblings of one send, `null` for a single-channel send.

A `NotificationGroup` **table** was considered and rejected: it buys per-group metadata nobody has
asked for, plus a FK and a join, when `filter(group_id=...)` already answers every question on the
list. Promote it to a real table only if group-level state (an escalation policy, a "reached"
flag) actually appears.

#### Per-channel recipients and templates

The two things that genuinely differ per channel are the recipient addresses (`a@example.com` vs
`+6421234567` vs a user id) and the template body. So the multi-channel call takes recipients
**per channel**, and resolves the template per channel from one logical `key` — which is what the
`("key", "channel")` uniqueness in [§2.3](#23-notificationtemplate) is for:

```python
from notifier import send_multi

group_id = send_multi(
    key="stock-alert",  # resolves stock-alert/email, stock-alert/sms, …
    recipients={
        Channel.EMAIL: ["ops@example.com"],
        Channel.SMS: ["+6421234567"],
        Channel.INSITE: ["42"],
    },
    context={"sku": sku, "level": level},
)
```

Semantics:

- All rows are created in **one transaction**, then dispatched on commit. A template missing for
  a requested channel is a `ValidationError` that rolls the whole group back — no half-created
  group. Set `require_all_templates=False` to skip absent channels instead.
- Delivery is **independent after that point**. One channel failing neither blocks nor rolls back
  the others.
- **Non-prod gating applies per channel** ([§3.2](#32-environment-gating)). If a `DefaultRecipient`
  exists for email but not SMS, the email is redirected and the SMS is `suppressed` — the group can
  legitimately end up with mixed results in dev.
- `send_multi` returns the `group_id`. `Notification.objects.filter(group_id=...)` is the whole
  group API.

#### Explicit non-goal: fallback / escalation

"SMS only if the email fails", or "escalate to phone after 10 minutes unread", is **not** in v1.
Channels in a group are independent and simultaneous. `group_id` is the field such a feature would
build on, but the state machine it needs (per-group policy, timers, cancel-siblings-on-first-success)
is a v2 design, not something to imply now.

### 2.8 Recipients are addresses; readers are users

Two list fields on `Notification` look similar and are not:

| | `recipients` | `read_by` |
| --- | --- | --- |
| Type | `ArrayField(CharField(255))` | `ArrayField(IntegerField())` |
| Holds | **addresses** — no account required | **Django user ids** |
| Channels | all three | `insite` only |
| Written by | the caller, at send time | the host project, whenever a user opens the message |

**Recipients never have to be Django users.** An invoice to `billing@customer.example`, an SMS to a
courier's phone — neither party has an account, and both must work. This is why `recipients` is a
plain string array and why the package declares no FK to `AUTH_USER_MODEL`.

**Readers are users**, because "who has read this" is only meaningful for someone logged into the
app. Hence `read_by` is integer user ids, and hence it applies to `insite` alone.

Per-channel meaning of `recipients`:

| Channel | Contents | Delivery shape |
| --- | --- | --- |
| `email` | email addresses | **One message to all of them**, one SMTP transaction. See [§6.2](#62-email). |
| `sms` | phone numbers (E.164) | One or many numbers; providers are per-number, so the backend fans out. See [§6.3](#63-sms). |
| `insite` | user ids **as strings** | No transport. This is the one channel where an address happens to be a user id. |

> The `insite` string/int mismatch is deliberate and worth knowing about: `recipients=["42"]` but
> `read_by=[42]`. Keeping `recipients` homogeneous across all three channels is worth more than
> making one channel's ids numeric, and the alternative — a second nullable integer array used by
> one channel — is worse.

### 2.9 PostgreSQL only

The package targets PostgreSQL exclusively. This is a deliberate narrowing, decided by the owner,
and it buys three concrete things:

- **`ArrayField` instead of `JSONField` for the list columns** (`recipients`, `read_by`,
  `requested_recipients`, `effective_recipients`). Real typed elements instead of JSON scalars,
  smaller on disk than `jsonb`, GIN-indexable, and `__contains` / `__overlap` / `__len` all work.
- **`JSONField.contains` is no longer a problem.** That lookup is unsupported on SQLite and Oracle,
  which would have made `read_by` unqueryable through the ORM on those backends. Moot now.
- **GIN indexes, and array equality in check constraints**, are available unconditionally rather
  than behind a backend check.

`context` and `provider_response` stay `JSONField` — they hold genuinely nested, heterogeneous
data, which is what `jsonb` is for. Arrays are the right type only for homogeneous lists.

**Consequences to accept:**

- **`django.contrib.postgres` must be importable**, so `psycopg` is required at runtime. The
  package still declares only Django as a dependency — the host project supplies the driver, as it
  already must to run Django on Postgres at all.
- **Tests run against PostgreSQL, not SQLite.** No fast in-memory test backend; CI needs a Postgres
  service container ([§9](#9-packaging--repo-layout)).
- **This must be declared loudly**, since the package is public. Trove classifiers do not express a
  database requirement, so it belongs in the README's first paragraph and in the PyPI summary — a
  user discovering `ImproperlyConfigured` after `pip install` is a bad first impression.

### 2.10 Runs on a service with no users, no auth, and no admin

A likely deployment is a **headless microservice** — something that sends mail and has no human
accounts at all. That is a supported first-class configuration, not a degraded one.

This is a valid, fully functional install:

```python
INSTALLED_APPS = ["django.contrib.postgres", "notifier"]
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", ...}}
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates"}]
```

> **Corrected during M1.** An earlier draft claimed the requirement was "a PostgreSQL connection"
> and that `INSTALLED_APPS = ["notifier"]` alone sufficed, citing the `ArrayField` documentation as
> having "verified" that `django.contrib.postgres` was unnecessary. That was wrong: the field docs
> do not mention it, but Django enforces it with the **`postgres.E005`** system check, which fails
> once per `ArrayField`. A second requirement was also missed — a `DjangoTemplates` backend, since
> bodies render through the project's engine. Both are now enforced by this package's own system
> checks (`notifier.E001`, `notifier.E003`) so the failure is one actionable message rather than
> four opaque ones.

**Required beyond a database connection:**

| Requirement | Why |
| --- | --- |
| `django.contrib.postgres` in `INSTALLED_APPS` | Django's `postgres.E005` check refuses `ArrayField` without it. The app carries no models and depends on neither auth nor contenttypes, so the headless property is untouched — the list is two entries, not one. |
| A `DjangoTemplates` backend in `TEMPLATES` | Bodies render through the project's configured engine, which is what makes custom template tags behave as they do in a view ([§2.3](#rendering-semantics)). No context processors are needed — there is no request at send time. |

**Not required — none of these need to be installed:**

| App | Why it is not needed |
| --- | --- |
| `django.contrib.auth` | No FK to `AUTH_USER_MODEL` anywhere ([Goal 6](#1-goals)). `read_by` is a raw integer array, not a relation, so it needs no user table to exist. |
| `django.contrib.admin` | `notifier/admin.py` is only imported by admin autodiscovery, which only runs when the admin is installed. `apps.ready()` imports signals and checks, never admin. |
| `django.contrib.contenttypes` | Not used — `GenericForeignKey` was explicitly rejected ([§2.6](#26-why-one-concrete-table-not-per-channel-models)). |
| `django.contrib.sites` | Not used. Absolute URLs in templates come from `TEMPLATE_CONTEXT_PROCESSOR` ([§2.3](#rendering-semantics)). |

**What a userless deployment means in practice:**

- **The in-site channel is inert.** It is only meaningful where people log in. On a headless service
  nothing ever writes `read_by`, and the cost of that is one always-empty array column. Nothing
  needs disabling.
- **`created_by` would have been 100% `NULL`.** This is the strongest argument that dropping the
  audit FKs was correct: on a service with no users there is no human to record, ever. It also
  means [`source`](#open-questions) is not merely the better option for cause-tracking — it is the
  *only* one that can work here.
- **Email and SMS are the whole product** on such a deployment, which matches the stated purpose
  ([§1](#1-goals)).

This is enforced, not just asserted: `tests/settings_minimal.py` holds the minimal configuration
and **the entire suite runs against it** as well as against the full settings, so an accidental
import of `auth`, `admin` or `contenttypes` fails the build rather than surfacing at a user's deploy
([AC-35](#8-acceptance-criteria)).

### 2.11 `source`: what caused this send

`source` is a free-text label naming the *cause* of a notification, set by whatever created it:

```python
send(channel="email", recipients=[...], key="order-shipped", context={...}, source="order.shipped")
```

Convention, not enforcement: `"<domain>.<event>"` for business events, `"cron:<job>"` for scheduled
work, `"webhook:<provider>"` for inbound triggers, `"admin:<username>"` when a person really did it.
No `choices`, no validation — an enum would need updating every time a new call site appears, which
is exactly the friction that leads to it being left blank.

**Why a plain `CharField` and not a user FK.** `created_by` / `updated_by` were specified and then
dropped, and `source` is what replaced them:

| `created_by` FK | `source` CharField |
| --- | --- |
| FK to `AUTH_USER_MODEL`, migration dependency | no relation at all |
| Needs admin `save_model()` wiring | caller passes a literal, or nothing |
| Cannot be set outside a request | set anywhere, including cron and workers |
| **Unusable on a headless service** — no users exist | works identically with or without users |

The last row decides it. On the microservice deployment
([§2.10](#210-runs-on-a-service-with-no-users-no-auth-and-no-admin)) there is no human to record,
ever, so a user FK could never answer anything. `source` works there unchanged.

Indexed because the queries it exists for are filters: "every `cron:daily-digest` that failed
today", "did the Stripe webhook fire twice?". Blank is always allowed — `source` is diagnostic, and
a notification with no stated cause must still send.

---

## 3. Settings

One dict, merged over defaults by `notifier.conf.notifier_settings`. Unspecified keys keep their
default; the dict is re-read on `setting_changed` so `override_settings` works in tests.

```python
NOTIFIER = {
    # ---- environment gating -------------------------------------------------
    "ENV": None,  # None -> settings.DJANGO_ENV -> $DJANGO_ENV -> "dev"
    "PRODUCTION_ENVS": ("prod", "production"),
    "NON_PROD_SUBJECT_PREFIX": "[{env}] ",  # "" disables
    "SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT": True,
    # ---- backends -----------------------------------------------------------
    "BACKENDS": {
        "email": "notifier.backends.email.DjangoEmailBackend",
        "sms": "notifier.backends.sms.ConsoleSmsBackend",
        "insite": "notifier.backends.insite.InsiteBackend",
    },
    # ---- email --------------------------------------------------------------
    "EMAIL": {
        "FROM": None,  # None -> settings.DEFAULT_FROM_EMAIL
        "CONNECTION": None,  # None -> Django's default mail connection
        "FAIL_SILENTLY": False,
        "RECIPIENT_MODE": "auto",  # "auto" | "to" | "bcc" | "separate"; see §6.2
    },
    # ---- sms ----------------------------------------------------------------
    "SMS": {
        "FROM": None,  # sender id / long number, provider-specific
        "OPTIONS": {},  # opaque, handed to the backend
    },
    # ---- delivery -----------------------------------------------------------
    "USE_CELERY": None,  # None -> auto-detect; True/False -> force
    "CELERY_QUEUE": None,
    "CELERY_MAX_RETRIES": 3,
    "CELERY_RETRY_BACKOFF": 30,  # seconds, exponential
    "DISPATCH_ON_COMMIT": True,
    # ---- rendering ----------------------------------------------------------
    "TEMPLATE_CONTEXT_PROCESSOR": None,  # dotted path -> dict, merged under context
    # ---- housekeeping -------------------------------------------------------
    "LOG_RETENTION_DAYS": 90,  # None -> keep forever
}
```

### 3.1 SMTP configuration

The package **does not define its own SMTP settings.** Email goes through Django's own mail
framework, so `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_USE_TLS`, and `DEFAULT_FROM_EMAIL` are the configuration surface, and any existing
Django email backend (SMTP, console, locmem, third-party) works unchanged.

`NOTIFIER["EMAIL"]["CONNECTION"]` exists only for the case where notifications must go out over a
*different* SMTP server than the rest of the project. It takes a dict of
`django.core.mail.get_connection()` kwargs.

**Rationale:** re-implementing SMTP config would duplicate settings the host project already has,
and would silently diverge from `django.core.mail` behaviour (TLS handling, connection reuse,
test `outbox`).

### 3.2 Environment gating

```python
env = (
    notifier_settings.ENV  # 1. NOTIFIER["ENV"]
    or getattr(settings, "DJANGO_ENV", None)  # 2. settings.DJANGO_ENV
    or os.environ.get("DJANGO_ENV")  # 3. $DJANGO_ENV
    or "dev"  # 4. fail-safe
)
is_production = env in notifier_settings.PRODUCTION_ENVS
```

**Django settings outrank the environment variable.** A project that keeps all configuration in
its settings module — the common case — declares `DJANGO_ENV = "production"` there and never
touches the process environment. The env-var step remains as step 3 for twelve-factor deployments
that inject it at runtime.

**Step 4 is the load-bearing one.** A project that configures nothing is treated as non-production
and therefore cannot mail real recipients until it explicitly declares itself production. The
accident this produces is "staging sent nothing", never "staging emailed the customer list".

**In-site is exempt from all of this**, in every environment. The gate exists to stop a
non-production system contacting real people, and in-site contacts nobody. Gating it would achieve
nothing — the `Notification` is persisted *before* dispatch, so suppressing the "send" does not
un-write it — while making in-site impossible to develop against locally without inventing a
`DefaultRecipient` holding a user id. *(Added during M3; the first draft gated all three channels.)*

For `email` and `sms`, when `is_production` is false:

1. Load `DefaultRecipient.objects.filter(channel=..., enabled=True)`.
2. If any exist → `effective_recipients` becomes those addresses. `requested_recipients` keeps the
   original list, so the log shows exactly what *would* have gone out in prod.
3. If none exist → result is `suppressed` when `SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT` is true
   (the default), otherwise the send proceeds to the real recipients.
4. Subject is prefixed with `NON_PROD_SUBJECT_PREFIX.format(env=env)`.

The default for step 3 is deliberately *fail-closed*: a misconfigured staging environment must not
email real customers.

---

## 4. Trigger semantics

### 4.1 What triggers a send

Exactly two situations:

1. **Create** with `status == ready`.
2. **Update** where the persisted status was something other than `ready` and the new status is `ready`.

Everything else — `ready → ready` re-saves, edits to `context`, `archived` — triggers nothing.

### 4.2 Implementation

Old-value capture uses `Model.from_db`, which is the cheapest correct place: it stashes
`instance._loaded_status` with no extra query.

```python
@classmethod
def from_db(cls, db, field_names, values):
    instance = super().from_db(db, field_names, values)
    instance._loaded_status = instance.status
    return instance
```

A `post_save` receiver compares `_loaded_status` (absent ⇒ created) against `instance.status` and,
on a qualifying transition, calls `notifier.dispatch.dispatch(notification)`.

**Dispatch must run after commit.** With `DISPATCH_ON_COMMIT` true, dispatch is wrapped in
`transaction.on_commit`. Without it, a Celery worker can pick up the task and query for a row that
the enclosing transaction has not committed yet — the classic `DoesNotExist`-on-a-row-you-just-created
race. `DISPATCH_ON_COMMIT = False` exists only for autocommit-style scripts and is documented as
"you probably don't want this".

### 4.3 Duplicate sends

Per the owner's decision, dedup is out of scope. Stated plainly so it is not a surprise later:

- `mark_ready()` on an already-`sent` notification re-sends it. That is the intended `resend()` path.
- Two concurrent saves that both flip `draft → ready` can both dispatch. The `sending` status
  narrows the window but does not close it — there is no `SELECT … FOR UPDATE` and no unique
  constraint on "one successful send per notification".
- Celery retries after a partial provider failure can deliver twice.

#### Why nothing is done about it

*"At-most-once"* is the delivery guarantee that says **never deliver twice, even at the risk of not
delivering at all.** Its opposite, *at-least-once*, prefers a duplicate to a miss.
This design promises neither: it can send twice, and if the process dies mid-send it can also send
zero times.

The compare-and-swap fix — `filter(pk=..., status=READY).update(status=SENDING)`, dispatch only
when it returns `1`, relying on a single `UPDATE` being atomic — was evaluated and **rejected for
v1**:

- **The scenario it actually closes is concurrent status flips**, two saves racing from `draft` to
  `ready`. In this deployment that is close to impossible: notifications are created by application
  code, and essentially nobody logs into the Django admin.
- **The scenario that does occur, it does not fix.** Celery brokers are at-least-once: a task can be
  redelivered when a worker is OOM-killed, restarted during a deploy, or exceeds its visibility
  timeout. CAS helps there *only* if the redelivered task finds the row still `sending` — and then
  it introduces the opposite failure: a worker that died mid-send leaves the row stuck at `sending`
  **forever**, with CAS now blocking the legitimate retry.
- **So CAS is not the five-line change it looks like.** Done properly it needs a companion reclaim
  path (sweep rows stuck at `sending` for more than N minutes back to `ready`), which is a timer, a
  tunable, and a new failure mode of its own. That is a v2 feature, not a freebie.

A host project that genuinely needs at-most-once should carry an idempotency key of its own. If
duplicates ever become a real, observed problem here, the fix is CAS **plus** the reclaim sweep —
tracked in [Open questions](#open-questions), not pre-built.

---

## 5. Delivery path

### 5.0 Why not `django.tasks`

Django 6.0 ships a built-in background-tasks framework, `django.tasks` — a `@task` decorator, an
`.enqueue()` call, and a pluggable `BaseTaskBackend`. On the surface it is exactly the abstraction
this section hand-rolls, and adopting it would delete the fragile Celery auto-detection below. It
was evaluated as the reason to raise the floor to Django 6.0, and **rejected for v1** on three
counts, any one of which is disqualifying:

- **No retries.** `django.tasks` deliberately has no notion of retry, scheduling, recurrence or
  delivery guarantees — it is one-off execution only. Retries with backoff are already a specified
  feature here ([§5.2](#52-the-two-paths), `notification_exhausted`), so adopting it would mean
  reimplementing retry logic on top, not inheriting it.
- **No Celery backend exists.** As of 2026-07 there is no backend implementing `BaseTaskBackend`
  on top of Celery — it is an open request against Celery itself. Adopting `django.tasks` would
  therefore mean *dropping* Celery, or writing and maintaining that backend ourselves. Neither is
  this package's job.
- **The shipped backends are dev/test only.** Django provides the interface and no worker.
  Production requires a third-party backend, and today the realistic one is `django-tasks`'
  database-backed worker — a different runtime from the Celery the host project already operates.

So the Django 6.0 floor would have cost the 5.2 LTS support window and bought nothing usable.

> **Revisit when** a Celery-backed `BaseTaskBackend` is production-ready **and** `django.tasks`
> grows retries. At that point it becomes the right abstraction: the host configures `TASKS` once
> for all its background work, `ImmediateBackend` becomes the inline path natively, and
> [§5.1](#51-celery-detection) — the weakest part of this design — disappears entirely.

### 5.1 Celery detection

`USE_CELERY = None` (default) resolves once, lazily, at first dispatch:

```text
celery importable  AND  celery.current_app has a configured broker  ->  async
otherwise                                                          ->  inline
```

**Caveat, documented in the README:** "celery is importable" is not "a worker is running". A
project with Celery installed but no worker will enqueue tasks that nobody consumes. Production
deployments should set `USE_CELERY` explicitly rather than rely on detection.

**Corrected after testing against a real worker.** An earlier draft said such notifications "sit at
`sending` forever". They do not, and the difference matters operationally, because these are two
distinct states with different recovery paths:

| | Status | Log row | Recovers when |
| --- | --- | --- | --- |
| Enqueued, no worker consuming | **`ready`** | none | a worker starts — the broker holds the task, so it is delivered late rather than lost |
| Worker died inside the backend's `send()` | **`sending`** | none | nothing recovers it automatically |

`sending` is set by `deliver()`, which runs *in the worker*; a task nobody has picked up has not run
`deliver()` at all, so the row never leaves `ready`. Only the second case strands a row
permanently — and because neither writes a log row, neither is `failed`, so plain
`notifier_retry_failed` finds neither. Reclaiming the stranded one needs
`notifier_retry_failed --include-sending --older-than <threshold>`, where the threshold must exceed
the slowest legitimate send so a live in-flight delivery is not duplicated.

Under `CELERY_TASK_ALWAYS_EAGER`, the async path executes inline — which is what test suites want,
and it exercises the same code path as production.

### 5.2 The two paths

**Async:** `notifier.tasks.send_notification_task(notification_id)`, a `shared_task` defined behind
a guarded import so the module is importable without Celery. Bound task, `autoretry_for` the
backend's declared retryable exceptions, `max_retries` / `retry_backoff` from settings. Each
retry writes its own `NotificationLog` row.

**Inline:** the same `notifier.dispatch.deliver(notification)` call, in-process.

`deliver()` re-raises retryable exceptions so the Celery task can retry them. Inline there is
nothing to retry with, so the inline path **swallows delivery failures** — they are already recorded
in the log row and the `failed` status. Letting an SMTP error escape would throw it out of the
caller's `save()`, turning "the notification failed" into "creating a notification crashed".
`ImproperlyConfigured` is the exception: a configuration bug must reach the developer who can fix it,
so it propagates from both paths. *(Both behaviours added during M3.)*

Both paths are the *same function*; the only difference is which side of the queue it runs on.
Status and log outcomes are identical.

### 5.3 Status flow

```text
draft ──mark_ready()──> ready ──dispatch──> sending ──┬── ok ────> sent
                          ▲                           │
                          │                           └── error ─> failed
                          └────── mark_ready() / resend() ──────────┘
```

A **suppressed** or **skipped** notification stays at `ready`. It never claimed to have been sent,
and re-running it in a properly configured environment must work without a manual status reset.
The outcome is visible only in its `NotificationLog` row.

---

## 6. Backends

### 6.1 Interface

```python
class BaseBackend(abc.ABC):
    retryable_exceptions: tuple[type[Exception], ...] = ()

    def __init__(self, **options): ...

    @abc.abstractmethod
    def send(
        self,
        notification: Notification,
        recipients: list[str],
        message: RenderedMessage,
    ) -> BackendResult: ...
```

`RenderedMessage` is a frozen dataclass: `subject`, `text`, `html`.
`BackendResult` is a frozen dataclass: `ok: bool`, `provider_response: dict`, `error: str | None`.

A backend raises for transport failures (so retries can classify them via
`retryable_exceptions`) and returns `ok=False` for provider-level rejections that retrying will
not fix.

### 6.2 Email

`DjangoEmailBackend` builds one `EmailMultiAlternatives` and hands it to
`django.core.mail` — the connection, TLS and `outbox` behaviour are Django's ([§3.1](#31-smtp-configuration)).
`body_text` becomes the body and `body_html`, when present, is attached as a `text/html` alternative.

**A notification with several email addresses is one message, not several.** One SMTP transaction,
one `NotificationLog` row. What differs between modes is only which header the addresses land in:

```python
"EMAIL": {
    "RECIPIENT_MODE": "auto",   # "auto" (default) | "to" | "bcc" | "separate"
}
```

- **`auto` — the default.** `to` for a single recipient, `bcc` for two or more.
- **`to`** — always `To:`, all addresses visible to each other. Matches
  `django.core.mail.send_mail(recipient_list=[...])`.
- **`bcc`** — always hidden. Same single transaction and single log row as `to`.
- **`separate`** — one message *per address*, still one `Notification` and one log row, with
  per-address results in `provider_response`. Costs N SMTP round trips; only needed when each
  recipient's copy must differ, or when a provider rejects BCC.

The count that decides `auto` is the **effective** recipient list, after non-production redirection
([§3.2](#32-environment-gating)). Three requested recipients collapsing to one `DefaultRecipient` in
staging therefore send as `to` — which is correct: one address, nothing to hide.

**Why the threshold is at two.** The two failure modes are not symmetric, and neither is the
protection worth paying for. With **two or more** recipients, getting it wrong leaks one customer's
address to another — a privacy incident that cannot be undone once the mail is out — while a hidden
list is merely inconvenient and fixable in settings. With **exactly one** recipient there is no
second party in the message and therefore nothing to leak, so the protection buys nothing and costs
something: a Bcc-only envelope arrives with an empty `To:`, which is unusual for ordinary
transactional mail and scores worse with some spam filters.

> **Verified against real infrastructure**, which is why this rule exists rather than a flat `bcc`
> default. A single-recipient message sent Bcc-only through a commercial MTA arrives with the
> recipient field literally blank in the reader's client. Nothing was wrong with it, but it does not
> look like the receipt or password-reset it is. `auto` keeps the guarantee exactly where it is
> needed and normal-looking mail everywhere else.

The mode is a setting rather than a per-notification field because it is a policy decision. A
project needing both can override it per send through the backend `OPTIONS`.

> One caveat that survives: **replies do not reach the other recipients** in `bcc`. A thread meant to
> be a group conversation wants an explicit `to`.

### 6.3 SMS

Ships as **abstraction + console backend only**, per the owner's decision. No third-party SDK is a
dependency, and the package installs with Django alone.

`recipients` may hold one number or many. Unlike email there is no single-transaction equivalent —
SMS providers are per-number — so the backend fans out internally and reports per-number outcomes
in `NotificationLog.provider_response`. It remains **one `Notification` and one log row**: the unit
of work is the send, not the number.

**Two failure rules the base class applies**, both found necessary during M3:

- **Nothing got through and every failure is retryable** → the exception is re-raised, so the retry
  machinery engages. The first implementation caught every per-number exception and reported
  `ok=False`, which made `retryable_exceptions` dead code for this channel: a network blip was
  recorded as a permanent failure and never tried again.
- **Some got through** → reported as `ok=False` but *never* re-raised. Retrying a partial success
  would send a second message to the numbers that already worked.

- `notifier.backends.sms.BaseSmsBackend` — the contract, with `FROM` / `OPTIONS` wired from settings.
- `notifier.backends.sms.ConsoleSmsBackend` (default) — writes the message to `stderr`,
  returns `ok=True`. Mirrors `django.core.mail.backends.console`.
- `notifier.backends.sms.LocmemSmsBackend` — appends to `notifier.backends.sms.outbox`, the SMS
  analogue of `django.core.mail.outbox`, for tests.

Integrating a real provider is a documented ~30-line subclass. The README carries one worked
example (a generic HTTP provider) so the shape is unambiguous, without the package taking on an
SDK dependency or a provider's API-stability risk.

### 6.4 In-site

`InsiteBackend` performs no transport. It marks the notification delivered and leaves the payload
in the `rendered_*` snapshot fields for the host project to render.

Read tracking is `Notification.read_by`, an `ArrayField(IntegerField())` of Django user ids, read
and written by the host project — no `InsiteMessage` table, no per-recipient rows, no dependency on
`AUTH_USER_MODEL`. `is_read_by(user_id)` and `mark_read(user_id)` are thin conveniences over it.

#### 6.4.1 Scope: read state is explicitly not optimised

**`read_by` is a convenience, not a feature the design is built around.** The package's job is
getting email and SMS out ([§1](#1-goals)); most notifications never touch `read_by` at all, and
in-site volumes are expected to be low enough that its performance does not matter.

Concretely, the following are **accepted, not problems to solve**:

- `read_by` on a widely-read in-site message grows large. Fine — there will not be many such rows.
- Marking read is a read-modify-write on a shared row, so concurrent marks serialise under
  `select_for_update()`. Fine at the expected volume.
- "Unread for user X" is `NOT (read_by @> ARRAY[42])`, a negation that no index can serve. Fine on
  a small table.

No index is carried for `read_by`, and no `read_at` column is added. If in-site ever becomes a
primary, high-volume channel, the answer is a join table (`notification`, `user_id`, `read_at`) —
a deliberate v2 decision, not something to pre-build now.

> An earlier draft of this document built out a two-regime analysis, a partial unread index, and a
> "one row per recipient" recommendation for this field. That was disproportionate to a secondary
> channel, and the recommendation was **wrong for email and SMS anyway** — one email to five
> addresses is one message and one row ([§6.2](#62-email)), not five. Removed.

#### 6.4.2 Why `ArrayField`, and not JSON or comma-separated text

The storage question is worth answering even though the performance question is not, because the
wrong type here costs correctness rather than speed.

| Option | Verdict |
| --- | --- |
| `ArrayField(IntegerField(), default=list)` | **Chosen.** Real integers, validated by the database. Available because the target is Postgres-only ([§2.9](#29-postgresql-only)). |
| `JSONField(default=list)` | Workable, and what an earlier draft specified. Worse: JSON scalars instead of ints, larger on disk, and `contains` is unsupported on SQLite/Oracle — moot now, but portability was the only argument for it. |
| `TextField` with `",1,42,7,"` | **Rejected**, on correctness grounds. |

Comma-separated text is the one option that is actively unsafe:

- **It needs sentinel commas to be correct at all.** Without wrapping both ends, `LIKE '%42%'`
  matches user `142`. Every read and write has to maintain the `,…,` invariant by hand, forever.
- **Zero type safety.** `"42"` vs `42`, stray whitespace, and duplicates all become application
  bugs. `ArrayField(IntegerField())` rejects a non-integer at the database.
- **No usable ORM lookups and no check constraint worth writing** — the host project, which is
  precisely who owns this field, would be hand-writing `LIKE`.
- On the performance axis it also happens to be worst — a leading-wildcard `LIKE` can never use an
  index — but per 6.4.1 that is not what decides it here.

The only gain is a few bytes per row, on rows that already carry `context`, `body_text`, `body_html`
and three `rendered_*` fields. Same argument as [§2.1](#why-textchoices-and-not-integerchoices).

---

## 7. Public API

Three entry points, in increasing order of control.

```python
from notifier import send, send_multi

# 1. One channel, one call. Creates a ready Notification and dispatches it.
notification = send(
    channel="email",
    recipients=["a@example.com"],
    key="order-shipped",  # template key; or pass subject/body_* inline
    context={"order": order.number},
)

# 2. Several channels at once -> one row per channel, sharing a group_id. See §2.7.
group_id = send_multi(
    key="order-shipped",
    recipients={
        Channel.EMAIL: ["a@example.com"],
        Channel.SMS: ["+6421234567"],
        Channel.INSITE: ["42"],
    },
    context={"order": order.number},
)
siblings = Notification.objects.filter(group_id=group_id)

# 3. The explicit route, for anything needing review before it goes out.
n = Notification.objects.create(
    channel=Channel.EMAIL,
    recipients=["a@example.com"],
    subject="Hi",
    body_text="…",
    status=Status.DRAFT,
)
n.mark_ready()
```

`send()` and `send_multi()` are thin wrappers over `Notification.objects.create()` — they add no
delivery path of their own, so anything achievable through them is achievable through the ORM.

### 7.1 Signals

In `notifier.signals`, all with `sender=Notification`:

| Signal | Fired when | Extra kwargs |
| --- | --- | --- |
| `pre_send` | after render, before the backend runs | `notification`, `recipients`, `message` |
| `notification_sent` | backend accepted | `notification`, `log` |
| `notification_failed` | backend raised or rejected | `notification`, `log`, `exception` |
| `notification_suppressed` | non-prod, no default recipient | `notification`, `log` |
| `notification_exhausted` | the final retry failed — the send is given up on | `notification`, `log`, `attempts` |

`notification_exhausted` fires exactly once per notification, after the last retry, in addition to
the `notification_failed` that accompanies that attempt. It exists because "attempt 3 of 3 failed"
and "we have stopped trying" are different events, and only the second one is worth paging someone
about. Without it, a host project wanting an alert on give-up would have to reimplement the retry
arithmetic in a `notification_failed` receiver.

### 7.2 Admin

All four models registered. `Notification` admin: filters on channel/status/created, readonly
`rendered_*` and `send_attempts`, inline `NotificationLog` (readonly), and a
**"Re-send selected"** action.

Re-sending is also reachable one row at a time: a **"Re-send"** button in every changelist row, and
**"Re-send now"** in the change form's submit row. Selecting a checkbox to re-drive a single row is
the common case, and from an open change form it means navigating away first.

All three entry points call `Notification.resend()` — no second send path
([§5.2](#52-the-two-paths)) — and all three **POST**, to `<pk>/resend/`, which returns `405` on
anything else. Delivery must not hang off a URL something can follow unattended: a link-prefetching
browser, a crawler that got the address from a pasted screenshot, or a second click on a copied link
would each put real mail in front of a customer. The row button therefore carries no form of its
own — the changelist already *is* one form and forms cannot nest, so the button borrows that form's
CSRF token through `formaction`. The change form button does the same, which is why pressing it
saves nothing.

Neither button renders without `change_notification`, and `resend_view` re-checks the permission
against the object, so a button in a page loaded before a permission change cannot send. With no
usable `Referer` — `Referrer-Policy: no-referrer`, or an off-site value that must never be
redirected to — the redirect falls back to the change page.

The buttons live in `notifier/templates/admin/notifier/notification/change_form.html`, the first
path `ModelAdmin` looks up, so a host project's own template of that name still wins (`DIRS` is
searched before app directories). Neither the template nor `admin.py` loads on a headless install
([§2.10](#210-runs-on-a-service-with-no-users-no-auth-and-no-admin)).

### 7.3 Management commands

| Command | Purpose |
| --- | --- |
| `notifier_send_test --channel email --to a@example.com` | End-to-end smoke test of config, without touching business data. |
| `notifier_retry_failed [--older-than 1h] [--limit N]` | Re-dispatch `failed` notifications. |
| `notifier_prune_logs [--days N]` | Delete logs past `LOG_RETENTION_DAYS` (default 90). |

> **`LOG_RETENTION_DAYS = 90` is a policy, not a mechanism.** Nothing in the package deletes
> anything on a timer — `notifier_prune_logs` only removes rows when something runs it. The host
> project must schedule it (cron, Celery beat, a systemd timer). Setting the value and never
> scheduling the command means logs are kept forever while the settings file claims 90 days, so the
> README states this next to the setting, and `notifier_send_test` reports the log-table row count
> as a nudge.

---

## 8. Acceptance criteria

Each row maps to one test. IDs are stable and referenced by [§10 Milestones](#10-milestones).

| ID | Area | Criterion |
| --- | --- | --- |
| AC-01 | Trigger | `Notification.objects.create(status=READY)` → one `sent` log, `status == sent`, `sent_at` set. |
| AC-02 | Trigger | `create(status=DRAFT)` → zero logs. |
| AC-03 | Trigger | `draft → ready` → one log. `ready → ready` re-save → no new log. |
| AC-04 | Trigger | `archived → ready` → one log. |
| AC-05 | Trigger | Inside `transaction.atomic()`, no dispatch before commit; on rollback, nothing is sent. |
| AC-06 | Env gating | `DJANGO_ENV=dev` + one enabled `DefaultRecipient` → the only address the message targets is that one, and the log's `requested_recipients` keeps the original. |
| AC-07 | Env gating | `DJANGO_ENV=dev` + no enabled `DefaultRecipient` → `mail.outbox` empty, log result `suppressed`, `status` still `ready`. |
| AC-08 | Env gating | `DJANGO_ENV=production` → real recipients used, no subject prefix. |
| AC-09 | Env gating | A `DefaultRecipient` with `enabled=False` is ignored. |
| AC-10 | Rendering | Template + context produce the expected `rendered_subject` / `rendered_text` / `rendered_html`. |
| AC-11 | Rendering | `subject` on the notification overrides the template's subject. |
| AC-12 | Rendering | Editing a template after send does not change a sent notification's `rendered_*`. |
| AC-13 | Rendering | Validation raises `ValidationError` for each invalid channel/body combination in [§2.2](#22-notification). |
| AC-14 | Delivery | `USE_CELERY=False` → delivered inline, `log.is_async is False`. |
| AC-15 | Delivery | `USE_CELERY=True` + `ALWAYS_EAGER` → delivered, `log.is_async is True`. |
| AC-16 | Delivery | Backend raising a retryable exception → `status == failed`, one `failed` log per attempt, `last_error` populated, `send_attempts` incremented. |
| AC-17 | Delivery | Empty `recipients` → log result `skipped`, no backend call. |
| AC-18 | Delivery | Email with both bodies arrives as `EmailMultiAlternatives` with a `text/html` alternative. |
| AC-39 | Rendering | An HTML-only body yields a plaintext part that keeps link targets and separates list items; an authored `body_text` is used verbatim instead. |
| AC-19 | In-site | `mark_read(1)` twice leaves `read_by == [1]`. |
| AC-20 | In-site | `InsiteBackend` sends nothing over the wire and still writes a `sent` log. |
| AC-21 | Multi-channel | `send_multi` over three channels creates exactly three rows sharing one non-null `group_id`, and three logs. |
| AC-22 | Multi-channel | One channel's backend failing leaves the sibling rows `sent` — no rollback, no shared status. |
| AC-23 | Multi-channel | A template key missing for a requested channel raises `ValidationError` and creates **zero** rows; with `require_all_templates=False` it creates rows for the channels that do have one. |
| AC-24 | Multi-channel | In non-prod with a `DefaultRecipient` for email only, the email row is redirected and the SMS row is `suppressed`. |
| AC-25 | Templates | Two `NotificationTemplate` rows may share a `key` across channels; a duplicate `("key", "channel")` pair raises `IntegrityError`. |
| AC-26 | Constraints | `bulk_create()` of an SMS notification with non-empty `body_html` raises `IntegrityError` — the check constraint holds where `clean()` is bypassed. |
| AC-27 | Constraints | A non-`insite` notification with a non-empty `read_by` raises `IntegrityError`. |
| AC-28 | Queries | `recipients__contains=["a@example.com"]` returns the expected rows, and `EXPLAIN` shows the GIN index in use on a seeded table. |
| AC-29 | Email | Three addresses in `recipients`, at the shipped default, produce **one** message with all three in `Bcc:`, none of them in `To:`, one SMTP transaction, and **one** log row. |
| AC-30 | Email | `RECIPIENT_MODE="to"` puts all three in `To:`; `"separate"` produces three messages, still one `Notification` and one log row. |
| AC-40 | Email | Under the `auto` default a single recipient goes in `To:` and two or more go in `Bcc:`; an explicitly configured mode is never overridden. |
| AC-41 | Trigger | `loaddata` and `serializers.deserialize` of a `status="ready"` row send nothing and write no log — restoring a backup must not re-deliver it. |
| AC-42 | Checks | `notifier.E002` flags only the alias the router sends notifier's writes to; an unrelated non-PostgreSQL alias does not block management commands. |
| AC-43 | Rendering | A self-closing `<style/>` / `<title/>` / `<pre/>` does not discard the rest of the derived plaintext. |
| AC-44 | Email | In `separate` mode a total retryable outage re-raises so retries engage, while a partial success never does. |
| AC-45 | Delivery | One channel's broken backend does not strand a sibling's pending dispatch, and a broker outage does not escape into the caller's `save()`. |
| AC-46 | Admin | The per-object re-send button posts and only posts: a `GET` on `<pk>/resend/` delivers nothing, and view-only staff neither see the button on the changelist or the change form nor can post to it. |
| AC-31 | SMS | Three numbers produce one `Notification` and one log row, with a per-number entry in `provider_response`. |
| AC-32 | Constraints | 501 recipients raises `IntegrityError`; 500 saves; `clean()` raises `ValidationError` at 501 with a readable message. |
| AC-33 | Rendering | A template using `{% for %}`, a filter and autoescaping renders identically to the same string through `Engine.from_string`; `{{ request }}` renders empty. |
| AC-34 | Rendering | A template with a syntax error raises `ValidationError` on `full_clean()`, not at send time. |
| AC-35 | Headless | With `INSTALLED_APPS = ["django.contrib.postgres", "notifier"]` — no `auth`, `admin` or `contenttypes`, and no user model — migrations apply and the **whole** suite passes unchanged. |
| AC-36 | Retries | Exhausting `CELERY_MAX_RETRIES` fires `notification_exhausted` exactly once, after the final `notification_failed`. |
| AC-37 | Env gating | Resolution order holds: `NOTIFIER["ENV"]` beats `settings.DJANGO_ENV`, which beats `$DJANGO_ENV`; with all three unset the env is `dev` and a send to a real address is `suppressed`. |
| AC-38 | Source | `send(..., source="x")` persists it; omitted, it is `""` and the send still succeeds; `filter(source="cron:x")` uses the index. |

---

## 9. Packaging & repo layout

```text
django-notifier-hub/               # repo root (github.com/ck-tech-nz/django-notifier-hub, public)
├── pyproject.toml                 # hatchling; uv for dev
├── README.md
├── PRD.md
├── CHANGELOG.md
├── src/notifier/
│   ├── __init__.py                # exports send(), version
│   ├── apps.py                    # NotifierConfig; connects signals in ready()
│   ├── conf.py                    # settings merge + setting_changed reload
│   ├── models.py
│   ├── dispatch.py                # dispatch() / deliver() — the single delivery path
│   ├── rendering.py               # RenderedMessage, template rendering
│   ├── recipients.py              # env gating + DefaultRecipient resolution
│   ├── signals.py
│   ├── tasks.py                   # guarded Celery import
│   ├── admin.py
│   ├── backends/
│   │   ├── base.py                # BaseBackend, BackendResult
│   │   ├── email.py               # DjangoEmailBackend
│   │   ├── sms.py                 # BaseSmsBackend, ConsoleSmsBackend, LocmemSmsBackend
│   │   └── insite.py
│   ├── management/commands/
│   └── migrations/
└── tests/
    ├── settings.py                 # full: auth + admin installed
    ├── settings_minimal.py         # headless: INSTALLED_APPS = ["notifier"]
    └── test_*.py
```

- **Build/dev:** `uv`, `hatchling`, `ruff` (lint + format), `pytest` + `pytest-django`.
- **Extras:** `pip install django-notifier-hub[celery]`.
- **CI:** GitHub Actions under `ck-tech-nz`; matrix over Python 3.13/3.14 × Django 5.2/6.0 via
  `nox` or `tox-uv` — four combinations, all upstream-supported — plus a coverage gate and an
  allowed-to-fail `Django main` job for early warning on 6.1. Add 6.1 to the matrix on release
  (expected 2026-08, when Django 6.0 leaves mainstream support).
- **Test database:** a PostgreSQL **service container**, not SQLite ([§2.9](#29-postgresql-only)).
  Also pin one job to the oldest Postgres still in support so `ArrayField`, `GinIndex` and the
  array-equality check constraints are verified against it, not only against latest.
- **Toolchain floor:** `requires-python = ">=3.13"`, `ruff target-version = "py313"`. No
  `from __future__ import annotations` anywhere — it has been unnecessary since 3.10.
- **Release:** public on PyPI as `django-notifier-hub`. Tag-triggered `uv build` +
  `pypa/gh-action-pypi-publish` via **Trusted Publishing** (OIDC) — the workflow job declares
  `permissions: id-token: write` and runs in a GitHub **environment** named `pypi`; PyPI verifies
  the OIDC claim (owner + repo + workflow filename + environment) and mints a short-lived token
  per run. **No API token exists in repo secrets, on any developer machine, or in this repo's
  history** — there is no long-lived credential to leak or rotate. A matching TestPyPI publisher
  on a `release/*` prerelease tag gives a dry run before the real thing.
- **Migrations** are generated by `makemigrations`, never hand-written, and squashed to a single
  file before the first release. With no FK to `AUTH_USER_MODEL`, the initial migration has no
  swappable dependency and can be applied in any order relative to the host's auth app.
- **Licence: MIT.** `LICENSE` at the repo root, `license = "MIT"` plus
  `license-files = ["LICENSE"]` in `pyproject.toml` (PEP 639 form — the deprecated
  `License :: OSI Approved :: MIT License` classifier is not used).
- **Support posture, stated in the README:** *published for reuse, best-effort, no support
  promised.* Issues and PRs are welcome but carry no response-time commitment, and the project may
  decline changes that do not serve its own use. Saying this at `0.1.0` is much easier than
  retrofitting it once strangers have expectations. It is a statement of intent, not a licence
  term — MIT's warranty disclaimer already covers the legal side.

---

## 10. Milestones

| # | Scope | Done when |
| --- | --- | --- |
| M1 | Models, migrations, admin, rendering, `DjangoEmailBackend`, inline dispatch, env gating, headless config | AC-01…AC-13, AC-17, AC-18, AC-25…AC-30, AC-32…AC-35, AC-37, AC-38 pass |
| M2 | Celery path, retries, `notification_exhausted`, `notifier_retry_failed` | AC-14…AC-16, AC-36 pass |
| M3 | SMS abstraction + console/locmem, in-site backend, `read_by`, `send_multi` + `group_id` | AC-19…AC-24, AC-31 pass |
| M4 | README with provider-integration example, LICENSE, CI matrix, `0.1.0` release | Green matrix, published |

---

## Open questions

**None.** Every design decision is settled — see [Resolved](#resolved) below. M1 is fully specified
and implementable as written.

Two things are deferred by decision rather than undecided, and neither blocks any milestone:

- **The SMS provider for the worked README example** ([§6.3](#63-sms)) waits until the real gateway
  is chosen. The abstraction does not depend on it; M3 ships console and locmem backends.
- **At-most-once handling** ([§4.3](#43-duplicate-sends)) is deliberately absent. If duplicates ever
  become an observed problem, the fix is compare-and-swap *plus* a stuck-`sending` reclaim sweep —
  evaluated and rejected for v1, not overlooked.

### Resolved

| | Decision | Date |
| --- | --- | --- |
| SMS backend | Pluggable abstraction + console backend only; no vendor SDK. Real provider deferred — expected to be an HTTP gateway, not a direct GSM modem | 2026-07-30 |
| In-site read state | `read_by` array of user ids; deliberately unindexed and unoptimised ([§6.4.1](#641-scope-read-state-is-explicitly-not-optimised)) | 2026-07-30 |
| Recipients | Plain string list; addresses, not users ([§2.8](#28-recipients-are-addresses-readers-are-users)) | 2026-07-30 |
| Package name | `django-notifier-hub` on PyPI, `notifier` as the import path | 2026-07-30 |
| Python / Django floor | Python ≥ 3.13, Django ≥ 5.2.8 — supported upstream versions only | 2026-07-30 |
| Database | PostgreSQL only; `ArrayField` over `JSONField` ([§2.9](#29-postgresql-only)) | 2026-07-30 |
| Model shape | One concrete table, not per-channel inheritance ([§2.6](#26-why-one-concrete-table-not-per-channel-models)) | 2026-07-30 |
| Multi-channel | One row per channel sharing a `group_id` ([§2.7](#27-sending-one-message-over-several-channels)) | 2026-07-30 |
| Email `RECIPIENT_MODE` | **`auto`**: `to` for one recipient, `bcc` for two or more. Started as a flat `bcc`; real-MTA testing showed a single-recipient Bcc envelope arrives with a blank recipient field, and with one recipient there is nothing to protect ([§6.2](#62-email)) | 2026-07-31 |
| Release | Public: GitHub + PyPI via Trusted Publishing ([§9](#9-packaging--repo-layout)) | 2026-07-30 |
| At-most-once | **Nothing done.** CAS rejected for v1 — it does not fix the failure that actually occurs, and introduces stuck-`sending` rows ([§4.3](#43-duplicate-sends)) | 2026-07-30 |
| Audit stamps | `created_at` / `updated_at` only. `created_by` / `updated_by` were specced and then **dropped** — an FK to `AUTH_USER_MODEL` is unusable on a headless service that has no users at all | 2026-07-30 |
| Headless install | Supported and CI-tested: no `auth` / `admin` / `contenttypes` and no user model. Needs `django.contrib.postgres` and a `DjangoTemplates` backend — corrected during M1 ([§2.10](#210-runs-on-a-service-with-no-users-no-auth-and-no-admin)) | 2026-07-30 |
| Recipient cap | 500, as a check constraint literal rather than a setting ([§2.2](#validation)) | 2026-07-30 |
| Log retention | `LOG_RETENTION_DAYS = 90`; the host must schedule `notifier_prune_logs` | 2026-07-30 |
| Retry policy | 3 retries, 30s exponential backoff, per-attempt log rows, plus a `notification_exhausted` signal on give-up | 2026-07-30 |
| Template engine | Full Django engine, `TemplateResponse` semantics minus the request ([§2.3](#rendering-semantics)) | 2026-07-30 |
| Version floor | Stay on Python 3.13 / Django 5.2 LTS. Checked against 3.14 / 6.0-only: nothing in the spec is constrained, `django.tasks` is unusable ([§5.0](#50-why-not-djangotasks)), and 5.2 LTS outlives 6.0 by a year | 2026-07-30 |
| `source` field | Added — free-text cause label, indexed, replacing the dropped `created_by` ([§2.11](#211-source-what-caused-this-send)) | 2026-07-30 |
| `ENV` resolution | `NOTIFIER["ENV"]` → `settings.DJANGO_ENV` → `$DJANGO_ENV` → `"dev"`. Django settings outrank the env var; the `"dev"` fallback is fail-safe | 2026-07-30 |
| Licence | MIT | 2026-07-30 |
| Support posture | Published for reuse, best-effort, no support promised | 2026-07-30 |

Two consequences of the public release that bind the code, not just the release job: nothing in the
repo may carry a real hostname, customer address, phone number or credential — fixtures and tests
use `example.com` / RFC-2606 names and Django's locmem/console backends only — and the public API
is a compatibility promise from `0.1.0` onward.
