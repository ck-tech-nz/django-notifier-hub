"""Template rendering.

The full Django template engine, so ``{{ }}``, ``{% %}``, filters and
autoescaping behave exactly as in a ``TemplateResponse``. The one difference:
there is no request, so no ``RequestContext`` -- ``{{ request }}``,
``{{ user }}`` and ``{% csrf_token %}`` are unavailable (PRD 2.3).
"""

from dataclasses import dataclass

from django.template import Context, Template, TemplateSyntaxError

from notifier.conf import notifier_settings
from notifier.html2text import html_to_text


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    subject: str
    text: str
    html: str

    @property
    def is_empty(self) -> bool:
        return not (self.subject or self.text or self.html)


def check_template_syntax(source: str) -> str | None:
    """Return an error message if ``source`` will not compile, else ``None``."""
    try:
        Template(source)
    except TemplateSyntaxError as exc:
        return str(exc)
    return None


def render_string(source: str, context: dict) -> str:
    if not source:
        return ""
    return Template(source).render(Context(context))


def render_notification(notification) -> RenderedMessage:
    """Resolve the message body for ``notification`` without touching the DB."""
    from notifier.models import Channel

    context = {**notifier_settings.template_context(), **(notification.context or {})}
    template = notification.template

    # Inline values win over the template's, so a caller can override a single
    # part without cloning the template.
    subject_src = notification.subject or (template.subject if template else "")
    text_src = notification.body_text or (template.body_text if template else "")
    html_src = notification.body_html or (template.body_html if template else "")

    subject = render_string(subject_src, context)
    text = render_string(text_src, context)
    html = render_string(html_src, context)

    if notification.channel == Channel.SMS:
        # SMS carries no HTML at all, enforced by check constraint as well.
        html = ""
    elif html and not text:
        # Always give a plaintext alternative: some clients refuse HTML-only
        # mail, and it keeps the log readable. An authored `body_text` always
        # wins -- this is only the fallback when there is none.
        text = html_to_text(html)

    subject = _apply_non_prod_prefix(subject)
    return RenderedMessage(subject=subject[:255], text=text, html=html)


def _apply_non_prod_prefix(subject: str) -> str:
    if notifier_settings.is_production:
        return subject
    prefix = notifier_settings.NON_PROD_SUBJECT_PREFIX or ""
    if not prefix:
        return subject
    return f"{prefix.format(env=notifier_settings.env)}{subject}"
