"""Find and download existing public-domain illustrations; never generate artwork."""
import asyncio
import html
import io
import logging
import re
from urllib.parse import urlsplit, urljoin

import aiohttp

MAX_BYTES = 6 * 1024 * 1024
PROVIDER_TIMEOUT = 12
USER_AGENT = 'WargameBot/1.1 (https://github.com/masterclash823-hub/wargame-bot-exploration; event illustrations)'
AI_WORDS = ('ai-generated', 'ai generated', 'stable diffusion', 'midjourney', 'dall-e', 'generated with ai')
log = logging.getLogger(__name__)


def plain(value):
    return html.unescape(re.sub('<[^>]+>', '', str(value or ''))).strip()


def trusted_url(value, hosts):
    try:
        p=urlsplit(value)
        return p.scheme=='https' and p.hostname in hosts and not p.username and not p.password and p.port in (None,443)
    except (TypeError,ValueError):return False


def search_topic(text):
    topics = [
        (r'powód[źz]|powodz|flood', 'historical flood'),
        (r'pożar|pozar|fire', 'historic city fire'),
        (r'głód|glod|famine|harvest|żniw|plon', 'harvest painting'),
        (r'bunt|rebell|revolt|protest', 'popular revolt painting'),
        (r'epidemi|plague|disease|chorob', 'historical plague'),
        (r'handel|handlow|trade|merchant|kupc', 'market painting'),
        (r'okręt|statek|flot|naval|ship|port', 'sailing ships painting'),
        (r'bitw|wojn|battle|\bwar\b|army', 'battle painting'),
        (r'koloni|colon|explor|wypraw', 'exploration expedition painting'),
        (r'traktat|pokój|pokoju|treaty|peace|dyplom', 'peace treaty painting'),
    ]
    return next((query for pattern, query in topics if re.search(pattern, text, re.I)),
                'historic town painting')


def commons_images(data):
    pages=data.get('query', {}).get('pages', {})
    pages=list(pages.values()) if isinstance(pages,dict) else pages
    if not isinstance(pages,list):return
    for page in sorted((p for p in pages if isinstance(p,dict)),key=lambda p:p.get('index',999)):
        for info in page.get('imageinfo', []):
            meta = info.get('extmetadata', {})
            field = lambda key: plain(meta.get(key, {}).get('value', ''))
            description = (page.get('title', '') + ' ' + field('Categories') + ' ' + field('ImageDescription')).lower()
            if any(word in description for word in AI_WORDS):
                continue
            license_name = field('LicenseShortName')
            if license_name.lower() not in ('public domain', 'cc0', 'cc0 1.0'):
                continue
            url = info.get('thumburl', info.get('url', ''))
            source = info.get('descriptionurl', '')
            mime=info.get('thumbmime',info.get('mime')) if info.get('thumburl') else info.get('mime')
            if (mime not in ('image/jpeg', 'image/png', 'image/webp')
                    or not trusted_url(url,{'upload.wikimedia.org'})
                    or not trusted_url(source,{'commons.wikimedia.org'})):
                continue
            yield {'url': url, 'source': source, 'credit': (field('Artist') or 'Wikimedia Commons')[:180],
                   'license': license_name, 'provider':'Wikimedia Commons'}


def pick_image(data):
    return next(commons_images(data),None)


def museum_images(data):
    base=data.get('config',{}).get('iiif_url','https://www.artic.edu/iiif/2').rstrip('/')
    if not trusted_url(base,{'www.artic.edu','artic.edu'}):return
    for item in data.get('data',[]):
        image_id=item.get('image_id') or ''
        description=plain(item.get('title'))+' '+plain(item.get('artist_display'))
        if (item.get('is_public_domain') is not True or not re.fullmatch(r'[a-fA-F0-9-]+',image_id)
                or type(item.get('id')) is not int or any(w in description.lower() for w in AI_WORDS)):
            continue
        yield dict(url=f'{base}/{image_id}/full/843,/0/default.jpg',source=f"https://www.artic.edu/artworks/{item['id']}",
                   credit=plain(item.get('artist_display'))[:180] or 'Art Institute of Chicago',
                   license='Public domain',provider='Art Institute of Chicago')


