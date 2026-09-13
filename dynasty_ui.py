"""Marriage proposals are separate from alliance acceptance and require both players."""
import discord
import db
import i18n
import dynasty
from world_service import tr


def description(t, m):
    rules = tr('Mariaż daje każdej stronie +0,25 stabilności/miesiąc (łącznie maks. +0,5). Zerwanie sojuszu przez stronę mariażu: −20 reputacji i −5 stabilności. Więź w grze kończy się wraz z sojuszem. Każde państwo ma trzy postacie: władcę, jednego dorosłego syna i jedną dorosłą córkę.',
               'A marriage grants each party +0.25 stability/month (maximum +0.5 total). Breaking the alliance: −20 reputation and −5 stability. The in-game bond ends with the alliance. Each nation has three characters: a ruler, one adult son and one adult daughter.')
    if m:
        names = {t['proposer_id']:t['a_name'], t['recipient_id']:t['b_name']}
        couple = '\n'.join(names[m[k+'_id']] + ': ' + dynasty.person(m[k+'_person']) for k in ('proposer','recipient'))
        return i18n.term(m['status']) + '\n' + couple + '\n\n' + rules
    return rules + '\n\n' + tr('Wybierz postacie i wyślij propozycję. Dopiero zgoda drugiego gracza tworzy więź.', 'Choose the characters and send a proposal. The other player must accept to establish the bond.')


class MarriageView(i18n.LocalizedView):
    def __init__(self, uid, t, m):
        super().__init__(timeout=600)
        self.uid,self.t,self.m = uid,t,m
        self.own_person,self.other_person = 'self','self'
        for select in (self.own,self.other):
            select.options = [discord.SelectOption(label=dynasty.person(k),value=k,default=k=='self') for k in dynasty.PEOPLE]
        self.own.placeholder = tr('Postać z Twojego państwa', 'Character from your nation')
        self.other.placeholder = tr('Postać z drugiego państwa', 'Character from the other nation')
        self.send.label = tr('Zaproponuj mariaż','Propose marriage')
        self.accept.label = tr('Zaakceptuj mariaż','Accept marriage')
        self.decline.label = tr('Odrzuć / wycofaj','Decline / withdraw')
        if m:
            for item in (self.own,self.other,self.send):self.remove_item(item)
            self.accept.disabled = m['status']!='proposed' or str(uid)!=m['recipient_owner']
            self.decline.disabled = m['status']!='proposed'
        else:
            self.remove_item(self.accept);self.remove_item(self.decline)

    @i18n.localized
    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        await i.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    @discord.ui.select(row=0)
    @i18n.localized
    async def own(self,i,select):
        self.own_person=select.values[0]
        for option in select.options:option.default=option.value==self.own_person
        await i.response.edit_message(view=self)

    @discord.ui.select(row=1)
    @i18n.localized
    async def other(self,i,select):
        self.other_person=select.values[0]
        for option in select.options:option.default=option.value==self.other_person
        await i.response.edit_message(view=self)

    @discord.ui.button(label='Propose',style=discord.ButtonStyle.primary,row=2)
    @i18n.localized
    async def send(self,i,button):
        try:dynasty.propose(self.t['id'],i.user.id,self.own_person,self.other_person)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        await show(i,self.t['id'])

    async def answer(self,i,accept):
        try:dynasty.answer(self.m['id'],i.user.id,accept)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
        await show(i,self.t['id'])

    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success,row=2)
    @i18n.localized
    async def accept(self,i,button):await self.answer(i,True)

    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger,row=2)
    @i18n.localized
    async def decline(self,i,button):await self.answer(i,False)


async def show(i,tid):
    import treaty_service
    try:
        t=treaty_service.get_treaty(tid,i.user.id)
        with db.cursor() as c:
            dynasty.alliance(c,tid)
            m=dynasty.current(c,tid)
    except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
    e=discord.Embed(title=tr('💍 Mariaż dynastyczny','💍 Dynastic marriage'),description=description(t,m))
    from flags import flagged_embed
    flagged_embed(e,(t['a_flag'],t['a_name']),(t['b_flag'],t['b_name']))
    await i.response.send_message(embed=e,view=MarriageView(i.user.id,t,m),ephemeral=True)
