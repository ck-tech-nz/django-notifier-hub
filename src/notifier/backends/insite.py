"""In-site: no transport at all.

The message is already persisted; the rendered snapshot is what the host
project reads. Read state is ``Notification.read_by``, owned by the host
project and deliberately left unindexed and unoptimised (PRD 6.4.1).
"""

from notifier.backends.base import BackendResult, BaseBackend
from notifier.rendering import RenderedMessage


class InsiteBackend(BaseBackend):
    def send(
        self,
        notification,
        recipients: list[str],
        message: RenderedMessage,
    ) -> BackendResult:
        return BackendResult.success(recipients=len(recipients), transport="none")
