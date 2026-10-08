"""Private AI recommendation; only a fresh GM confirmation can approve it."""
import asyncio
import discord
import db
import i18n
import project_ai
from nation_access import find_nation
from utils import gm_only
from world_service import tr
from technology_ui import deliver,response_done
from workflow_ui import choose


class ReviewView(discord.ui.View):
    def __init__(self,uid,guild,pid,fp):
        super().__init__(timeout=600);self.uid,self.guild,self.pid,self.fp=uid,guild,pid,fp
        self.accept.label=tr('GM: zatwierdź rekomendację','GM: approve recommendation')

    @discord.ui.button(label='Approve',style=discord.ButtonStyle.success)
    @i18n.localized
    async def accept(self,i,button):
        if i.user.id!=self.uid or i.guild_id!=self.guild or not gm_only(i):
            await deliver(i,content=tr('Tylko GM, który otworzył tę ocenę.','Only the GM who opened this review.'));return
        await i.response.defer(ephemeral=True)
        try:await asyncio.to_thread(project_ai.approve,self.pid,self.fp,i.user.id)
        except ValueError as exc:await deliver(i,content=str(exc));return
        await deliver(i,content=tr(f'Zatwierdzono projekt #{self.pid}. Gracz płaci i rozpoczyna budowę przez /project build.',
            f'Project #{self.pid} approved. The player pays and starts construction using /project build.'))


async def show(i,pid=0):
    if not response_done(i):await i.response.defer(ephemeral=True)
    gm=gm_only(i)
    if not pid:
        def rows():
            with db.cursor() as c:
                if gm:c.execute("SELECT id,name FROM megaprojects WHERE status='proposed' ORDER BY id DESC")
                else:
                    n=find_nation(i.user.id,c)
                    if not n:return []
                    c.execute("SELECT id,name FROM megaprojects WHERE nation_id=? AND status='proposed' ORDER BY id DESC",(n['id'],))
                return c.fetchall()
        projects=await asyncio.to_thread(rows)
        async def selected(j,value):await show(j,int(value))
        await choose(i,[discord.SelectOption(label=f"#{p['id']} {p['name']}"[:100],value=str(p['id'])) for p in projects],selected,
            content=tr('Wybierz projekt do oceny. Używane jest wyłącznie osobne API projektów; ponowny odczyt zapisanej oceny nie zużywa tokenów.',
                       'Choose a project to review. Only the separate project API is used; reading a saved review consumes no tokens.'));return
    try:p,fp,r=await project_ai.review(pid,i.user.id,gm)
    except ValueError as exc:await deliver(i,content=str(exc));return
    labels={'approve':tr('Rekomendacja akceptacji','Approval recommended'),'revise':tr('Wymaga zmian','Needs revision'),'reject':tr('Rekomendacja odrzucenia','Rejection recommended')}
    e=discord.Embed(title=f"#{pid} {p['name']}"[:256],description=f"**{labels[r['verdict']]}**\n{r['reason']}")
    if r['verdict']=='approve':
        e.add_field(name=tr('Premia miesięczna','Monthly bonus'),value=i18n.resource_list(r['effect']['resources_per_tick']))
        e.add_field(name=tr('Koszt i budowa','Cost and construction'),value=f"{r['cost']['gold']}g · {r['months']} "+tr('mies.','months'))
    e.set_footer(text=tr('Ocena nie daje bonusów. GM akceptuje, gracz płaci, a efekt działa po budowie. Maks. 3 projekty AI na państwo.',
                        'A review grants no bonuses. GM approval, player payment and construction are required. Max. 3 AI projects per nation.'))
    await deliver(i,embed=e,view=ReviewView(i.user.id,i.guild_id,pid,fp) if gm and r['verdict']=='approve' else None)
