"""Private border selector with a read-only preview and explicit purchase confirmation."""
import asyncio
import json
import logging

import discord
import i18n
import province_admin as provinces
from world_service import tr

PAGE_SIZE = 25
log = logging.getLogger(__name__)


def _json(raw,default):
    try:
        value=json.loads(raw)
        return value if isinstance(value,type(default)) else default
    except (TypeError,ValueError):
        return default


def _safe(text,limit=200):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))[:limit]


def _identity_label(province,kind):
    entity_id=province[kind+'_id']
    if entity_id is None:return tr('Brak danych','No data')
    if entity_id==0:return tr('Brak przypisania','Unassigned')
    return _json(province[kind+'_json'],{}).get('name') or f'ID {entity_id}'


def preview_embed(nation,rows,selected,page):
    embed=discord.Embed(title=tr('Zakup prowincji — ','Buy a province — ')+_safe(nation['name'],120),
                        color=discord.Color.gold())
    embed.add_field(name=tr('Skarbiec','Treasury'),value=f"{nation['treasury']:.2f} 🪙")
    if selected is None:
        embed.description=tr('Wybierz sąsiednią prowincję z listy, aby zobaczyć szczegóły i cenę. Zakup wymaga potwierdzenia.',
                             'Choose a neighboring province to see its details and price. Buying requires confirmation.')
        if not rows:
            embed.description=tr('Brak wolnych prowincji lądowych sąsiadujących z twoim państwem. '
                                 'Jeśli mapa nie ma danych sąsiedztwa, poproś GM o ponowny import mapy.',
                                 'No unclaimed land provinces border your nation. '
                                 'If map adjacency data is missing, ask a GM to reimport the map.')
    else:
        p=selected
        embed.description=f"**#{p['azgaar_cell_id']} · {_safe(p['name'] or tr('Prowincja','Province'))}**"
        for label,value in (
            (tr('Teren','Terrain'),i18n.term(p['terrain'])),
            (tr('Biom','Biome'),i18n.term(p['biome'])),
            (tr('Ludność','Population'),str(p['population'])),
            (tr('Kultura','Culture'),_identity_label(p,'culture')),
            (tr('Religia','Religion'),_identity_label(p,'religion')),
            (tr('Fortyfikacje','Fortification'),str(p['fortification_level'])),
        ):
            embed.add_field(name=label,value=_safe(value))
        resources=_json(p['base_resources_json'],{})
        embed.add_field(name=tr('Zasoby bazowe','Base resources'),
                        value=(', '.join(f'{i18n.term(k)}: {v}' for k,v in resources.items()) or '—')[:1000],inline=False)
        buildings=_json(p['buildings_json'],[])
        embed.add_field(name=tr('Budynki','Buildings'),value=(', '.join(i18n.term(str(b)) for b in buildings) or '—')[:1000],inline=False)
        price=[tr('Cena bazowa: 500 złota','Base price: 500 gold'),
               tr('Kultura obecna w państwie: ','Culture present in your nation: ')+('-100' if p['culture'] else '0'),
               tr('Religia obecna w państwie: ','Religion present in your nation: ')+('-100' if p['religion'] else '0'),
               tr('Do zapłaty: ','Total: ')+f"**{p['cost']} 🪙**"]
        if nation['treasury']<p['cost']:
            price.append(tr('Brakuje złota: ','Missing gold: ')+f"{p['cost']-nation['treasury']:.2f}")
        embed.add_field(name=tr('Cena zakupu','Purchase price'),value='\n'.join(price),inline=False)
    embed.set_footer(text=tr('Strona','Page')+f" {page+1}/{max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)} · "+
                          tr('Dostępne prowincje: ','Available provinces: ')+str(len(rows)))
    return embed


