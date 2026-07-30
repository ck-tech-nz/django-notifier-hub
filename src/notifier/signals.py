"""Public signals. All are sent with ``sender=Notification``."""

import django.dispatch

#: After rendering, before the backend runs.
#: kwargs: notification, recipients, message
pre_send = django.dispatch.Signal()

#: The backend accepted the message. kwargs: notification, log
notification_sent = django.dispatch.Signal()

#: The backend raised or reported a rejection. kwargs: notification, log, exception
notification_failed = django.dispatch.Signal()

#: Non-production, and no enabled DefaultRecipient to redirect to. Nothing left
#: the process. kwargs: notification, log
notification_suppressed = django.dispatch.Signal()

#: The final retry failed and the send is given up on. Fires once per
#: notification, after the notification_failed for that last attempt --
#: "attempt 3 of 3 failed" and "we have stopped trying" are different events,
#: and only the second is worth alerting on. kwargs: notification, log, attempts
notification_exhausted = django.dispatch.Signal()
