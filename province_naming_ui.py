"""Free province names through the player's private territory panel."""
import asyncio

import discord

import province_admin as provinces
from cogs.panel import FieldsModal
from technology_ui import deliver
from workflow_ui import choose
from world_service import tr


async def save(i,cell,name,*,nation_id=None,expected_name=None):
    await i.response.defer(ephemeral=True)
    try:
        result=await asyncio.to_thread(provinces.rename,cell,i.user.id,name,
                                       nation_id=nation_id,expected_name=expected_name)
    except ValueError as exc:
        await deliver(i,content=str(exc));return
    name=discord.utils.escape_mentions(discord.utils.escape_markdown(result['name']))
    await deliver(i,content=tr(f'Prowincja #{cell}: **{name}**. Koszt: 0 złota.',
                              f'Province #{cell}: **{name}**. Cost: 0 gold.'))


async def show(i):
    await i.response.defer(ephemeral=True)
    try:nation,rows=await asyncio.to_thread(provinces.naming_options,i.user.id)
    except ValueError as exc:
        await deliver(i,content=str(exc));return
    uid=i.user.id
    async def selected(j,value):
        p=next((p for p in rows if str(p['azgaar_cell_id'])==value),None)
        if j.user.id!=uid or p is None:
            await deliver(j,content=tr('To nie jest Twój wybór prowincji.','This is not your province selection.'));return
        async def submit(k,name):
            if k.user.id!=uid:
                await deliver(k,content=tr('To nie jest Twój formularz.','This is not your form.'));return
            await save(k,p['azgaar_cell_id'],name,nation_id=nation['id'],expected_name=p['name'] or '')
        await j.response.send_modal(FieldsModal(
            tr('Nazwij prowincję #','Name province #')+value,
            [dict(label=tr('Nazwa miasta — za darmo','City name — free'),
                  placeholder=tr('np. Nowy Kraków','e.g. New Harbor'),default=(p['name'] or '')[:80],max_length=80)],submit))
    options=[discord.SelectOption(label=f"#{p['azgaar_cell_id']} · {p['name'] or tr('Bez nazwy','Unnamed')}"[:100],
                                  value=str(p['azgaar_cell_id'])) for p in rows]
    await choose(i,options,selected,content=tr(
        'Wybierz własną prowincję i nadaj jej nazwę miasta. Koszt: 0 złota.',
        'Choose your own province and give it a city name. Cost: 0 gold.'))
