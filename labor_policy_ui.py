"""Labor reforms show a concrete quote and consequences before confirmation."""
import discord
import db
import i18n
import labor_regimes as service
from world_service import tr
from utils import get_nation_by_owner


def rules():
    return tr(
        'Wolna praca: zwykła produkcja, bez kosztów tej polityki.\n\n'
        'Niewolnictwo: +10% produkcji farm, pastwisk, plantacji, obozów drwali, kopalń, glinianek i smolarni. '
        'Co miesiąc: nadzór 1 złota/1000 mieszkańców, +2 niepokojów, −0,5 stabilności i −1 reputacji. '
        'Wprowadzenie: 50 złota, −5 stabilności i −10 reputacji.\n\n'
        'Zniesienie: 20 złota/1000 mieszkańców na reformę, potem 3 miesiące −5% produkcji wymienionych budynków. '
        'Koszty nadzoru i miesięczne kary kończą się od razu. Ludność i pula pracowników pozostają te same. Algae nie otrzymuje premii.',
        'Free labor: normal production, without costs from this policy.\n\n'
        'Slavery: +10% output from farms, pastures, plantations, lumber camps, mines, clay pits and tar works. '
        'Monthly: supervision costs 1 gold/1000 inhabitants, +2 unrest, −0.5 stability and −1 reputation. '
        'Introduction: 50 gold, −5 stability and −10 reputation.\n\n'
        'Abolition: reform costs 20 gold/1000 inhabitants, followed by 3 months of −5% output from those buildings. '
        'Supervision costs and monthly penalties end immediately. Population and workforce stay the same. Algae receives no bonus.')


class LaborView(i18n.LocalizedView):
    def __init__(self,uid,nid,state,cost):
        super().__init__(timeout=300)
        self.uid,self.nid,self.state,self.cost=uid,nid,state,cost
        self.mode='free' if state['mode']=='slavery' else 'slavery'
        self.confirm.label=tr('Znieś niewolnictwo','Abolish slavery') if self.mode=='free' else tr('Wprowadź niewolnictwo','Introduce slavery')

    @i18n.localized
    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        await i.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    @discord.ui.button(label='Confirm reform',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def confirm(self,i,button):
        try:service.change(self.nid,i.user.id,self.mode,self.state['version'],self.cost)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        self.stop()
        await i.response.edit_message(content=tr('Reforma przyjęta. Aktualny bilans znajdziesz w Gospodarka → Zasoby.',
                                                  'Reform adopted. Open Economy → Resources for the updated balance.'),embed=None,view=None)


async def show(i):
    n=get_nation_by_owner(str(i.user.id))
    if not n:await i.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
    with db.cursor() as c:
        state=service.state(c,n['id'])
        c.execute('SELECT COALESCE(SUM(population),0) AS pop FROM provinces WHERE owner_nation_id=? AND active=1',(n['id'],))
        pop=c.fetchone()['pop']
    mode='free' if state['mode']=='slavery' else 'slavery'
    cost=service.costs(pop,mode)
    e=discord.Embed(title=tr('Polityka pracy','Labor policy'),description=rules())
    from flags import flagged_embed
    flagged_embed(e,(n['flag'],n['name']))
    e.add_field(name=tr('Obecnie','Current'),value=service.label(state['mode']))
    e.add_field(name=tr('Okres przejściowy','Transition remaining'),value=str(state['transition_months'])+tr(' mies.',' months'))
    e.add_field(name=tr('Koszt proponowanej reformy','Quoted reform cost'),value=f'{cost:g}g')
    e.add_field(name=tr('Nadzór przy niewolnictwie / miesiąc','Supervision under slavery / month'),value=f'{pop/1000:g}g')
    e.set_footer(text=tr('Przycisk zatwierdza opisaną zmianę i pobiera podany koszt.','The button confirms the described reform and charges the quoted cost.'))
    await i.response.send_message(embed=e,view=LaborView(i.user.id,n['id'],state,cost),ephemeral=True)
