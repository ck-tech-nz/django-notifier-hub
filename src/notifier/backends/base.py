"""The backend contract.

What varies per channel is only *how the message leaves the process*. This is
the only place that variation lives -- ``dispatch.deliver()`` is
channel-agnostic (PRD 2.6).
"""

import abc
from dataclasses import dataclass, field
from typing import Any

from notifier.rendering import RenderedMessage


@dataclass(frozen=True, slots=True)
class BackendResult:
    """What a backend reports back.

    ``ok=False`` means the provider rejected the message in a way retrying will
    not fix. For transport failures, raise instead -- if the exception type is
    listed in ``retryable_exceptions`` the retry machinery will pick it up.
    """

    ok: bool
    provider_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, **provider_response: Any) -> "BackendResult":
        return cls(ok=True, provider_response=provider_response)

    @classmethod
    def failure(cls, error: str, **provider_response: Any) -> "BackendResult":
        return cls(ok=False, error=error, provider_response=provider_response)


class BaseBackend(abc.ABC):
    """Subclass to add a channel or swap a provider."""

    #: Exception types worth retrying. Raised exceptions of these types are
    #: reported as retryable; anything else is a permanent failure.
    retryable_exceptions: tuple[type[Exception], ...] = ()

    def __init__(self, **options: Any) -> None:
        self.options = options

    @property
    def dotted_path(self) -> str:
        cls = type(self)
        return f"{cls.__module__}.{cls.__qualname__}"

    @abc.abstractmethod
    def send(
        self,
        notification,
        recipients: list[str],
        message: RenderedMessage,
    ) -> BackendResult:
        """Deliver ``message`` to ``recipients``.

        ``recipients`` is already environment-gated -- it may differ from
        ``notification.recipients`` and is the list that must actually be used.
        """
        raise NotImplementedError
