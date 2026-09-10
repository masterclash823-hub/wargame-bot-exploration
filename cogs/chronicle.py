"""GM-configured daily public reports; no delivery until a channel is configured."""
import json
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands,tasks
import db
import i18n
from utils import gm_only
from world_service import tr,seed_world
import chronicle_service as news


class RetryView(i18n.LocalizedView):
    def __init__(self,bot,owner,d):
        super().__init__(timeout=120);self.bot,self.owner,self.delivery=bot,owner,d
        self.resend.label=tr('Sprawdzono kanał — ponów','Channel checked — retry')

    @discord.ui.button(label='Retry',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def resend(self,interaction,button):
        if interaction.user.id!=self.owner or not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        if not await news.recover(self.bot,self.delivery):
            try:news.retry(self.delivery['guild_id'],self.delivery['slot'])
            except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True);return
            await news.deliver(self.bot,self.delivery)
        self.stop()
        await interaction.followup.send(tr('Sprawdź stan przez /chronicle status.','Check /chronicle status for the result.'),ephemeral=True)


class ChronicleCog(commands.Cog):
    chronicle=app_commands.Group(name='chronicle',description='Daily public reports / Codzienny raport publiczny')

    def __init__(self,bot):
        self.bot=bot;seed_world();self.daily.start()

    def cog_unload(self):self.daily.cancel()

    @tasks.loop(minutes=1)
    async def daily(self):
        try:await news.run_reports(self.bot)
        except Exception as exc:print(f'[CHRONICLE] {type(exc).__name__}: {exc}',flush=True)

    @daily.before_loop
    async def before_daily(self):await self.bot.wait_until_ready()

    @chronicle.command(name='configure',description='[GM] Set report channel and time / Ustaw kanał i porę raportu')
    @i18n.localized
    async def configure(self,interaction:discord.Interaction,channel:discord.TextChannel,
                        hour_utc:app_commands.Range[int,0,23]=18,language:Literal['pl','en']='pl'):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        permissions=channel.permissions_for(interaction.guild.me)
        if channel.guild.id!=interaction.guild.id or not all((permissions.view_channel,permissions.send_messages,permissions.embed_links,permissions.read_message_history)):
            await interaction.response.send_message(tr('Bot potrzebuje dostępu do kanału, wysyłania wiadomości, osadzeń i odczytu historii.',
                                                       'The bot needs view, send, embed and message history permissions.'),ephemeral=True);return
        due=news.configure(interaction.guild.id,channel.id,hour_utc,language)
        await interaction.response.send_message(tr('Raport w kanale ','Report channel ')+channel.mention+
                                                tr(' · codziennie o ',' · daily at ')+f'{hour_utc:02}:00 UTC\n'+
                                                tr('Pierwszy raport: ','First report: ')+f'<t:{int(due.timestamp())}:F>',ephemeral=True)

    @chronicle.command(name='pause',description='[GM] Pause daily reports / Wstrzymaj raporty')
    @i18n.localized
    async def pause(self,interaction:discord.Interaction):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        with db.cursor() as c:c.execute('UPDATE news_settings SET enabled=0 WHERE guild_id=?',(str(interaction.guild.id),))
        await interaction.response.send_message(tr('Raporty wstrzymane.','Reports paused.'),ephemeral=True)

    @chronicle.command(name='preview',description='[GM] Preview daily report / Podgląd raportu')
    @i18n.localized
    async def preview(self,interaction:discord.Interaction):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        now=news.utcnow()
        with db.cursor() as c:rows=news.top_actions(c,now)
        await interaction.response.send_message(embed=news.render({'language':i18n.current_language(),'slot':now.isoformat(),'report_json':json.dumps(rows)}),ephemeral=True)

    @chronicle.command(name='status',description='[GM] Report delivery status / Stan wysyłki raportów')
    @i18n.localized
    async def status(self,interaction:discord.Interaction):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        with db.cursor() as c:
            c.execute('SELECT * FROM news_settings WHERE guild_id=?',(str(interaction.guild.id),));s=c.fetchone()
            c.execute('SELECT * FROM news_deliveries WHERE guild_id=? ORDER BY slot DESC LIMIT 5',(str(interaction.guild.id),));rows=c.fetchall()
        if not s:await interaction.response.send_message(tr('Najpierw użyj /chronicle configure.','Use /chronicle configure first.'),ephemeral=True);return
        text=f"<#{s['channel_id']}> · {s['hour_utc']:02}:00 UTC · "+(tr('włączone','enabled') if s['enabled'] else tr('wyłączone','paused'))
        for r in rows:text+='\n'+r['slot'][:16]+' · '+i18n.term(r['status'])
        await interaction.response.send_message(text,ephemeral=True)

    @chronicle.command(name='retry',description='[GM] Review a failed report / Sprawdź nieudaną wysyłkę')
    @i18n.localized
    async def retry(self,interaction:discord.Interaction):
        if not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        with db.cursor() as c:
            c.execute("SELECT * FROM news_deliveries WHERE guild_id=? AND status IN ('failed','uncertain') ORDER BY slot DESC LIMIT 1",(str(interaction.guild.id),));d=c.fetchone()
        if not d:await interaction.response.send_message(tr('Brak raportu wymagającego ponowienia.','No report needs a retry.'),ephemeral=True);return
        await interaction.response.send_message(tr('Sprawdź, czy ten raport już dotarł na kanał. Ponowienie niepewnej wysyłki może utworzyć duplikat.',
                                                   'Check whether this report already reached the channel. Retrying an uncertain delivery may duplicate it.'),
            embed=news.render(d),view=RetryView(self.bot,interaction.user.id,d),ephemeral=True)


async def setup(bot):await bot.add_cog(ChronicleCog(bot))
