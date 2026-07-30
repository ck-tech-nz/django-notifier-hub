"""Email delivery through ``django.core.mail``.

The package defines no SMTP settings of its own: ``EMAIL_HOST``,
``DEFAULT_FROM_EMAIL``, ``mail.outbox`` and any third-party email backend are
the configuration surface (PRD 3.1).
"""

import smtplib

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives

from notifier.backends.base import BackendResult, BaseBackend
from notifier.conf import notifier_settings
from notifier.rendering import RenderedMessage


class DjangoEmailBackend(BaseBackend):
    """One notification is one message, not one per address.

    Which header the addresses land in is set by
    ``NOTIFIER["EMAIL"]["RECIPIENT_MODE"]``, default ``"auto"``.
    """

    retryable_exceptions = (smtplib.SMTPException, OSError)

    @staticmethod
    def resolve_mode(mode: str, recipient_count: int) -> str:
        """Turn ``auto`` into a concrete header choice.

        With a single recipient there is no second party to leak an address to,
        so `to` is used: the message looks like ordinary mail to the reader and
        to spam filters, and the recipient sees their own address rather than an
        empty "To" field. From two recipients up, hiding the list is the
        recoverable default (PRD 6.2).
        """
        if mode != "auto":
            return mode
        return "to" if recipient_count <= 1 else "bcc"

    def send(
        self,
        notification,
        recipients: list[str],
        message: RenderedMessage,
    ) -> BackendResult:
        config = notifier_settings.EMAIL
        mode = self.resolve_mode(notifier_settings.recipient_mode, len(recipients))
        from_email = config["FROM"] or None  # None -> DEFAULT_FROM_EMAIL
        connection_kwargs = config["CONNECTION"] or {}
        connection = mail.get_connection(**connection_kwargs) if connection_kwargs else None

        if mode == "separate":
            return self._send_separately(recipients, message, from_email, connection, config)

        email = self._build(message, from_email, connection)
        if mode == "bcc":
            email.bcc = list(recipients)
        else:
            email.to = list(recipients)

        sent = email.send(fail_silently=config["FAIL_SILENTLY"])
        if not sent:
            return BackendResult.failure(
                "The email backend accepted no messages.", mode=mode, recipients=len(recipients)
            )
        return BackendResult.success(mode=mode, messages=1, recipients=len(recipients))

    def _send_separately(self, recipients, message, from_email, connection, config):
        per_address: dict[str, str] = {}
        errors: list[Exception] = []
        delivered = 0
        for address in recipients:
            email = self._build(message, from_email, connection)
            email.to = [address]
            try:
                count = email.send(fail_silently=config["FAIL_SILENTLY"])
            except ImproperlyConfigured:
                raise
            except Exception as exc:
                per_address[address] = f"{type(exc).__name__}: {exc}"
                errors.append(exc)
                continue
            if count:
                delivered += 1
                per_address[address] = "sent"
            else:
                per_address[address] = "not sent"

        response = {"mode": "separate", "messages": delivered, "per_recipient": per_address}
        if delivered == len(recipients):
            return BackendResult.success(**response)

        # Nothing got through and every failure is retryable -- an outage, not a
        # rejection. Re-raise so the retry machinery engages, matching
        # BaseSmsBackend. Without this, `separate` mode silently opts out of
        # retries that the `to` and `bcc` paths get for free, because those let
        # the exception escape to deliver().
        if (
            delivered == 0
            and errors
            and all(isinstance(exc, self.retryable_exceptions) for exc in errors)
        ):
            raise errors[0]

        # A partial success must NOT be retried: the addresses that already
        # received the message would get a second copy.
        return BackendResult.failure(
            f"{len(recipients) - delivered} of {len(recipients)} messages were not delivered.",
            **response,
        )

    def _build(self, message: RenderedMessage, from_email, connection) -> EmailMultiAlternatives:
        email = EmailMultiAlternatives(
            subject=message.subject,
            body=message.text,
            from_email=from_email,
            connection=connection,
        )
        if message.html:
            email.attach_alternative(message.html, "text/html")
        return email
