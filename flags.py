"""Render flags consistently, including file pages, emoji and durable uploads."""
import os
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

ASSET = re.compile(r'flag:([0-9a-f]{64})\Z')
CUSTOM_EMOJI = re.compile(r'<(a?):[A-Za-z0-9_]+:(\d{15,22})>\Z')


def clean_source(value):
    value = (value or '').strip()
    link = re.fullmatch(r'!?\[[^\]]*\]\((https?://.+)\)', value, re.I)
    if link: value = link[1]
    if value.lower().startswith('<http') and value.endswith('>'): value = value[1:-1]
    return value


def http_source(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 80, 443):
            return parsed
    except ValueError:
        pass
    return None


def public_base():
    value = (os.getenv('FLAG_PUBLIC_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or '').strip().rstrip('/')
    parsed = http_source(value)
    return value if parsed and parsed.scheme.lower() == 'https' and not parsed.query and not parsed.fragment else None


def flag_url(value):
    value = clean_source(value)
    asset = ASSET.fullmatch(value)
    if asset:
        base = public_base()
        return f'{base}/flags/{asset[1]}.png' if base else None
    emoji = CUSTOM_EMOJI.fullmatch(value)
    if emoji:
        ext = 'gif' if emoji[1] else 'png'
        return f'https://cdn.discordapp.com/emojis/{emoji[2]}.{ext}?size=128&quality=lossless'
    if len(value) == 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in value):
        country = ''.join(chr(ord(ch) - 0x1F1E6 + ord('a')) for ch in value)
        return f'https://flagcdn.com/w320/{country}.png'
    parsed = http_source(value)
    if not parsed: return None
    host = parsed.hostname.lower()
    parts = parsed.path.split('/')
    if host in ('github.com', 'www.github.com') and len(parts) >= 6 and parts[3] in ('blob', 'raw'):
        path = '/'.join(parts[1:3] + parts[4:])
        return 'https://raw.githubusercontent.com/' + quote(unquote(path), safe='/%:@')
    title = unquote(parsed.path.removeprefix('/wiki/'))
    if (host == 'commons.wikimedia.org' or host.endswith('.wikipedia.org')) and parsed.path.startswith('/wiki/') and re.match(r'^(File|Image|Plik):', title, re.I):
        filename = title.split(':', 1)[1].replace(' ', '_')
        return f'https://{host}/wiki/Special:Redirect/file/{quote(filename, safe="")}?width=960'
    if host == 'upload.wikimedia.org' and parsed.path.lower().endswith('.svg') and '/thumb/' not in parsed.path:
        project = parts[2] if len(parts) > 2 else ''
        if project == 'commons' or re.fullmatch(r'[a-z-]+', project):
            wiki = 'commons.wikimedia.org' if project == 'commons' else project + '.wikipedia.org'
            return f'https://{wiki}/wiki/Special:Redirect/file/{quote(unquote(parts[-1]), safe="")}?width=960'
    # In particular, preserve the signatures on Discord attachment URLs.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, quote(parsed.path, safe='/%:@'), parsed.query, ''))


def flag_text(value):
    """URLs belong in embed media; Unicode and Discord emoji remain inline."""
    value = clean_source(value)
    return '' if ASSET.fullmatch(value) or http_source(value) else value


def flagged_embed(embed, *nations):
    """One flag uses a thumbnail; a second uses a labelled author icon."""
    images = [(url, name) for flag, name in nations if (url := flag_url(flag))]
    if images:
        embed.set_thumbnail(url=images[0][0])
    if len(images) > 1:
        embed.set_author(name=str(images[1][1])[:256], icon_url=images[1][0])
    return embed
