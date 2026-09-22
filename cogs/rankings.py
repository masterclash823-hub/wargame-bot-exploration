"""GM-only ranking previews and explicit channel publication."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import i18n
import rankings
from utils import gm_only
from world_service import tr

log=logging.getLogger(__name__)


class RankingsCog(commands.Cog):
    @app_commands.command(name='ranking',description='[GM] Create a nation ranking / Utwórz ranking państw')
    @app_commands.guild_only()
    @app_commands.describe(category='Ranking category / Kategoria rankingu',
                           limit='Visible places: 1–25; full list in TXT / Liczba widocznych wpisów',
                           channel='Publish here; omit for private preview / Kanał publikacji lub prywatny podgląd')
    @app_commands.choices(category=[app_commands.Choice(name=label[0],value=key) for key,label in rankings.CATEGORIES.items()])
    @i18n.localized
    async def ranking(self,interaction:discord.Interaction,category:str='prestige',
                      limit:app_commands.Range[int,1,25]=10,channel:discord.TextChannel=None):
        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        if category not in rankings.CATEGORIES or not 1<=limit<=25:
            await interaction.response.send_message(tr('Wybierz kategorię i limit od 1 do 25.',
                                                       'Choose a category and a limit from 1 to 25.'),ephemeral=True);return
        if channel is not None:
            if channel.guild.id!=interaction.guild.id:
                await interaction.response.send_message(tr('Wybierz kanał tego serwera.',
                                                           'Choose a channel on this server.'),ephemeral=True);return
            p=channel.permissions_for(interaction.guild.me)
            if not all((p.view_channel,p.send_messages,p.embed_links,p.attach_files)):
                await interaction.response.send_message(tr('Bot potrzebuje dostępu do kanału, wysyłania wiadomości, osadzeń i plików.',
                                                           'The bot needs view, send, embed and attach files permissions.'),ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:result=await asyncio.to_thread(rankings.load,category)
        except Exception:
            log.exception('Could not build %s ranking',category)
            await interaction.followup.send(tr('Nie udało się obliczyć rankingu. Sprawdź logi bota.',
                                               'Could not calculate the ranking. Check the bot logs.'),ephemeral=True);return
        if not result['rows']:
            await interaction.followup.send(tr('Brak państw do rankingu. Państwa upadłe są pomijane.',
                                               'No nations to rank. Fallen nations are excluded.'),ephemeral=True);return
        embed,file=rankings.render(result,limit)
        try:
            message=dict(embed=embed,file=file,allowed_mentions=discord.AllowedMentions.none())
            if channel is None:await interaction.followup.send(**message,ephemeral=True)
            else:
                await channel.send(**message)
                await interaction.followup.send(tr('Ranking opublikowany w ','Ranking published in ')+channel.mention,
                                                ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.followup.send(tr('Discord nie potwierdził wysłania rankingu. Sprawdź kanał przed ponowieniem.',
                                               'Discord did not confirm delivery. Check the channel before retrying.'),ephemeral=True)
        finally:file.close()


async def setup(bot):await bot.add_cog(RankingsCog())
