"""Search existing Commons images; never generate artwork."""
import asyncio
import html
import re
import aiohttp


def search_topic(text):
    topics = [
        (r'powód|powodz|flood', 'historical flood'),
        (r'pożar|pozar|fire', 'historic city fire'),
        (r'głód|glod|famine|harvest|żniw|plon', 'harvest painting'),
        (r'bunt|rebell|revolt|protest', 'popular revolt painting'),
        (r'epidemi|plague|disease|chorob', 'historical plague'),
        (r'handel|handlow|trade|merchant|kupc', 'market painting'),
        (r'okręt|statek|flot|naval|ship|port', 'sailing ships painting'),
        (r'bitw|wojn|battle|war|army', 'battle painting'),
        (r'koloni|colon|explor|wypraw', 'exploration expedition painting'),
        (r'traktat|pokój|pokoju|treaty|peace|dyplom', 'peace treaty painting'),
    ]
    return next((query for pattern, query in topics if re.search(pattern, text, re.I)),
                'historic town painting')


def pick_image(data):
    for page in sorted(data.get('query', {}).get('pages', {}).values(), key=lambda p: p.get('index', 999)):
        for info in page.get('imageinfo', []):
            meta = info.get('extmetadata', {})
            plain = lambda key: html.unescape(re.sub('<[^>]+>', '', str(meta.get(key, {}).get('value', ''))))
            description = (page.get('title', '') + ' ' + plain('Categories') + ' ' + plain('ImageDescription')).lower()
            if any(word in description for word in ('ai-generated', 'ai generated', 'stable diffusion', 'midjourney', 'dall-e', 'generated with ai')):
                continue
            license_name = plain('LicenseShortName')
            if license_name.lower() not in ('public domain', 'cc0', 'cc0 1.0'):
                continue
            url = info.get('thumburl', info.get('url', ''))
            source = info.get('descriptionurl', '')
            if (info.get('mime') not in ('image/jpeg', 'image/png', 'image/webp')
                    or not url.startswith('https://upload.wikimedia.org/')
                    or not source.startswith('https://commons.wikimedia.org/')):
                continue
            return {'url': url, 'source': source, 'credit': (plain('Artist') or 'Wikimedia Commons')[:180],
                    'license': license_name}
    return None


async def find_event_image(text, query=''):
    params = {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': '6',
              'gsrsearch': (query.strip()[:120] or search_topic(text)) + ' -"AI-generated"',
              'gsrlimit': '20', 'prop': 'imageinfo', 'iiprop': 'url|mime|extmetadata', 'iiurlwidth': '1000'}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),
                headers={'User-Agent': 'WargameBot/1.0 (event illustrations; Wikimedia Commons)'}) as session:
            async with session.get('https://commons.wikimedia.org/w/api.php', params=params) as response:
                response.raise_for_status()
                return pick_image(await response.json())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
