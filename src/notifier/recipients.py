"""Environment gating: who actually receives a notification.

Fail-closed. Outside production, real recipients are replaced by the enabled
``DefaultRecipient`` rows for that channel; if there are none, the send is
suppressed and nothing leaves the process. The accident this produces is
"staging sent nothing", never "staging emailed the customer list" (PRD 3.2).
"""

from dataclasses import dataclass

from notifier.conf import notifier_settings


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of applying environment gating to a recipient list."""

    requested: list[str]
    effective: list[str]
    env: str
    #: True when non-prod gating found no default recipient and the send must
    #: not proceed.
    suppressed: bool
    redirected: bool

    @property
    def is_empty(self) -> bool:
        return not self.effective


def resolve_recipients(channel: str, requested: list[str]) -> Resolution:
    from notifier.models import Channel

    requested = list(requested or [])
    env = notifier_settings.env

    # In-site is exempt, in every environment. The gate exists to stop a
    # non-production system contacting real people, and in-site contacts nobody:
    # it writes no message anywhere outside the row that already exists. Gating
    # it would achieve nothing -- the Notification is persisted *before*
    # dispatch, so suppressing the "send" does not un-write it -- while making
    # in-site impossible to develop against locally without inventing a default
    # recipient with a user id.
    if channel == Channel.INSITE:
        return Resolution(
            requested=requested,
            effective=requested,
            env=env,
            suppressed=False,
            redirected=False,
        )

    if notifier_settings.is_production:
        return Resolution(
            requested=requested,
            effective=requested,
            env=env,
            suppressed=False,
            redirected=False,
        )

    # An empty request stays empty rather than becoming a send to the default
    # recipient: "nothing to do" must not turn into "mail the QA mailbox".
    if not requested:
        return Resolution(
            requested=requested, effective=[], env=env, suppressed=False, redirected=False
        )

    from notifier.models import DefaultRecipient

    defaults = list(
        DefaultRecipient.objects.filter(channel=channel, enabled=True)
        .order_by("address")
        .values_list("address", flat=True)
    )

    if defaults:
        return Resolution(
            requested=requested, effective=defaults, env=env, suppressed=False, redirected=True
        )

    if notifier_settings.SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT:
        return Resolution(
            requested=requested, effective=[], env=env, suppressed=True, redirected=False
        )

    # Explicitly opted out of the safety net.
    return Resolution(
        requested=requested, effective=requested, env=env, suppressed=False, redirected=False
    )
