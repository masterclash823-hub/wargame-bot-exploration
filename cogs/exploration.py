"""Free-form adventures with gradual conclusions and persistent public delivery."""
import json
import discord
from discord import app_commands
from discord.ext import commands,tasks
import db
import i18n
import exploration_service as service
from exploration_publication import check_channel,publish
from utils import get_nation_by_owner
from world_service import tr
from flags import flagged_embed


def render(row):
    s=json.loads(row['state_json'])
    e=discord.Embed(title=tr('Wyprawa','Expedition')+f" #{row['id']} · {row['name'][:130]}",description=s['text'])
    flagged_embed(e,(row['flag'],row['name']))
    if s['finished']:
        e.add_field(name=tr('Wynik','Outcome'),value=tr('Sukces','Success') if s['success'] else tr('Niepowodzenie','Failure'))
        e.add_field(name=tr('Ogłoszenie','Announcement'),value=f"<#{row['channel_id']}> · "+(tr('opublikowane','published') if row['publication_status']=='sent' else tr('oczekuje na publikację; bot ponawia automatycznie','pending publication; the bot retries automatically')))
    else:
        count=len(s['history'])
        phase={
            'journey':tr('W drodze','On the journey'),
            'closing':tr('Droga do finału','Approaching the conclusion'),
            'finale':tr('Domykanie wątku','Closing the story'),
        }[service.narrative_phase(count)]
        e.set_footer(text=tr('Odpowiedzi: ','Replies: ')+f"{count} · {phase} · /exploration expedition_id:{row['id']}")
    return e


async def display(i,row):
    await i.followup.send(embed=render(row),view=ExpeditionView(i.client,row),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


async def after_answer(i,row):
    if row['status']=='resolved':
        try:await publish(i.client,row['id'])
        except Exception as exc:print(f'[EXPLORATION DELIVERY] {type(exc).__name__}',flush=True)
        row=service.load(row['id'],i.user.id,i.guild_id)
    await display(i,row)


class AnswerModal(discord.ui.Modal):
    def __init__(self,row):
        super().__init__(title=tr('Twoja odpowiedź','Your reply'))
        self.row=row
        self.text=discord.ui.TextInput(label=tr('Co robi wyprawa?','What does the expedition do?'),style=discord.TextStyle.paragraph,max_length=4000)
        self.add_item(self.text)

    @i18n.localized
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        try:row=await service.answer(self.row['id'],self.row['version'],i.user.id,i.guild_id,self.text.value)
        except ValueError as exc:await i.followup.send(str(exc),ephemeral=True);return
        await after_answer(i,row)


class ExpeditionView(i18n.LocalizedView):
    def __init__(self,bot,row):
        super().__init__(timeout=600);self.bot,self.row=bot,row
        self.answer.label=tr('Odpowiedz własnymi słowami','Reply in your own words')
        if row['status']!='active':self.remove_item(self.answer)

    @discord.ui.button(label='Reply',style=discord.ButtonStyle.primary)
    @i18n.localized
    async def answer(self,i,button):
        try:row=service.load(self.row['id'],i.user.id,i.guild_id)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        if row['version']!=self.row['version'] or row['status']!='active':
            await i.response.send_message(tr('Wznów aktualną turę przez /exploration.','Resume the current turn through /exploration.'),ephemeral=True);return
        await i.response.send_modal(AnswerModal(row))


class PreparationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=tr('Przygotowania wyprawy','Expedition preparations'))
        self.text=discord.ui.TextInput(label=tr('Cel, ludzie, zapasy, trasa i zabezpieczenia','Objective, supplies, route and precautions'),style=discord.TextStyle.paragraph,min_length=20,max_length=4000)
        self.add_item(self.text)

    @i18n.localized
    async def on_submit(self,i):await begin(i,self.text.value)


async def begin(i,preparations):
    await i.response.defer(ephemeral=True)
    n=get_nation_by_owner(str(i.user.id))
    if not n:await i.followup.send(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
    try:
        role=check_channel(i.channel,i.guild)
        row=await service.start(n['id'],i.user.id,i.guild_id,i.channel_id,role.id,preparations)
    except ValueError as exc:await i.followup.send(str(exc),ephemeral=True);return
    await display(i,row)


class ExplorationCog(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.deliveries.start()
    def cog_unload(self):self.deliveries.cancel()

    @tasks.loop(minutes=1)
    async def deliveries(self):
        with db.cursor() as c:
            c.execute("SELECT id FROM explorations WHERE status='resolved' AND publication_status IN ('pending','sending') ORDER BY id LIMIT 50");rows=c.fetchall()
        for r in rows:
            try:await publish(self.bot,r['id'])
            except Exception as exc:print(f'[EXPLORATION DELIVERY] #{r["id"]}: {type(exc).__name__}',flush=True)

    @deliveries.before_loop
    async def ready(self):await self.bot.wait_until_ready()

    @app_commands.command(name='exploration',description='Start or resume a free-text expedition / Rozpocznij lub wznów wyprawę')
    @app_commands.guild_only()
    @i18n.localized
    async def exploration(self,i:discord.Interaction,preparations:str='',expedition_id:int=0):
        n=get_nation_by_owner(str(i.user.id))
        if not n:await i.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        if preparations and expedition_id:
            await i.response.send_message(tr('Wybierz przygotowania nowej wyprawy albo ID istniejącej.','Provide new preparations or an existing expedition ID.'),ephemeral=True);return
        if not preparations and not expedition_id:
            with db.cursor() as c:
                c.execute("SELECT id FROM explorations WHERE nation_id=? AND guild_id=? AND status='active'",(n['id'],str(i.guild_id)));active=c.fetchone()
            if active:expedition_id=active['id']
        if expedition_id:
            await i.response.defer(ephemeral=True)
            try:row=service.load(expedition_id,i.user.id,i.guild_id)
            except ValueError as exc:await i.followup.send(str(exc),ephemeral=True);return
            await after_answer(i,row)
        elif preparations:await begin(i,preparations)
        else:
            try:check_channel(i.channel,i.guild)
            except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
            await i.response.send_modal(PreparationModal())


async def setup(bot):await bot.add_cog(ExplorationCog(bot))
