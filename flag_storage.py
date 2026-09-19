"""Validated raster uploads persist in the same database as the game."""
import base64
import hashlib
import io

import db
from flags import clean_source, flag_url, public_base, ASSET, CUSTOM_EMOJI
from world_service import world_lock, tr

MAX_BYTES = 2 * 1024 * 1024


def image_png(data):
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data or len(data) > MAX_BYTES:
        raise ValueError(tr('Plik flagi: maksymalnie 2 MB.', 'Flag file: at most 2 MB.'))
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG', 'WEBP', 'GIF') or image.width * image.height > 4_000_000:
                raise ValueError
            image.seek(0)
            normalized = ImageOps.exif_transpose(image).convert('RGBA')
            if normalized.getchannel('A').getextrema()[1] == 0: raise ValueError
            normalized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            normalized.save(output, format='PNG')
            return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(tr('Wgraj PNG, JPG, WebP lub GIF do 4 mln pikseli. Plik musi zawierać widoczny obraz; SVG nie jest obsługiwany.',
                            'Upload a PNG, JPG, WebP or GIF up to 4 million pixels with visible content. SVG is not supported.')) from exc


def can_edit(c, nid, uid, gm=False, expected_owner=None):
    c.execute('SELECT * FROM nations WHERE id=?', (nid,))
    nation = c.fetchone()
    if not nation: raise ValueError(tr('Nie znaleziono państwa.', 'Nation not found.'))
    if not gm and nation['owner_id'] != str(uid):
        raise ValueError(tr('Możesz zmieniać tylko własną flagę.', 'You can only change your own flag.'))
    if expected_owner is not None and nation['owner_id'] != expected_owner:
        raise ValueError(tr('Państwo zmieniło właściciela. Powtórz komendę.', 'Nation ownership changed. Run the command again.'))
    return nation


def save_flag(nid, uid, *, gm=False, source=None, data=None, expected_owner=None):
    if (source is None) == (data is None):
        raise ValueError(tr('Podaj plik albo flagę, nie oba naraz.', 'Provide a file or a flag, not both.'))
    if data is not None:
        if not public_base():
            raise ValueError(tr('Brak publicznego adresu bota. Ustaw FLAG_PUBLIC_BASE_URL lub uruchom bota jako Web Service na Renderze.',
                                'The bot has no public URL. Set FLAG_PUBLIC_BASE_URL or run it as a Render Web Service.'))
        data = image_png(data)
        digest = hashlib.sha256(data).hexdigest()
        value = 'flag:' + digest
    else:
        value = clean_source(source)
        if not value or len(value) > 2048 or ASSET.fullmatch(value):
            raise ValueError(tr('Podaj emoji albo link do obrazka.', 'Provide an emoji or an image link.'))
        regional = len(value) == 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in value)
        if not (CUSTOM_EMOJI.fullmatch(value) or regional or value in ('🏳️', '🏴', '🏁', '🚩', '🏳️‍🌈', '🏳️‍⚧️')):
            url = flag_url(value)
            if not url: raise ValueError(tr('Podaj emoji albo link do obrazka.', 'Provide an emoji or an image link.'))
            from urllib.parse import urlsplit
            if urlsplit(url).path.lower().endswith('.svg'):
                raise ValueError(tr('Wgraj wersję PNG tego SVG.', 'Upload a PNG version of this SVG.'))
            value = url
    with db.atomic() as c:
        world_lock(c)
        can_edit(c, nid, uid, gm, expected_owner)
        if data is not None:
            c.execute('INSERT INTO flag_assets(digest,png_base64) VALUES(?,?) ON CONFLICT(digest) DO NOTHING',
                      (digest, base64.b64encode(data).decode('ascii')))
        c.execute('UPDATE nations SET flag=? WHERE id=?', (value, nid))
    return value


async def change_flag(interaction, nation='', flag=None, file=None):
    import asyncio
    import discord
    from utils import gm_only
    from flags import flagged_embed, flag_text
    gm = gm_only(interaction)
    with db.cursor() as c:
        if nation: c.execute('SELECT * FROM nations WHERE LOWER(name)=LOWER(?)', (nation,))
        else: c.execute('SELECT * FROM nations WHERE owner_id=?', (str(interaction.user.id),))
        row = c.fetchone()
    try:
        if not row: raise ValueError(tr('Nie znaleziono państwa.', 'Nation not found.'))
        with db.cursor() as c: can_edit(c, row['id'], interaction.user.id, gm)
        if (flag is None) == (file is None):
            raise ValueError(tr('Podaj plik albo emoji/link flagi.', 'Provide a file or a flag emoji/link.'))
        if file is not None and file.size > MAX_BYTES:
            raise ValueError(tr('Plik flagi: maksymalnie 2 MB.', 'Flag file: at most 2 MB.'))
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        data = await asyncio.wait_for(file.read(), timeout=15) if file is not None else None
        value = await asyncio.to_thread(save_flag, row['id'], interaction.user.id, gm=gm,
                                        source=flag, data=data, expected_owner=row['owner_id'])
    except (ValueError, discord.HTTPException, asyncio.TimeoutError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else tr('Nie udało się pobrać pliku. Wgraj go ponownie.', 'Could not read the upload. Attach it again.')
        await interaction.followup.send(message, ephemeral=True); return
    embed = flagged_embed(discord.Embed(title=tr('Flaga zapisana', 'Flag saved'),
        description=(flag_text(value) + ' ' + row['name']).strip()), (value, row['name']))
    if file is None and flag_url(value):
        embed.set_footer(text=tr('Pusty podgląd? Użyj /nation flag z plikiem PNG lub JPG. Zewnętrzne linki mogą wygasać.',
                                 'Blank preview? Use /nation flag with a PNG or JPG file. External links can expire.'))
    await interaction.followup.send(embed=embed, ephemeral=True)
