"""Named research projects, monthly science and rare algae applications."""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands,tasks

import db
import i18n
import technology as tech
import technology_ui as ui
from utils import gm_only,get_nation_by_owner


class TechCog(commands.Cog):
    tech_grp=app_commands.Group(name='tech',description='Research and technology')
    algae_grp=app_commands.Group(name='algae',description='Rare algae deposits and applications')

    def __init__(self,bot):
        self.bot=bot
        self.notifications.start()

    def cog_unload(self):
        self.notifications.cancel()

    @tech_grp.command(name='status',description='Open your private research panel')
    @app_commands.describe(nation='Nation name — GM only / Nazwa narodu — tylko GM')
    @i18n.localized
    async def tech_status(self,interaction:discord.Interaction,nation:str=''):
        await interaction.response.defer(ephemeral=True)
        if nation:
            if not gm_only(interaction):
                await ui.deliver(interaction,content=i18n.text('Tech levels are private. You can only view your own.'));return
            with db.cursor() as c:
                c.execute('SELECT * FROM nations WHERE LOWER(name)=LOWER(?)',(nation,));n=c.fetchone()
            if n:await ui.deliver(interaction,embed=ui.overview(n['id']));return
        else:
            n=get_nation_by_owner(str(interaction.user.id))
            if n:await ui.show(interaction,n['id']);return
        await ui.deliver(interaction,content=i18n.t(i18n.current_language(),'nation_not_found' if nation else 'no_nation'))

    @tech_grp.command(name='research',description='Choose a named research project')
    @app_commands.describe(project='Research project; leave empty to browse')
    @i18n.localized
    async def tech_research(self,interaction:discord.Interaction,project:str=''):
        await interaction.response.defer(ephemeral=True)
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:
            await ui.deliver(interaction,content=i18n.t(i18n.current_language(),'no_nation'));return
        if project and project not in tech.PROJECTS:
            await ui.deliver(interaction,content=tech.tr('Wybierz projekt z katalogu lub podpowiedzi.','Choose a project from the catalogue or autocomplete.'));return
        await ui.show(interaction,n['id'],code=project or None)

    @tech_research.autocomplete('project')
    @i18n.localized
    async def project_autocomplete(self,interaction:discord.Interaction,current:str):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:return []
        with db.cursor() as c:choices=tech.available(n,tech.discoveries(c,n['id']))
        return [app_commands.Choice(name=tech.name(k),value=k) for k in choices if current.casefold() in (k+' '+tech.name(k)).casefold()][:25]

    @algae_grp.command(name='locations',description='List every active algae deposit on the map')
    @i18n.localized
    async def algae_locations(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ui.deliver(interaction,embed=ui.locations_embed())

    @algae_grp.command(name='gather',description='Gather trace algae on your deposit at high cost (economy 3)')
    @i18n.localized
    async def algae_gather(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:
            await ui.deliver(interaction,content=i18n.t(i18n.current_language(),'no_nation'));return
        await ui.show_gather(interaction,n['id'])

    async def edit_deposit(self,interaction,cell_id,add):
        if not gm_only(interaction):
            await ui.deliver(interaction,content=i18n.t(i18n.current_language(),'gm_only'));return
        await interaction.response.defer(ephemeral=True)
        try:
            p=await asyncio.to_thread(tech.set_deposit,cell_id,add)
            text=tech.tr('Dodano złoże w prowincji ', 'Added deposit in province ') if add else tech.tr('Usunięto złoże z prowincji ', 'Removed deposit from province ')
            text+=f"#{p['azgaar_cell_id']}. "
            if not add:text+=tech.tr('Farma zostaje, ale wydobycie ustaje. Zapasy graczy pozostają.', 'The farm remains, but extraction stops. Player stockpiles are preserved.')
            await ui.deliver(interaction,content=text,embed=ui.locations_embed())
        except ValueError as exc:await ui.deliver(interaction,content=str(exc))

    @algae_grp.command(name='deposit_add',description='[GM] Add an algae deposit by province cell ID')
    @i18n.localized
    async def deposit_add(self,interaction:discord.Interaction,cell_id:int):
        await self.edit_deposit(interaction,cell_id,True)

    @algae_grp.command(name='deposit_remove',description='[GM] Remove an algae deposit by province cell ID')
    @i18n.localized
    async def deposit_remove(self,interaction:discord.Interaction,cell_id:int):
        await self.edit_deposit(interaction,cell_id,False)

    @algae_grp.command(name='programs',description='Manage algae applications and monthly supplies')
    @i18n.localized
    async def algae_programs(self,interaction:discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:
            await ui.deliver(interaction,content=i18n.t(i18n.current_language(),'no_nation'));return
        await ui.show_programs(interaction,n['id'])

    @tasks.loop(minutes=1)
    async def notifications(self):
        # A claim is recorded before Discord delivery. Never duplicate an uncertain DM.
        with db.cursor() as c:
            c.execute('SELECT r.*,n.owner_id FROM research_discoveries r JOIN nations n ON n.id=r.nation_id WHERE r.notified=0 ORDER BY r.completed_month LIMIT 25')
            rows=c.fetchall()
        for r in rows:
            if r['code'] not in tech.PROJECTS:continue
            with db.atomic() as c:
                c.execute('UPDATE research_discoveries SET notified=1 WHERE nation_id=? AND code=? AND completions=? AND notified=0',
                          (r['nation_id'],r['code'],r['completions']))
                if not c.rowcount:continue
                c.execute('SELECT owner_id,name FROM nations WHERE id=?',(r['nation_id'],));n=c.fetchone()
            if not n:continue
            try:
                with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                    p=tech.PROJECTS[r['code']]
                    e=discord.Embed(title='🔬 '+tech.tr('Badanie ukończone','Research completed'),description=tech.discovery_story(n['name'],r['code'])+'\n**'+tech.name(r['code'])+'**\n'+tech.effect_text(p['effects']))
                    if p['algae']:e.description+='\n'+tech.tr('Włącz program w panelu. Premie wymagają 1 algae miesięcznie.','Enable the program in the panel. Bonuses require 1 algae per month.')
                    e.set_footer(text=tech.tr('Otwórz /tech status i wybierz następny projekt.','Open /tech status and choose your next project.'))
                    user=self.bot.get_user(int(n['owner_id'])) or await asyncio.wait_for(self.bot.fetch_user(int(n['owner_id'])),timeout=10)
                    with db.cursor() as c:
                        c.execute('SELECT owner_id FROM nations WHERE id=?',(r['nation_id'],));live=c.fetchone()
                        if not live or live['owner_id']!=n['owner_id']:
                            c.execute('UPDATE research_discoveries SET notified=0 WHERE nation_id=? AND code=? AND completions=?',(r['nation_id'],r['code'],r['completions']))
                            continue
                    await asyncio.wait_for(user.send(embed=e),timeout=10)
            except (discord.HTTPException,asyncio.TimeoutError,ValueError):
                with db.cursor() as c:c.execute('UPDATE research_discoveries SET notified=2 WHERE nation_id=? AND code=? AND completions=?',(r['nation_id'],r['code'],r['completions']))

    @notifications.before_loop
    async def before_notifications(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(TechCog(bot))