def validate_image(data):
    from PIL import Image, UnidentifiedImageError
    if not data or len(data)>MAX_BYTES:raise ValueError('Image must be at most 6 MB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            extension={'JPEG':'jpg','PNG':'png','WEBP':'webp'}.get(image.format)
            if not extension or image.width*image.height>12_000_000:raise ValueError('Unsupported image')
            image.verify()
            return extension
    except (OSError,UnidentifiedImageError,Image.DecompressionBombError) as exc:
        raise ValueError('Invalid raster image') from exc


async def from_upload(file):
    if file.size>MAX_BYTES:raise ValueError('Image must be at most 6 MB')
    data=await file.read()
    return dict(data=data,extension=validate_image(data),url='',source='',credit='GM',license='',provider='GM')


async def _download(session,url):
    for _ in range(4):
        if not trusted_url(url,{'upload.wikimedia.org','www.artic.edu','artic.edu'}):raise ValueError('Untrusted image host')
        async with session.get(url,allow_redirects=False) as response:
            if response.status in (301,302,303,307,308):
                url=urljoin(url,response.headers.get('Location',''));continue
            response.raise_for_status()
            if response.status!=200:raise ValueError('No image response')
            if response.content_length and response.content_length>MAX_BYTES:raise ValueError('Image too large')
            data=bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data)>MAX_BYTES:raise ValueError('Image too large')
            data=bytes(data)
            return data,validate_image(data)
    raise ValueError('Too many image redirects')


async def _search(session,provider,query):
    if provider=='commons':
        url='https://commons.wikimedia.org/w/api.php'
        params={'action':'query','format':'json','generator':'search','gsrnamespace':'6',
                'gsrsearch':query+' -"AI-generated"','gsrlimit':'30','prop':'imageinfo',
                'iiprop':'url|mime|thumbmime|extmetadata','iiurlwidth':'960'}
    else:
        url='https://api.artic.edu/api/v1/artworks/search'
        params={'q':query,'query[term][is_public_domain]':'true','limit':'12',
                'fields':'id,title,artist_display,image_id,is_public_domain'}
    async with session.get(url,params=params,allow_redirects=False) as response:
        response.raise_for_status()
        if response.status!=200:raise ValueError('No search response')
        data=await response.json()
    if not isinstance(data,dict) or data.get('error'):raise ValueError('Search API error')
    return list(commons_images(data) if provider=='commons' else museum_images(data))


async def find_event_image(text, query=''):
    topic=search_topic(query or text)
    broad={'historical flood':'flood','historic city fire':'fire','harvest painting':'harvest',
           'popular revolt painting':'revolt','historical plague':'plague','market painting':'market',
           'sailing ships painting':'sailing ships','battle painting':'battle',
           'exploration expedition painting':'exploration','peace treaty painting':'peace',
           'historic town painting':'town'}[topic]
    # Only general topics or the GM's chosen keywords leave the bot, not private prose.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),headers={'User-Agent':USER_AGENT}) as session:
        for provider in ('commons','museum'):
            queries=[query.strip()[:120] or (topic if provider=='commons' else broad)]
            if not query.strip() or topic!='historic town painting':queries.append(broad)
            queries=list(dict.fromkeys(queries))
            try:
                async with asyncio.timeout(PROVIDER_TIMEOUT):
                    for keywords in queries:
                        candidates=await _search(session,provider,keywords)
                        for candidate in candidates[:3]:
                            try:
                                data,extension=await _download(session,candidate['url'])
                                return {**candidate,'data':data,'extension':extension}
                            except (aiohttp.ClientError,TimeoutError,ValueError):
                                log.warning('Event illustration: %s returned an unreadable image',provider)
            except (aiohttp.ClientError,TimeoutError,ValueError,TypeError,KeyError,AttributeError) as exc:
                log.warning('Event illustration: %s unavailable (%s)',provider,type(exc).__name__)
    log.warning('Event illustration: no downloadable image from either provider')
    return None
