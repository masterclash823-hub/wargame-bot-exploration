"""Optional national goals and a private archive of persistent decisions."""
import json
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
from flags import flagged_embed
from utils import get_nation_by_owner,get_nation_by_name,gm_only
from world_service import GOALS,tr,profile,choose_goal,abandon_goal


class NationView(i18n.LocalizedView):
    def __init__(self,owner,nid,gm_read=False):
        super().__init__(timeout=600);self.owner,self.nid,self.gm_read=owner,nid,gm_read

    @i18n.localized
    async def interaction_check(self,interaction):
        n=get_nation_by_owner(str(interaction.user.id))
        if interaction.user.id==self.owner and ((n and n['id']==self.nid) or (self.gm_read and gm_only(interaction))):return True
        await interaction.response.send_message(tr('Nie masz dostępu do tego panelu państwa.','You cannot access this nation panel.'),ephemeral=True)
        return False


def goal_state(nid):
    with db.cursor() as c:
        c.execute('SELECT * FROM nations WHERE id=?',(nid,));n=c.fetchone()
        c.execute('SELECT * FROM nation_goals WHERE nation_id=? ORDER BY id DESC LIMIT 1',(nid,));g=c.fetchone()
        p=profile(c,nid)
    e=flagged_embed(discord.Embed(title=tr('🎯 Cele — ','🎯 Goals — ')+n['name'],color=discord.Color.gold()),(n['flag'],n['name']))
    e.description=tr('Jeden opcjonalny cel naraz. Postęp liczy się automatycznie co miesiąc; nagroda: 10 prestiżu.',
                     'One optional goal at a time. Progress updates automatically each month; reward: 10 prestige.')
    e.add_field(name=tr('Prestiż','Prestige'),value=str(p['prestige']))
    e.add_field(name=tr('Reputacja dyplomatyczna','Diplomatic reputation'),value=f"{p['reputation']}/100")
    if g:
        names=GOALS[g['code']];progress=json.loads(g['progress_json'])
        e.add_field(name=names[0 if i18n.current_language()=='pl' else 1],value=names[2 if i18n.current_language()=='pl' else 3],inline=False)
        count=progress.get('count',0);target={'food_security':3,'development':2,'scholarship':1}[g['code']]
        e.add_field(name=tr('Postęp','Progress'),value=f"{count:g}/{target:g} · "+tr('miesiące: ','months: ')+f"{progress.get('months',0)}/3")
        e.add_field(name=i18n.text('Status'),value=i18n.term(g['status']))
    return e,g


class GoalView(NationView):
    def __init__(self,owner,nid,goal):
        super().__init__(owner,nid);self.goal=goal
        self.select_goal.placeholder=tr('Wybierz jeden z trzech celów','Choose one of three goals')
        self.select_goal.options=[discord.SelectOption(label=v[0 if i18n.current_language()=='pl' else 1],value=k,
                                                       description=v[2 if i18n.current_language()=='pl' else 3][:100]) for k,v in GOALS.items()]
        active=bool(goal and goal['status']=='active')
        self.select_goal.disabled=active;self.abandon.disabled=not active
        self.abandon.label=tr('Porzuć cel bez kary','Abandon goal without penalty')
        self.refresh.label=tr('Odśwież','Refresh')

    async def update(self,interaction):
        embed,goal=goal_state(self.nid)
        await interaction.response.edit_message(embed=embed,view=GoalView(self.owner,self.nid,goal))

    @discord.ui.select(row=0)
    @i18n.localized
    async def select_goal(self,interaction,select):
        try:choose_goal(self.nid,interaction.user.id,select.values[0])
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await self.update(interaction)

    @discord.ui.button(label='Abandon',row=1)
    @i18n.localized
    async def abandon(self,interaction,button):
        try:abandon_goal(self.nid,interaction.user.id,self.goal['id'])
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await self.update(interaction)

    @discord.ui.button(label='Refresh',row=1)
    @i18n.localized
    async def refresh(self,interaction,button):await self.update(interaction)


def memory_page(nid,page):
    from event_adventure import effects_text
    with db.cursor() as c:
        c.execute('SELECT COUNT(*) AS count FROM nation_memories WHERE nation_id=?',(nid,));total=c.fetchone()['count']
        page=min(max(1,page),max(1,total))
        c.execute('SELECT payload_json FROM nation_memories WHERE nation_id=? ORDER BY id DESC LIMIT 1 OFFSET ?',(nid,page-1));r=c.fetchone()
    e=discord.Embed(title=tr('📜 Pamięć państwa','📜 National memory'),color=discord.Color.blue())
    if r:
        p=json.loads(r['payload_json']);e.description=p['opening'][:1500]
        for i,d in enumerate(p['decisions'],1):e.add_field(name=tr('Decyzja ','Decision ')+str(i),value=d['action'][:1000],inline=False)
        e.add_field(name=tr('Zapisane skutki','Recorded consequences'),value=effects_text(p['outcome'],i18n.current_language())[:1000],inline=False)
        e.set_footer(text=f"{page}/{total} · Event #{p['event_id']}")
    else:e.description=tr('Zakończone eventy zapiszą tutaj Twoje decyzje i rzeczywiste skutki.','Completed events will record your decisions and actual consequences here.')
    return e,page,total


class MemoryView(NationView):
    def __init__(self,owner,nid,page,total,gm_read=False):
        super().__init__(owner,nid,gm_read);self.page=page
        self.previous.disabled=page<=1;self.next_page.disabled=page>=total

    async def move(self,interaction,delta):
        e,page,total=memory_page(self.nid,self.page+delta)
        await interaction.response.edit_message(embed=e,view=MemoryView(self.owner,self.nid,page,total,self.gm_read))

    @discord.ui.button(label='◀')
    @i18n.localized
    async def previous(self,interaction,button):await self.move(interaction,-1)

    @discord.ui.button(label='▶')
    @i18n.localized
    async def next_page(self,interaction,button):await self.move(interaction,1)


class WorldCog(commands.Cog):
    goals=app_commands.Group(name='goals',description='Optional national goals / Opcjonalne cele państwowe')

    @goals.command(name='status',description='Choose a goal and view progress / Wybierz cel i zobacz postęp')
    @i18n.localized
    async def status(self,interaction:discord.Interaction):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        embed,goal=goal_state(n['id'])
        await interaction.response.send_message(embed=embed,view=GoalView(interaction.user.id,n['id'],goal),ephemeral=True)

    @app_commands.command(name='memories',description='View remembered decisions / Zapisane decyzje państwa')
    @i18n.localized
    async def memory(self,interaction:discord.Interaction,nation:str='',page:app_commands.Range[int,1,100000]=1):
        if nation and not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        n=get_nation_by_name(nation) if nation else get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        embed,page,total=memory_page(n['id'],page)
        await interaction.response.send_message(embed=embed,view=MemoryView(interaction.user.id,n['id'],page,total,bool(nation)),ephemeral=True)


async def setup(bot):await bot.add_cog(WorldCog())
