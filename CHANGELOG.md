# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The public API is a compatibility promise from `0.1.0` onward.

## [Unreleased]

### Changed

- `notifier_retry_failed` gained `--include-sending`, to reclaim rows stranded at `sending` by a
  worker that died mid-delivery. It requires `--older-than`, because a row a live worker is
  legitimately working on is indistinguishable from a stranded one.

### Fixed

- **`loaddata` no longer sends.** The `post_save` receiver ignored Django's `raw=True`, so restoring
  a fixture or a serialized backup containing `status="ready"` rows delivered every one of them for
  real.
- **`notifier.E002` no longer blocks multi-database projects.** It checked every `DATABASES` alias
  rather than the one the router sends notifier's writes to, so an unrelated legacy MySQL or
  analytics SQLite connection made every management command fail.
- **One channel's failure no longer strands its siblings.** Dispatch is registered with
  `on_commit(..., robust=True)`, and the Celery enqueue is guarded, so a broken backend or a broker
  outage cannot silently discard the pending dispatch of other notifications committed in the same
  transaction — which `send_multi` had promised but did not deliver.
- **`separate` recipient mode now participates in retries.** A total retryable outage re-raises, as
  the SMS backend already did; a partial success still never retries.
- **A self-closing `<style/>` no longer eats the plaintext body.** `handle_startendtag` opened a
  skip region that nothing closed, silently truncating or blanking the `text/plain` part.
- **`py.typed` now ships**, so the advertised `Typing :: Typed` classifier is true.

- Documentation said a notification enqueued with no worker running would "sit at `sending`
  forever". Testing against a real worker showed otherwise, and the distinction matters: with no
  worker the row stays at **`ready`** and is delivered once a worker starts, because `sending` is
  set inside the worker. Only a worker dying *inside* the backend's `send()` strands a row at
  `sending`.

## [0.1.0] — unreleased

First release. Everything below is new, so this entry describes the shape of the package rather
than a diff.

### Added

#### Core

A `Notification` row reaching `status="ready"` is what causes delivery. Business code writes a row;
nothing calls a send function.

- Four models: `Notification`, `NotificationTemplate`, `NotificationLog` (append-only, one row per
  attempt), `DefaultRecipient`.
- Per-channel field rules enforced by database check constraints, not only `clean()`, so they hold
  on the write paths that bypass model validation — `bulk_create()`, `QuerySet.update()`, data
  migrations.
- `recipients` capped at 500 by a check-constraint literal.
- `source`, a free-text indexed label naming what caused a send (`"order.shipped"`,
  `"cron:daily-digest"`).
- `group_id`, linking the sibling rows of one multi-channel send.

#### Delivery

- One code path, `dispatch.deliver()`, shared by the inline and Celery routes; only
  `NotificationLog.is_async` differs.
- Dispatch deferred to `transaction.on_commit`, so a worker never races the enclosing transaction
  and a rollback sends nothing.
- Celery support via the `[celery]` extra, with exponential retry backoff and a
  `notification_exhausted` signal when the budget is spent.
- Four log outcomes — `sent`, `failed`, `suppressed`, `skipped` — where `suppressed` and `skipped`
  leave the status at `ready`.

#### Channels

- Email through `django.core.mail`, with `RECIPIENT_MODE` defaulting to **`auto`**: `to` for a
  single recipient, `bcc` for two or more. Two or more addresses in `To:` leaks one recipient to
  another and cannot be undone; one address has nothing to hide, and a Bcc-only envelope arrives
  with a blank recipient field. Explicit `to`, `bcc` and `separate` are available.
- An HTML email always carries a `text/plain` part. An authored `body_text` is used verbatim;
  otherwise it is derived by `notifier.html2text`, a stdlib-only converter that keeps link targets,
  block structure and list bullets. `strip_tags` was the first implementation and was replaced: it
  drops URLs entirely, so a plaintext reader saw "Track it here." with nothing to act on.
- SMS as an abstraction plus console and locmem backends. No vendor SDK is a dependency. A total
  retryable failure re-raises so retries engage; a partial success is reported but never retried,
  because retrying would double-send to the numbers that worked.
- In-site with no transport, and `read_by` as an integer array the host project owns.

#### Safety and configuration

- Fail-closed environment gating: outside `PRODUCTION_ENVS`, recipients are replaced by enabled
  `DefaultRecipient` rows, and with none the send is suppressed. A project that configures nothing
  is treated as non-production. In-site is exempt — it has no transport, and the row is persisted
  before dispatch, so gating it would protect nobody.
- One `NOTIFIER` settings dict, merged one level deep over defaults and re-read on
  `setting_changed`.
- System checks (`notifier.E001`–`E003`) reporting the three real requirements — the
  `django.contrib.postgres` app, a `DjangoTemplates` backend, and a PostgreSQL database — as one
  actionable message each.
- Admin registration for all four models, with a read-only log and a re-send action.
- `notifier_send_test`, `notifier_retry_failed` and `notifier_prune_logs`.
- `LOG_RETENTION_DAYS` defaulting to 90 days. Nothing prunes on a timer; the host schedules the
  command.

### Requirements

- Python ≥ 3.13, Django ≥ 5.2.8, **PostgreSQL only**.
- No foreign key to `AUTH_USER_MODEL` and no dependency on `django.contrib.auth`, `admin` or
  `contenttypes`, so the package installs on a headless service with no user accounts. The whole
  test suite runs against that configuration as well as a full one.

### Deliberately not included

Deduplication / idempotency, per-recipient delivery status, scheduled send, bounce webhooks,
attachments, digests, throttling, and fallback between channels. `PRD.md` records the reasoning for
each.

[Unreleased]: https://github.com/ck-tech-nz/django-notifier-hub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ck-tech-nz/django-notifier-hub/releases/tag/v0.1.0
