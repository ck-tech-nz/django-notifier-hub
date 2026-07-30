# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**All four milestones are implemented; `0.1.0` is not published.** 219 tests pass against both
settings modules at ~93% coverage, ruff is clean, and CI plus the Trusted-Publishing release
workflow are in `.github/workflows/`. What remains before a release is tagging: the version in
`pyproject.toml` is `0.1.0.dev0`, and the release workflow refuses a tag that does not match it.

`PRD.md` is the specification and the source of truth. It is unusually decision-dense: most
sections record *why* an alternative was rejected, and several explicitly correct an earlier draft.
Read the relevant section before implementing — the obvious approach is frequently the one the PRD
already rejected with reasons (see [Invariants](#invariants--do-not-quietly-undo-these)).

Naming, which differs at every layer:

| | |
| --- | --- |
| Local directory | `~/Git/packages/notification` |
| Distribution | `django-notifier-hub` (**not** `django-notifier` — taken on PyPI since 2013) |
| Import path / app label | `notifier` (under `src/`) |
| Target repo | `github.com/ck-tech-nz/django-notifier-hub` |
| Settings namespace | `NOTIFIER` (one dict) |

Only the *distribution* carries the `-hub` suffix. `pip install django-notifier-hub` then
`INSTALLED_APPS = [..., "notifier"]` and `from notifier.models import Notification` — the import
path, app label, settings namespace and public API are all unsuffixed. Do not "correct" one to
match the other; PRD "On the distribution name" records why they differ.

## This is a public repository

The repo is public on GitHub and the package publishes publicly to PyPI (PRD open question 6,
resolved 2026-07-30). Everything committed here is world-readable **and permanent** — a force-push
does not unpublish what a fork, a CI log or the GitHub Events API already captured. Two rules
follow:

- **No real-world data, ever.** No customer or staff email addresses, phone numbers, hostnames,
  API keys, SMS provider credentials, database URLs, or internal service names — not in tests, not
  in fixtures, not in docstrings, not in migration data, not in commit messages. Use RFC-2606
  reserved domains (`example.com`, `example.org`), RFC-3849 addresses, and Django's
  `locmem`/console backends. Test recipients are `to@example.com`, never a real inbox.
- **No credential lands in the repo, by design.** Releases use PyPI Trusted Publishing (OIDC), so
  there is no PyPI API token in repo secrets or on any machine — see PRD §9. If a task seems to
  need a long-lived token committed anywhere, that is the signal to stop and ask, not to add one.

Because it is public, the audience is not just this project: the README, error messages and
`0.1.0` public API are a compatibility promise to strangers. Prefer clear failure messages over
internal shorthand.

## Commands

```bash
set -a; . ./.env.test; set +a        # local Postgres parameters (gitignored)

uv sync --all-extras --group dev     # hatchling build backend
uv run ruff check . && uv run ruff format .
uv run pytest                        # full settings
uv run pytest --ds=tests.settings_minimal          # headless settings (AC-35)
uv run pytest -k "ac_07 or suppressed"             # tests are named after acceptance criteria
uv build                             # sdist + wheel
```

**Tests require a live PostgreSQL server.** There is no SQLite fallback — `ArrayField`, `GinIndex`
and the array-equality check constraints are all load-bearing (PRD §2.9). CI runs a Postgres service
container; locally you need a reachable Postgres before `pytest` will collect.

There are **two** settings modules and **the whole suite runs against both**.
`tests/settings.py` has `auth` and `admin` installed; `tests/settings_minimal.py` is the headless
configuration (PRD §2.10, AC-35). Run the second with `uv run pytest --ds=tests.settings_minimal`.

Local Postgres connection parameters come from `NOTIFIER_TEST_DB_*` environment variables so nothing
is committed; `.env.test` is gitignored and holds them for this machine.

`NOTIFIER["DISPATCH_ON_COMMIT"]` is **False** in both test settings modules: pytest-django rolls
back the transaction wrapping each test, so `transaction.on_commit` callbacks never fire. The
on-commit wrapper itself is what AC-05 tests, and those tests re-enable it and drive the callbacks
explicitly. Do not "fix" a test by re-enabling it globally -- the rest of the suite would silently
stop sending.

Version floors are deliberate and narrow: `requires-python = ">=3.13"`, `Django>=5.2.8`. The
`.8` matters — Python 3.14 support landed in Django 5.2.8, and 3.14 is supported here. CI matrix is
Python 3.13/3.14 × Django 5.2/6.0, driven directly by the workflow matrix rather than nox or tox,
plus one job on the oldest supported PostgreSQL and an allowed-to-fail `Django main` job.

**Do not propose raising the floor to Python 3.14 / Django 6.0.** It was evaluated on 2026-07-30 and
rejected: nothing in the spec is constrained by 5.2/3.13, `django.tasks` is unusable (no retries, no
Celery backend, dev-only workers — PRD §5.0), and Django 5.2 LTS outlives 6.0 by a year (2028-04 vs
2027-04). The upgrade worth planning for is 6.2 LTS in 2027-04.

## Architecture

The whole design turns on one idea: **a `Notification` row reaching `status="ready"` is what causes
delivery.** Business code never calls a send function; it writes a row. Everything else follows.

**Trigger chain** (PRD §4). `Model.from_db` stashes `instance._loaded_status` with no extra query →
a `post_save` receiver compares it against the new status → on `create(status=ready)` or
`not-ready → ready`, it calls `dispatch.dispatch()`, wrapped in `transaction.on_commit`. The
on-commit wrapping is not optional in practice: without it a Celery worker races the enclosing
transaction and gets `DoesNotExist` on a row that was just created.

**One delivery path** (PRD §5.2). `dispatch.deliver(notification)` is the only code that sends.
The Celery task and the inline path both call it; the only difference is which side of the queue it
runs on, and `NotificationLog.is_async`. Do not grow a second implementation for either path.
Celery is auto-detected (importable *and* has a configured broker) unless `USE_CELERY` forces it.

`deliver()` re-raises retryable exceptions so the Celery task can retry. The **inline** path
therefore swallows them (`dispatch._enqueue`) -- inline there is nothing to retry with, and letting
an SMTP error escape would throw it out of the caller's `save()`. `ImproperlyConfigured` propagates
from both paths: a config bug must reach the developer, not be filed as a delivery failure.

**Polymorphism lives in the backend layer, not the model layer** (PRD §2.6). One concrete
`Notification` table for all three channels, because `NotificationLog` needs a single FK target —
"everything that failed in the last hour, across all channels" as one indexed query is the core
value. What actually varies per channel is only *how the message leaves the process*, which is
`backends/base.BaseBackend`. `deliver()` is channel-agnostic; only the backend swaps, resolved
through `NOTIFIER["BACKENDS"]`.

**Four models** in `notifier/models.py`: `Notification` (unit of work), `NotificationTemplate`
(Django-template strings, unique on `("key", "channel")`), `NotificationLog` (append-only, one row
per *attempt*), `DefaultRecipient` (the non-prod safety net).

**Runs headless** (PRD §2.10). No `auth`, no `admin`, no `contenttypes`, no user model. A likely
deployment is a microservice with no human users at all, which is why `created_by`/`updated_by` were
specced and then dropped, and why `source` exists instead.

The minimal configuration is `INSTALLED_APPS = ["django.contrib.postgres", "notifier"]` plus a
`DjangoTemplates` backend in `TEMPLATES`. Both requirements were missed in the PRD's first draft and
corrected during M1: `django.contrib.postgres` **is** required (Django's `postgres.E005` check
refuses `ArrayField` without it, whatever the field docs imply), and rendering goes through the
project's template engine. `notifier/checks.py` turns each into one actionable error
(`notifier.E001`, `notifier.E003`) instead of four opaque ones.

**Environment gating** (PRD §3.2) is fail-closed. The env resolves as `NOTIFIER["ENV"]` →
`settings.DJANGO_ENV` → `$DJANGO_ENV` → `"dev"`; Django settings outrank the environment variable,
and the `"dev"` fallback means an unconfigured project cannot reach real recipients. Outside
`PRODUCTION_ENVS`, recipients are
replaced by enabled `DefaultRecipient` rows for that channel; if there are none, the result is
`suppressed` and nothing leaves the process. `requested_recipients` on the log keeps the original
list so the log shows what *would* have gone out in prod. A misconfigured staging environment must
never reach real customers.

**Snapshots.** `rendered_subject` / `rendered_text` / `rendered_html` are written at send time.
Templates change; the snapshot is the record, and `template` + `context` are merely the inputs.
`template` is `SET_NULL` so deleting a template never deletes history.

**Multi-channel** (PRD §2.7) is N rows sharing a nullable `group_id` UUID — one row per channel,
created in one transaction, dispatched independently after commit. `filter(group_id=...)` is the
entire group API.

## Invariants — do not quietly undo these

Each of these was decided against a plausible alternative. Changing one means revisiting the PRD
section, not just the code.

- **`condition=` on `CheckConstraint`, never `check=`.** `check` was deprecated in Django 5.1 and
  removed in 6.0; the 5.2 floor makes `condition=` unconditionally available.
- **No `from __future__ import annotations`** anywhere — unnecessary since 3.10.
- **No FK to `AUTH_USER_MODEL`**, ever. `recipients` and `read_by` hold raw ids/addresses so the app
  installs into any project regardless of auth setup — including one with no users at all.
  `created_by`/`updated_by` were specced on 2026-07-30 and **removed**; `source` (a plain indexed
  `CharField`) is what answers "what caused this send". Do not reintroduce the FKs.
- **Nothing in `notifier` may import `django.contrib.auth`, `admin` or `contenttypes`** at module
  scope. `admin.py` is fine — it is only loaded by admin autodiscovery. AC-35 fails the build
  otherwise.
- **Email `RECIPIENT_MODE` defaults to `auto`**: `to` for one recipient, `bcc` for two or more, on
  the *effective* list after non-prod redirection. It was a flat `bcc` until real-MTA testing showed
  a single-recipient Bcc envelope arrives with a blank recipient field. Do not flatten it back to
  either extreme -- `bcc` everywhere makes ordinary transactional mail look wrong, `to` everywhere
  leaks addresses between customers (PRD §6.2).
- **`recipients` is capped at 500 by a check-constraint literal**, not a setting — constraints are
  frozen into migrations and cannot read `settings`, so a knob would drift from what the database
  enforces. Changing the cap means a migration.
- **`TextChoices`, not `IntegerChoices`.** `NOTIFIER["BACKENDS"]` is keyed by channel *name* and the
  public API is `send(channel="email")` — integers would make settings and database disagree.
- **`NotificationTemplate.key` is not unique on its own.** Uniqueness is `("key", "channel")`, so one
  logical key resolves per channel. A globally-unique `key` breaks `send_multi`.
- **`suppressed` and `skipped` leave `status` at `ready`** — not `sent`, not `failed`. The notification
  never claimed to be sent, and re-running it in a configured environment must work without a manual
  status reset. The outcome lives only in the log row.
- **No `channels` list field on `Notification`.** One row has one status; three channels have three
  outcomes. Collapsing them makes `status`, `sent_at`, `send_attempts` and retry all lie.
- **Django is the only hard dependency.** Celery is an extra, imported behind a guard in `tasks.py`
  so the module imports without it. No SMS provider SDK ever becomes a dependency — the package
  ships `BaseSmsBackend` + console/locmem only.
- **Do not define SMTP settings.** Email goes through `django.core.mail`, so `EMAIL_HOST`,
  `DEFAULT_FROM_EMAIL`, `mail.outbox` etc. are the configuration surface.
  `NOTIFIER["EMAIL"]["CONNECTION"]` exists only for sending over a *different* server.
- **Check constraints, not just `clean()`.** `clean()` is not called by `save()` and is bypassed by
  `bulk_create()`, `QuerySet.update()` and data migrations. Per-channel field rules belong in the
  database (AC-26, AC-27 test exactly this).
- **In-site is exempt from environment gating**, deliberately. It has no transport, and the row is
  persisted before dispatch, so suppressing the send protects nobody -- it only makes in-site
  undevelopable locally. Do not "restore consistency" by gating it.
- **`BaseSmsBackend` re-raises only when nothing got through and every failure is retryable.** A
  partial success must never be retried: the numbers that worked would get a second message. The
  first implementation swallowed everything into `ok=False`, which made `retryable_exceptions` dead
  code for SMS.
- **The plaintext alternative is derived by `notifier/html2text.py`, never `strip_tags`.**
  `strip_tags` drops link targets entirely, so a plaintext reader gets "Track it here." with no URL
  -- delivered but useless. An authored `body_text` always wins; the converter is only the fallback.
  It is stdlib-only on purpose: Django stays the single dependency, so do not reach for `html2text`
  or `beautifulsoup4`.
- **The `post_save` receiver must skip `raw=True` saves.** Without it `loaddata` and any
  deserializer re-deliver every restored `status="ready"` row for real (AC-41).
- **Dispatch is registered `on_commit(..., robust=True)`, and the Celery enqueue is guarded.**
  Django clears the hook list before running it, so one notification's failure would otherwise
  discard every sibling dispatch committed in the same transaction (AC-45).
- **Error messages use channel *values*, not enum members.** `str(channel)` before interpolating;
  `{channel!r}` on a `TextChoices` member leaks `Channel.INSITE` into text a user reads.

**Explicitly out of scope for v1** (PRD §1 non-goals, §4.3). Do not "fix" these without being asked:
deduplication / idempotency (`ready → draft → ready` sends twice, by decision), per-recipient
delivery status, scheduled send, bounce webhooks, attachments, digest/batching, and
fallback/escalation between channels.

## Working conventions

- **Coverage gate is 90%** (`--cov-fail-under=90` in CI). Currently ~93%. If a change drops below,
  add the test rather than lowering the gate -- the last time this came up, the gap was `checks.py`,
  i.e. the actionable error messages for the two requirements the PRD had got wrong.
- **Acceptance criteria are the test plan.** PRD §8 lists AC-01…AC-38, each mapping to one test;
  §10 maps them to milestones M1–M4. Reference the AC id in test names and PR descriptions, and
  extend the table rather than adding untracked tests.
- **Migrations are generated by `makemigrations`, never hand-written**, and squashed to a single file
  before the `0.1.0` release.
- **There are no open questions left.** As of 2026-07-30 every design decision is settled; PRD
  "Resolved" is the ledger, with dates. Two items are *deferred by decision*, not undecided: the
  first real SMS provider for the README example (M4), and at-most-once handling (rejected for v1,
  PRD §4.3). If something genuinely under-specified turns up, ask — do not settle it in code and
  leave the PRD stale.
- **Settled defaults worth not re-litigating:** `LOG_RETENTION_DAYS = 90` (nothing prunes
  automatically — the host schedules `notifier_prune_logs`); 3 retries at 30s exponential backoff
  plus a `notification_exhausted` signal on give-up; MIT licence; support posture is "published for
  reuse, best-effort, no support promised".
