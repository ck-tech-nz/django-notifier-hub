"""AC-31 and the SMS abstraction.

The package ships no vendor SDK: a real provider is a subclass implementing
``send_one``. These tests double as the contract that subclass must satisfy.
"""

import io

import pytest

from notifier.backends import sms
from notifier.backends.base import BackendResult
from notifier.backends.sms import BaseSmsBackend, ConsoleSmsBackend, LocmemSmsBackend
from notifier.models import Channel, LogResult, Notification, Status
from notifier.rendering import RenderedMessage

pytestmark = pytest.mark.django_db

THREE = ["+6421000001", "+6421000002", "+6421000003"]


def make_sms(**overrides):
    fields = {
        "channel": Channel.SMS,
        "status": Status.READY,
        "recipients": ["+6421000001"],
        "body_text": "Your code is 1234.",
    }
    fields.update(overrides)
    return Notification.objects.create(**fields)


def test_ac_31_many_numbers_are_one_notification_and_one_log(production):
    notification = make_sms(recipients=THREE)

    assert len(sms.outbox) == 3
    assert [m["number"] for m in sms.outbox] == THREE
    # The unit of work is the send, not the number.
    assert notification.logs.count() == 1

    response = notification.logs.get().provider_response
    assert response["messages"] == 3
    assert response["per_recipient"] == dict.fromkeys(THREE, "locmem")


def test_a_single_number_works_the_same_way(production):
    notification = make_sms()

    assert len(sms.outbox) == 1
    assert notification.logs.get().result == LogResult.SENT


def test_sms_never_carries_html(production):
    notification = make_sms(body_text="Plain only.")

    notification.refresh_from_db()
    assert notification.rendered_html == ""
    assert sms.outbox[0]["text"] == "Plain only."


def test_sms_renders_its_template(production, sms_template):
    make_sms(template=sms_template, body_text="", context={"order": "A-9"})

    assert sms.outbox[0]["text"] == "Order A-9 shipped."


def test_sender_and_options_reach_the_backend(production, settings):
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "SMS": {"FROM": "NOTIFY", "OPTIONS": {"template_code": "SMS_001"}},
    }

    make_sms()

    assert sms.outbox[0]["sender"] == "NOTIFY"
    assert sms.outbox[0]["options"] == {"template_code": "SMS_001"}


def test_non_prod_redirects_sms_to_its_own_default_recipient(settings):
    from notifier.models import DefaultRecipient

    settings.DJANGO_ENV = "dev"
    DefaultRecipient.objects.create(channel=Channel.SMS, address="+6427000000", enabled=True)
    # An email default must not be used for SMS.
    DefaultRecipient.objects.create(channel=Channel.EMAIL, address="qa@example.com", enabled=True)

    notification = make_sms(recipients=THREE)

    assert [m["number"] for m in sms.outbox] == ["+6427000000"]
    log = notification.logs.get()
    assert log.requested_recipients == THREE
    assert log.effective_recipients == ["+6427000000"]


# -- the abstraction ---------------------------------------------------------


def test_console_backend_writes_to_its_stream(production):
    stream = io.StringIO()
    backend = ConsoleSmsBackend()
    backend.stream = stream

    result = backend.send(None, ["+6421000001"], RenderedMessage("", "Hello.", ""))

    assert isinstance(result, BackendResult)
    assert result.ok
    assert "+6421000001" in stream.getvalue()
    assert "Hello." in stream.getvalue()


def test_a_partial_failure_is_reported_but_not_raised(production):
    """Retrying would re-send to the numbers that already worked."""

    class HalfBroken(BaseSmsBackend):
        retryable_exceptions = (RuntimeError,)

        def send_one(self, number, message):
            if number.endswith("2"):
                raise RuntimeError("carrier rejected")
            return "ok"

    result = HalfBroken().send(None, THREE, RenderedMessage("", "Hi.", ""))

    assert result.ok is False
    assert result.provider_response["messages"] == 2
    assert "carrier rejected" in result.provider_response["per_recipient"]["+6421000002"]


def test_a_total_retryable_failure_raises_so_retries_engage(production):
    """Otherwise `retryable_exceptions` would be meaningless for SMS."""

    class Broken(BaseSmsBackend):
        retryable_exceptions = (RuntimeError,)

        def send_one(self, number, message):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        Broken().send(None, THREE, RenderedMessage("", "Hi.", ""))


def test_a_total_non_retryable_failure_is_reported_not_raised(production):
    class Broken(BaseSmsBackend):
        retryable_exceptions = ()

        def send_one(self, number, message):
            raise ValueError("malformed number")

    result = Broken().send(None, THREE, RenderedMessage("", "Hi.", ""))

    assert result.ok is False
    assert result.provider_response["messages"] == 0


def test_base_backend_requires_send_one():
    class Incomplete(BaseSmsBackend):
        pass

    with pytest.raises(NotImplementedError):
        Incomplete().send_one("+6421000001", RenderedMessage("", "Hi.", ""))


def test_locmem_outbox_records_everything_needed_to_assert_on(production):
    LocmemSmsBackend().send(None, ["+6421000001"], RenderedMessage("Subj", "Body", ""))

    assert sms.outbox[-1]["number"] == "+6421000001"
    assert sms.outbox[-1]["subject"] == "Subj"
    assert sms.outbox[-1]["text"] == "Body"


def test_dotted_path_identifies_the_backend_in_the_log(production):
    notification = make_sms()

    assert notification.logs.get().backend == "notifier.backends.sms.LocmemSmsBackend"
