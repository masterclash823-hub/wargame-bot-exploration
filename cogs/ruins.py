"""Nearby ruin discovery requests, using the normal GM event publishing flow."""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
import nation_decay as decay
from utils import get_nation_by_owner
from world_service import tr
from cogs.companies import choose


class RuinsCog(commands.Cog):
    ruins=app_commands.Group(name='ruins',description='Discover fallen neighboring nations / Odkrywaj ruiny sąsiadów')

    @ruins.command(name='list',description='Nearby ruins and event requests / Pobliskie ruiny i propozycje eventów')
    @i18n.localized
    async def ruins_list(self,interaction:discord.Interaction):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:
            await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        with db.cursor() as c:rows=decay.nearby(c,n['id'])
        if not rows:
            await interaction.response.send_message(tr('Nie graniczysz z ruinami żadnego państwa. Sąsiedztwo wynika z połączeń pól zaimportowanej mapy.',
                                                        'You do not border any nation ruins. Neighbors come from the imported map cell connections.'),ephemeral=True);return
        async def selected(interaction,value):
            await interaction.response.defer(ephemeral=True)
            try:event=await asyncio.to_thread(decay.request_event,n['id'],interaction.user.id,int(value))
            except ValueError as exc:
                await interaction.followup.send(str(exc),ephemeral=True);return
            await interaction.followup.send(tr('Szkic odkrycia czeka na GM: event #','Discovery draft awaiting GM: event #')+str(event)+tr('. GM ustali skutki i opublikuje go przez /event post. Ponowne kliknięcie otwiera to samo zgłoszenie.','. The GM chooses consequences and publishes it with /event post. Repeating this action returns the same request.'),ephemeral=True)
        await choose(interaction,tr('Wybierz ruiny i zgłoś odkrycie jako event. Nie przyznaje to automatycznie zasobów ani prowincji.',
                                    'Choose ruins to request a discovery event. This does not automatically grant resources or provinces.'),
                     [discord.SelectOption(label=r['name'][:100],value=str(r['id']),description=f"ID: {r['id']}") for r in rows],selected)


async def setup(bot):await bot.add_cog(RuinsCog())
