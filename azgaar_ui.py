"""GM-facing map exchange. Public map files contain geography, never bot balances or plans."""
import asyncio
import gzip
import io
from urllib.parse import urlsplit

import aiohttp
import discord

import azgaar_service as service
from azgaar_format import MAX_BYTES
import i18n
from utils import gm_only
from world_service import tr


async def read_source(file=None, url=None):
    if file:
        if getattr(file, 'size', 0) > MAX_BYTES:
            raise ValueError(tr('Plik przekracza 24 MB.', 'File exceeds 24 MB.'))
        raw = await file.read()
    else:
        if not url or urlsplit(url).scheme not in ('https', 'http'):
            raise ValueError(tr('Dodaj plik lub bezpośredni adres HTTPS.', 'Attach a file or a direct HTTPS URL.'))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
            async with session.get(url, headers={'User-Agent': 'WargameBot/1.0'}) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.content.iter_chunked(64*1024):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ValueError(tr('Plik przekracza 24 MB.', 'File exceeds 24 MB.'))
                raw = bytes(raw)
    if len(raw) > MAX_BYTES:
        raise ValueError(tr('Plik przekracza 24 MB.', 'File exceeds 24 MB.'))
    return raw


async def allowed(i):
    if gm_only(i):
        return True
    await i.response.send_message(tr('Tylko dla GM.', 'GM only.'), ephemeral=True)
    return False


