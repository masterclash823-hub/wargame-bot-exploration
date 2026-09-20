"""Optional national goals and a private archive of persistent decisions."""
import json
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
from flags import flagged_embed
from utils import get_nation_by_owner,get_nation_by_name,gm_only
from world_service import GOALS,tr,profile,choose_goal,abandon_goal,goal_progress,month_index,gm_create_goal,gm_complete_goal


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
        c.execute("SELECT * FROM nation_goals WHERE nation_id=? ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END,id DESC LIMIT 1",(nid,));g=c.fetchone()
        p=profile(c,nid)
        progress=goal_progress(c,g,month_index(c)) if g and g['status']=='active' else json.loads(g['progress_json']) if g else {}
    e=flagged_embed(discord.Embed(title=tr('🎯 Cele — ','🎯 Goals — ')+n['name'],color=discord.Color.gold()),(n['flag'],n['name']))
    e.description=tr('Jeden opcjonalny cel naraz. Zwykłe cele rozliczają się co miesiąc; cele opisowe zatwierdza GM. Nagroda: 10 prestiżu.',
                     'One optional goal at a time. Standard goals settle monthly; the GM confirms custom goals. Reward: 10 prestige.')
    e.add_field(name=tr('Prestiż','Prestige'),value=str(p['prestige']))
    e.add_field(name=tr('Reputacja dyplomatyczna','Diplomatic reputation'),value=f"{p['reputation']}/100")
    if g:
        if g['code'] in GOALS:
            names=GOALS[g['code']]
            e.add_field(name=names[0 if i18n.current_language()=='pl' else 1],value=names[2 if i18n.current_language()=='pl' else 3],inline=False)
            count=progress.get('count',0);target={'food_security':3,'development':2,'scholarship':1}[g['code']]
            e.add_field(name=tr('Postęp','Progress'),value=f"{min(count,target):g}/{target:g} · "+tr('miesiące: ','months: ')+f"{min(progress.get('months',0),3)}/3")
        else:
            details=json.loads(g['baseline_json'])
            title=details.get('title',tr('Cel opisowy','Custom goal'))
            description=details.get('description',tr('Warunki ustala GM.','Ask the GM for the requirements.'))
            e.description+='\n\n**'+discord.utils.escape_markdown(title)+'**\n'+description
            if g['status']=='active':
                e.add_field(name=tr('Postęp','Progress'),value=tr('Wykonanie potwierdza GM.','Completion requires GM confirmation.'))
        e.add_field(name=i18n.text('Status'),value=i18n.term(g['status']))
        if progress.get('confirmed_by'):
            e.add_field(name=tr('Potwierdził GM','Confirmed by GM'),value=f"<@{progress['confirmed_by']}>")
        e.set_footer(text=f"ID: {g['id']}")
    return e,g


class GMGoalView(NationView):
    def __init__(self,owner,nid,goal):
        super().__init__(owner,nid,gm_read=True)
        self.goal=goal
        self.complete.label=tr('Potwierdź wykonanie (+10 prestiżu)','Confirm completion (+10 prestige)')
        self.complete.disabled=not goal or goal['status']!='active'

    @discord.ui.button(label='Confirm completion',style=discord.ButtonStyle.success)
    @i18n.localized
    async def complete(self,interaction,button):
        if interaction.user.id!=self.owner or not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        try:gm_complete_goal(self.nid,self.goal['id'],interaction.user.id)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        embed,goal=goal_state(self.nid)
        await interaction.response.edit_message(embed=embed,view=GMGoalView(self.owner,self.nid,goal),allowed_mentions=discord.AllowedMentions.none())


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
    @app_commands.describe(nation='[GM] Nation to inspect / Państwo do sprawdzenia')
    @i18n.localized
    async def status(self,interaction:discord.Interaction,nation:str=''):
        if nation and not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        n=get_nation_by_name(nation) if nation else get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        embed,goal=goal_state(n['id'])
        view=GMGoalView(interaction.user.id,n['id'],goal) if nation else GoalView(interaction.user.id,n['id'],goal)
        await interaction.response.send_message(embed=embed,view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @goals.command(name='create',description='[GM] Create a custom national goal / Utwórz cel państwowy')
    @app_commands.describe(nation='Nation name / Nazwa państwa',title='Goal title / Tytuł celu',description='Completion requirements / Warunki wykonania')
    @i18n.localized
    async def create(self,interaction:discord.Interaction,nation:str,title:str,description:str):
        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        n=get_nation_by_name(nation)
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'nation_not_found'),ephemeral=True);return
        try:gm_create_goal(n['id'],interaction.user.id,title,description)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        embed,goal=goal_state(n['id'])
        await interaction.response.send_message(embed=embed,view=GMGoalView(interaction.user.id,n['id'],goal),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @goals.command(name='complete',description='[GM] Review and confirm a goal / Sprawdź i potwierdź wykonanie celu')
    @app_commands.describe(nation='Nation name / Nazwa państwa',goal_id='Goal ID from status / ID celu z podglądu')
    @i18n.localized
    async def complete(self,interaction:discord.Interaction,nation:str,goal_id:int):
        if not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        n=get_nation_by_name(nation)
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'nation_not_found'),ephemeral=True);return
        embed,goal=goal_state(n['id'])
        if not goal or goal['id']!=goal_id or goal['status']!='active':
            await interaction.response.send_message(tr('Nie znaleziono aktywnego celu tego państwa.','No matching active goal for this nation.'),ephemeral=True);return
        await interaction.response.send_message(embed=embed,view=GMGoalView(interaction.user.id,n['id'],goal),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='memories',description='View remembered decisions / Zapisane decyzje państwa')
    @i18n.localized
    async def memory(self,interaction:discord.Interaction,nation:str='',page:app_commands.Range[int,1,100000]=1):
        if nation and not gm_only(interaction):await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        n=get_nation_by_name(nation) if nation else get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        embed,page,total=memory_page(n['id'],page)
        await interaction.response.send_message(embed=embed,view=MemoryView(interaction.user.id,n['id'],page,total,bool(nation)),ephemeral=True)


async def setup(bot):await bot.add_cog(WorldCog())
