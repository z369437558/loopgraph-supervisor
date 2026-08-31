import re


def slugify(text):
    """Lowercase the input, replace runs of non-alphanumeric characters
    with a single hyphen, and strip leading/trailing hyphens."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')