class ProvincePurchaseView(discord.ui.View):
    def __init__(self,uid,nation,rows,cell=None,page=0):
        super().__init__(timeout=600)
        self.uid,self.nid,self.lang=uid,nation['id'],i18n.current_language()
        self.selected=next((p for p in rows if p['azgaar_cell_id']==cell),None)
        if cell is not None and self.selected is None:
            raise ValueError(tr('Ta prowincja nie jest dostępna do zakupu. Otwórz /province buy, aby zobaczyć aktualną listę.',
                                'This province is unavailable to buy. Open /province buy for the current list.'))
        if self.selected is not None:page=rows.index(self.selected)//PAGE_SIZE
        self.page=min(max(0,page),max(0,(len(rows)-1)//PAGE_SIZE))
        self.busy=False
        self.embed=preview_embed(nation,rows,self.selected,self.page)
        visible=rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if visible:
            select=discord.ui.Select(placeholder=tr('Wybierz prowincję do podglądu','Choose a province to preview'),row=0,
                options=[discord.SelectOption(label=f"#{p['azgaar_cell_id']} · {p['name'] or tr('Prowincja','Province')}"[:100],
                    description=f"{p['cost']} 🪙 · {i18n.term(p['terrain'])} · {tr('Ludność','Population')}: {p['population']}"[:100],
                    value=str(p['azgaar_cell_id']),default=p['azgaar_cell_id']==cell) for p in visible])
            async def selected(i):
                await self.browse(i,cell=int(select.values[0]))
            select.callback=selected
            self.add_item(select)
        self.confirm_button=discord.ui.Button(label=tr('Potwierdź zakup','Confirm purchase'),style=discord.ButtonStyle.success,row=1,
                                              disabled=self.selected is None or nation['treasury']<self.selected['cost'])
        self.confirm_button.callback=self.confirm
        self.add_item(self.confirm_button)
        for label,page_number in ((tr('Poprzednia','Previous'),self.page-1),(tr('Następna','Next'),self.page+1)):
            button=discord.ui.Button(label=label,row=1,disabled=page_number<0 or page_number*PAGE_SIZE>=len(rows))
            async def clicked(i,page_number=page_number):await self.browse(i,page=page_number)
            button.callback=clicked;self.add_item(button)
        refresh=discord.ui.Button(label=tr('Odśwież','Refresh'),row=1)
        async def refreshed(i):await self.browse(i,page=self.page,cell=self.selected['azgaar_cell_id'] if self.selected else None)
        refresh.callback=refreshed;self.add_item(refresh)
        cancel=discord.ui.Button(label=tr('Anuluj','Cancel'),row=1)
        cancel.callback=self.cancel;self.add_item(cancel)

    async def interaction_check(self,interaction):
        if interaction.user.id==self.uid:return True
        with i18n.using_language(self.lang):
            await interaction.response.send_message(tr('To nie jest twój panel zakupu.','This is not your purchase panel.'),ephemeral=True)
        return False

    async def _ack(self,interaction):
        if not await self.interaction_check(interaction):return False
        # Database locks and preview queries happen only after acknowledging Discord.
        await interaction.response.defer()
        if self.is_finished():
            with i18n.using_language(self.lang):
                await interaction.followup.send(tr('Ten panel jest już zamknięty. Otwórz /province buy.',
                                                    'This panel is closed. Open /province buy.'),ephemeral=True)
            return False
        if self.busy:
            with i18n.using_language(self.lang):
                await interaction.followup.send(tr('Zakup jest już przetwarzany. Poczekaj na wynik.',
                                                    'The purchase is already processing. Wait for the result.'),ephemeral=True)
            return False
        return True

    async def browse(self,interaction,*,page=0,cell=None):
        if not await self._ack(interaction):return
        with i18n.using_language(self.lang):
            try:
                nation,rows=await asyncio.to_thread(provinces.purchase_options,self.uid,self.nid)
                view=ProvincePurchaseView(self.uid,nation,rows,cell,page)
                await interaction.edit_original_response(content=None,embed=view.embed,view=view)
                self.stop()
            except ValueError as exc:
                await interaction.followup.send(str(exc),ephemeral=True)

    async def confirm(self,interaction):
        if not await self._ack(interaction):return
        if self.selected is None:return
        self.busy=True
        with i18n.using_language(self.lang):
            try:
                result=await asyncio.to_thread(provinces.buy,self.selected['azgaar_cell_id'],self.uid,
                                              nation_id=self.nid,expected_cost=self.selected['cost'])
            except ValueError as exc:
                self.busy=False
                await interaction.followup.send(str(exc),ephemeral=True)
                return
            except Exception:
                self.busy=False
                log.exception('Province purchase failed for cell %s',self.selected['azgaar_cell_id'])
                await interaction.followup.send(tr('Nie udało się potwierdzić zakupu. Sprawdź własność prowincji przez /province info.',
                                                    'Could not confirm the purchase. Check ownership with /province info.'),ephemeral=True)
                return
            self.stop()
            await interaction.edit_original_response(content=tr('Kupiono prowincję','Purchased province')+
                                                     f" #{result['cell']} · {result['cost']} 🪙",embed=None,view=None)

    async def cancel(self,interaction):
        if not await self._ack(interaction):return
        self.stop()
        with i18n.using_language(self.lang):
            await interaction.edit_original_response(content=tr('Anulowano zakup.','Purchase cancelled.'),embed=None,view=None)


async def show(interaction,cell=None):
    await interaction.response.defer(ephemeral=True)
    try:
        nation,rows=await asyncio.to_thread(provinces.purchase_options,interaction.user.id)
        view=ProvincePurchaseView(interaction.user.id,nation,rows,cell)
        await interaction.followup.send(embed=view.embed,view=view,ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
