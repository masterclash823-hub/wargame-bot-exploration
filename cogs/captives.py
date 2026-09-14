"""Victory claims with private review and confirmation before population transfer."""
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
import captivity
from world_service import tr,month_index
from utils import get_nation_by_owner


def source(r):return (tr('Bitwa','Battle') if r['source_type']=='battle' else tr('Pokój','Peace'))+f" #{r['source_id']}"


class TakeConfirm(i18n.LocalizedView):
    def __init__(self,uid,nid,oid,cell,quantity):
        super().__init__(timeout=180);self.uid,self.nid,self.oid,self.cell,self.quantity=uid,nid,oid,cell,quantity
        self.confirm.label=tr('Potwierdź zniewolenie','Confirm enslavement')

    @discord.ui.button(label='Confirm',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def confirm(self,i,button):
        if i.user.id!=self.uid:
            await i.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return
        try:captivity.take(self.nid,i.user.id,self.oid,self.cell,self.quantity)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        self.stop()
        await i.response.edit_message(content=tr('Przeniesiono jeńców. Całe uprawnienie tego zwycięstwa jest wykorzystane.','Captives transferred. This victory claim is fully used.'),view=None)


async def preview(i,oid,cell,quantity):
    n=get_nation_by_owner(str(i.user.id))
    if not n:await i.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
    try:
        with db.cursor() as c:r=captivity.opportunity(c,oid,n['id'])
        if type(quantity) is not int or not 1<=quantity<=r['capacity']:raise ValueError(tr('Liczba przekracza limit lub jest nieprawidłowa.','Invalid quantity or victory limit exceeded.'))
    except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
    message=source(r)+f" → #{cell}\n"+tr('Jeńcy: ','Captives: ')+str(quantity)+'\n'+tr('Koszt transportu: ','Transport cost: ')+f'{quantity*.5:g}g\n'+tr(
        'Wymaga niewolnictwa. −5 reputacji. Ludność przegranego zmaleje, Twojej prowincji wzrośnie o tę samą liczbę. Nowi mieszkańcy zużywają żywność i podlegają zwykłym zasadom pracowników. Niewykorzystana część limitu przepada.',
        'Requires slavery. −5 reputation. The loser’s population decreases and your province gains the same number. New inhabitants consume food and follow normal worker rules. Unused capacity is forfeited.')
    await i.response.send_message(message,view=TakeConfirm(i.user.id,n['id'],oid,cell,quantity),ephemeral=True)


class DestinationModal(discord.ui.Modal):
    def __init__(self,r):
        super().__init__(title=tr('Przeniesienie jeńców','Transfer captives'));self.r=r
        self.cell=discord.ui.TextInput(label=tr('ID własnej prowincji docelowej','Your destination province ID'),max_length=15)
        self.quantity=discord.ui.TextInput(label=tr('Liczba jeńców','Number of captives'),default=str(r['capacity']),max_length=10)
        self.add_item(self.cell);self.add_item(self.quantity)

    @i18n.localized
    async def on_submit(self,i):
        try:cell,quantity=int(self.cell.value),int(self.quantity.value)
        except ValueError:await i.response.send_message(tr('Podaj liczby całkowite.','Enter whole numbers.'),ephemeral=True);return
        await preview(i,self.r['id'],cell,quantity)


class ClaimView(i18n.LocalizedView):
    def __init__(self,uid,nid,r):
        super().__init__(timeout=300);self.uid,self.nid,self.r=uid,nid,r
        self.take.label=tr('Weź niewolników','Take slaves');self.release.label=tr('Zrezygnuj z jeńców','Waive the claim')

    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        await i.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    @discord.ui.button(label='Take',style=discord.ButtonStyle.primary)
    @i18n.localized
    async def take(self,i,button):await i.response.send_modal(DestinationModal(self.r))

    @discord.ui.button(label='Release')
    @i18n.localized
    async def release(self,i,button):
        try:captivity.release(self.nid,i.user.id,self.r['id'])
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        await i.response.edit_message(content=tr('Zrezygnowano z brania jeńców.','The captive claim was waived.'),view=None)


class CaptivesCog(commands.Cog):
    captives=app_commands.Group(name='captives',description='Captives after a victory / Jeńcy po zwycięstwie')

    @captives.command(name='take',description='Review a captive transfer / Sprawdź przeniesienie jeńców')
    @i18n.localized
    async def take(self,i:discord.Interaction,claim_id:int,cell_id:int,quantity:int):await preview(i,claim_id,cell_id,quantity)

    @captives.command(name='list',description='Available victory claims / Dostępni jeńcy po zwycięstwach')
    @i18n.localized
    async def list_claims(self,i:discord.Interaction,page:app_commands.Range[int,1,100000]=1):
        n=get_nation_by_owner(str(i.user.id))
        if not n:await i.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        with db.cursor() as c:
            c.execute('SELECT * FROM captive_opportunities WHERE winner_id=? AND used=0 AND created_month+6>? ORDER BY id DESC LIMIT 25 OFFSET ?',(n['id'],month_index(c),(page-1)*25));rows=c.fetchall()
        if not rows:await i.response.send_message(tr('Brak dostępnych jeńców na tej stronie.','No captive claims on this page.'),ephemeral=True);return
        from cogs.panel import ChoiceView
        async def choose(i2,value):
            try:
                with db.cursor() as c:r=captivity.opportunity(c,int(value),n['id'])
            except ValueError as exc:await i2.response.send_message(str(exc),ephemeral=True);return
            await i2.response.send_message(source(r)+'\n'+tr('Limit: ','Limit: ')+str(r['capacity'])+tr('. Jednorazowo, do 6 miesięcy od zwycięstwa.','. One use, within 6 months of victory.'),view=ClaimView(i2.user.id,n['id'],r),ephemeral=True)
        options=[discord.SelectOption(label=f"#{r['id']} · {source(r)}"[:100],description=tr('Limit: ','Limit: ')+str(r['capacity']),value=str(r['id'])) for r in rows]
        await i.response.send_message(tr('Wybierz zwycięstwo. Strona ','Choose a victory. Page ')+str(page)+tr(' · dalsze: /captives list page:',' · more: /captives list page:')+str(page+1),view=ChoiceView(i.user.id,i18n.current_language(),options,choose),ephemeral=True)


async def setup(bot):await bot.add_cog(CaptivesCog())
