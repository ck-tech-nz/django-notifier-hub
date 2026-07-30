"""Context merged under every notification's own context.

The site origin belongs here rather than in each template: `{% url %}` yields a
path, and a path is not clickable in an email client.
"""


def extra_context() -> dict:
    return {"site_url": "https://app.example.com"}
