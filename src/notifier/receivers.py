"""The trigger: a row reaching ``status="ready"`` is what causes delivery."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from notifier.models import Notification, Status


@receiver(post_save, sender=Notification, dispatch_uid="notifier.dispatch_on_ready")
def dispatch_on_ready(sender, instance: Notification, created: bool, raw: bool = False, **kwargs):
    """Dispatch when a notification arrives at ``ready``.

    Exactly two cases qualify: created as ``ready``, or updated *into* ``ready``
    from some other status. A ``ready`` -> ``ready`` re-save does not, so
    editing an already-queued row never re-sends it.
    """
    if raw:
        # `loaddata` and any other deserializer save with raw=True. Those rows
        # are historical records being restored, not new work -- without this
        # guard, loading a fixture or a serialized backup that happens to
        # contain `status="ready"` rows would send every one of them for real.
        return

    if instance.status != Status.READY:
        # Keep the tracker in step for later saves within this instance's life.
        instance._loaded_status = instance.status
        return

    previous = None if created else getattr(instance, "_loaded_status", None)
    instance._loaded_status = instance.status

    if previous == Status.READY:
        return

    from notifier.dispatch import dispatch

    dispatch(instance)
