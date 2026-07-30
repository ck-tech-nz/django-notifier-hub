"""SMS: the abstraction, plus console and locmem backends.

No vendor SDK is or becomes a dependency. Integrating a real provider is a
subclass of ``BaseSmsBackend`` that implements ``send_one`` (PRD 6.3).
"""

import sys

from notifier.backends.base import BackendResult, BaseBackend
from notifier.conf import notifier_settings
from notifier.rendering import RenderedMessage

#: Populated by LocmemSmsBackend. The SMS analogue of django.core.mail.outbox.
outbox: list[dict] = []


class BaseSmsBackend(BaseBackend):
    """Fans out over numbers and records a per-number outcome.

    Providers are per-number, so unlike email there is no single-transaction
    equivalent. It stays one ``Notification`` and one log row regardless: the
    unit of work is the send, not the number.

    Subclasses implement :meth:`send_one`.
    """

    def send_one(self, number: str, message: RenderedMessage) -> str:
        """Deliver to one number. Return a provider reference or status string.

        Raise to report a failure for this number.
        """
        raise NotImplementedError

    @property
    def sender(self) -> str | None:
        return notifier_settings.SMS["FROM"]

    @property
    def provider_options(self) -> dict:
        return {**notifier_settings.SMS["OPTIONS"], **self.options}

    def send(
        self,
        notification,
        recipients: list[str],
        message: RenderedMessage,
    ) -> BackendResult:
        per_number: dict[str, str] = {}
        errors: list[Exception] = []
        delivered = 0

        for number in recipients:
            try:
                per_number[number] = self.send_one(number, message)
            except Exception as exc:
                per_number[number] = f"{type(exc).__name__}: {exc}"
                errors.append(exc)
                continue
            delivered += 1

        response = {"messages": delivered, "per_recipient": per_number}
        if delivered == len(recipients):
            return BackendResult.success(**response)

        # Nothing got through and every failure is retryable: re-raise so the
        # retry machinery engages. Swallowing these into ok=False would make
        # retryable_exceptions meaningless for SMS -- a network blip would be
        # recorded as a permanent failure and never tried again.
        if delivered == 0 and errors and self._all_retryable(errors):
            raise errors[0]

        # A partial success must NOT be retried: the numbers that already went
        # through would receive a second message.
        return BackendResult.failure(
            f"{len(recipients) - delivered} of {len(recipients)} messages were not delivered.",
            **response,
        )

    def _all_retryable(self, errors: list[Exception]) -> bool:
        if not self.retryable_exceptions:
            return False
        return all(isinstance(exc, self.retryable_exceptions) for exc in errors)


class ConsoleSmsBackend(BaseSmsBackend):
    """Writes to stderr. Mirrors ``django.core.mail.backends.console``."""

    stream = None

    def send_one(self, number: str, message: RenderedMessage) -> str:
        stream = self.stream or sys.stderr
        sender = self.sender or "<unset>"
        stream.write(f"--- SMS to {number} from {sender} ---\n{message.text}\n")
        stream.flush()
        return "console"


class LocmemSmsBackend(BaseSmsBackend):
    """Appends to :data:`outbox` instead of sending. For tests."""

    def send_one(self, number: str, message: RenderedMessage) -> str:
        outbox.append(
            {
                "number": number,
                "sender": self.sender,
                "subject": message.subject,
                "text": message.text,
                "options": self.provider_options,
            }
        )
        return "locmem"


class FailingSmsBackend(BaseSmsBackend):
    """Always raises. Used by the retry tests; harmless in production."""

    retryable_exceptions = (RuntimeError,)

    def send_one(self, number: str, message: RenderedMessage) -> str:
        raise RuntimeError(f"Refusing to send to {number}.")
