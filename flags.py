"""Render nation flags without exposing image URLs in Discord text."""
from urllib.parse import urlsplit


def flag_url(value):
    value = (value or '').strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme.lower() in ('http', 'https') and parsed.netloc else None


def flag_text(value):
    """URLs belong in embed media; Unicode and Discord emoji remain inline."""
    return '' if flag_url(value) else (value or '').strip()


def flagged_embed(embed, *nations):
    """One flag uses a thumbnail; a second uses a labelled author icon."""
    images = [(flag_url(flag), name) for flag, name in nations if flag_url(flag)]
    if images:
        embed.set_thumbnail(url=images[0][0])
    if len(images) > 1:
        embed.set_author(name=str(images[1][1])[:256], icon_url=images[1][0])
    return embed