class ImportConfirmation(i18n.LocalizedView):
    def __init__(self, uid, raw, map_raw, resync, sync_owners):
        super().__init__(timeout=300)
        self.uid, self.raw, self.map_raw = uid, raw, map_raw
        self.resync, self.sync_owners = resync, sync_owners
        self.used = False
        button = discord.ui.Button(label=tr('Zastosuj import', 'Apply import'), style=discord.ButtonStyle.danger if sync_owners else discord.ButtonStyle.primary)
        button.callback = i18n.localized(self.apply)
        self.add_item(button)

    async def apply(self, i):
        if i.user.id != self.uid or not gm_only(i):
            await i.response.send_message(tr('Potwierdzić może tylko GM, który rozpoczął import.', 'Only the GM who started the import can confirm it.'), ephemeral=True)
            return
        if self.used:
            await i.response.send_message(tr('Ten import już potwierdzono.', 'This import was already confirmed.'), ephemeral=True)
            return
        self.used = True
        await i.response.defer(ephemeral=True)
        try:
            stats = await asyncio.to_thread(service.import_map, self.raw, self.map_raw, resync=self.resync, sync_owners=self.sync_owners)
        except Exception as exc:
            self.used = False
            await i.followup.send(tr('Import nie został zapisany: ', 'Import was not saved: ') + str(exc)[:1400], ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await i.edit_original_response(content=tr('✅ Import zakończony. ', '✅ Import complete. ') +
            tr('Nowe / istniejące pola: ', 'New / existing cells: ') + f"{stats['inserted']} / {stats['updated']}\n" +
            tr('Państwa / kultury / religie: ', 'States / cultures / religions: ') + f"{stats['states']} / {stats['cultures']} / {stats['religions']}\n" +
            tr('Zmienione przypisania pól: ', 'Changed cell owners: ') + str(stats['owners_changed']) + '\n' +
            tr('Państwa oczekujące na powiązanie: ', 'States waiting to be linked: ') + str(stats['pending']) + '\n\n' +
            tr('Lista ID: /admin map_entities. Utwórz państwo dla gracza przez /nation found, następnie połącz je przez /admin map_bind. Eksport: /admin map_export.',
               'IDs: /admin map_entities. Create a player nation with /nation found, then link it with /admin map_bind. Export: /admin map_export.'), view=self)
        self.raw = self.map_raw = None
        self.stop()


async def import_command(i, file, url, map_file, resync, sync_owners):
    if not await allowed(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        raw = await read_source(file, url)
        map_raw = await read_source(map_file) if map_file else None
        data, native, _ = await asyncio.to_thread(service.prepare, raw, map_raw)
        count = len(data['pack']['cells'])
        message = tr('Mapa gotowa do importu: ', 'Map ready to import: ') + f"{count:,}" + tr(' pól.', ' cells.')
        message += '\n' + tr('Kultury i religie zostaną odczytane z pliku. Budynki, ludność i zasoby istniejących prowincji pozostaną w grze.',
                             'Cultures and religions will be read from the file. Existing province buildings, population and resources stay in the game.')
        message += '\n' + (tr('⚠️ Granice gry zostaną zastąpione granicami z pliku. Pola niepowiązanych państw staną się nieprzypisane.',
                               '⚠️ Game borders will be replaced by file borders. Cells of unlinked states become unclaimed.') if sync_owners else
                            tr('Obecne granice gry zostaną zachowane.', 'Current game borders will be preserved.'))
        if not native:
            message += '\n' + tr('Aby eksportować otwieralny projekt, przy pierwszym /admin map_export dodaj oryginalny plik .map.',
                                  'To export a loadable project, attach the original .map file to the first /admin map_export.')
        await i.followup.send(message, view=ImportConfirmation(i.user.id, raw, map_raw, resync, sync_owners), ephemeral=True)
    except (ValueError, UnicodeError, OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await i.followup.send(tr('Nie można odczytać mapy: ', 'Cannot read map: ') + str(exc)[:1500], ephemeral=True)


async def export_command(i, file=None, format='map'):
    if not await allowed(i):
        return
    await i.response.defer(ephemeral=True)
    try:
        if format not in ('map', 'json'):
            raise ValueError('Invalid format')
        raw = await read_source(file) if file else None
        output = await asyncio.to_thread(service.export_map, native_format=format == 'map', map_raw=raw)
        name = 'wargame.' + format
        limit = getattr(i.guild, 'filesize_limit', 10*1024*1024) if i.guild else 10*1024*1024
        if len(output) > limit:
            output = gzip.compress(output, mtime=0)
            if format == 'json':
                name += '.gz'
        if len(output) > limit:
            raise ValueError(tr('Mapa przekracza limit załączników tego serwera.', 'Map exceeds this server attachment limit.'))
        message = tr('W Azgaarze wybierz Load → Machine i otwórz plik .map.', 'In Azgaar choose Load → Machine and open the .map file.') if format == 'map' else tr(
            'Pełny JSON do wymiany danych. Azgaar otwiera projekty .map — wybierz format map, aby otworzyć mapę w edytorze.',
            'Full JSON for data exchange. Azgaar opens .map projects — choose format map to open it in the editor.')
        await i.followup.send(message, file=discord.File(io.BytesIO(output), filename=name), ephemeral=True)
    except (ValueError, UnicodeError, OSError) as exc:
        await i.followup.send(str(exc)[:1600], ephemeral=True)


async def entities_command(i, kind, page):
    if not await allowed(i):
        return
    rows = service.catalog(kind)
    page = max(1, page)
    rows_page = rows[(page-1)*15:page*15]
    lines = []
    for r in rows_page:
        name = discord.utils.escape_markdown(str(r['entity'].get('fullName') or r['entity'].get('name', '—')))[:140]
        suffix = ''
        if kind == 'states' and r['state_id']:
            suffix = ' → ' + (r['nation'] or (tr('usunięte z gry', 'deleted from game') if r['linked'] else tr('do powiązania', 'unlinked')))
        lines.append(f"**{r['state_id']}** · {name}{suffix}")
    title = {'states': tr('Państwa mapy', 'Map states'), 'cultures': tr('Kultury', 'Cultures'), 'religions': tr('Religie', 'Religions')}[kind]
    e = discord.Embed(title=title, description='\n'.join(lines) or '—')
    e.set_footer(text=f"{page}/{max(1, (len(rows)+14)//15)} · /admin map_bind · /province identity")
    await i.response.send_message(embed=e, ephemeral=True)
