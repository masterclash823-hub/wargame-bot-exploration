"""Private review and explicit acceptance of diplomatic terms."""
import json
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands
import db
import i18n
from flags import flagged_embed
from utils import get_nation_by_owner,get_nation_by_name,gm_only
from world_service import tr
import treaty_service as service


def treaty_embed(t):
    pl=i18n.current_language()=='pl';terms=json.loads(t['terms_json'])
    e=flagged_embed(discord.Embed(title=f"📜 #{t['id']} · "+service.KINDS[t['kind']][0 if pl else 1],
                                   description=t['a_name']+' → '+t['b_name'],color=discord.Color.blue()),
                    (t['a_flag'],t['a_name']),(t['b_flag'],t['b_name']))
    e.add_field(name=i18n.text('Status'),value=i18n.term(t['status']))
    e.add_field(name=tr('Czas obowiązywania','Duration'),value=str(t['duration'])+tr(' miesięcy gry',' game months'))
    e.add_field(name=tr('Ogłoszenie','Announcement'),value=tr('Publiczne — tylko fakt zawarcia','Public — signature only') if t['visibility']=='public' else tr('Prywatne','Private'))
    for prefix,name in (('give',t['a_name']),('receive',t['b_name'])):
        text=f"{terms[prefix+'_gold']:g}g\n"+tr('Przekazane prowincje: ','Ceded provinces: ')+(', '.join(map(str,terms[prefix+'_cells'])) or '—')
        e.add_field(name=(tr('Oddaje: ','Gives: ')+name)[:256],value=text,inline=False)
    if terms['tribute_gold']:
        payer=t['a_name'] if terms['payer']=='proposer' else t['b_name']
        e.add_field(name=tr('Reparacje miesięczne','Monthly reparations'),value=f"{payer}: {terms['tribute_gold']:g}g × {terms['tribute_months']}\n"+
                    tr('Pozostałe raty: ','Installments left: ')+str(terms['tribute_months'] if t['status'] in ('draft','proposed') else t['payments_left'])+
                    tr(' · zaległość: ',' · arrears: ')+f"{t['arrears']:g}g",inline=False)
    details={
        'peace':tr('Akceptacja kończy wojnę i ustanawia rozejm na wskazany czas.','Acceptance ends the war and establishes a truce for the stated duration.'),
        'non_aggression':tr('Wojna w okresie obowiązywania oznacza naruszenie paktu.','Declaring war during the term breaches the pact.'),
        'alliance':tr('Sojusz daje wzajemny dostęp wojskowy. Nie wypowiada automatycznie cudzych wojen.','An alliance grants mutual military access. It does not automatically join another nation’s wars.'),
        'military_access':tr('Oba państwa otrzymują prawo przemarszu.','Both nations gain military passage rights.'),
        'guarantee':tr('Autor gwarantuje bezpieczeństwo odbiorcy. Po ataku musi odpowiedzieć na wezwanie przed kolejnym miesiącem.','The proposer guarantees the recipient. After an attack, answer the call before the next game month.'),
    }
    e.add_field(name=tr('Zasady','Rules'),value=details[t['kind']],inline=False)
    for start in range(0,len(terms['note']),1000):
        e.add_field(name=tr('Opis do oceny stron i GM','Note for the parties and GM'),value=terms['note'][start:start+1000],inline=False)
    if t['status']=='draft':e.add_field(name=tr('Przed wysłaniem','Before sending'),value=tr(
        'Sprawdź warunki. Przyciskiem poniżej możesz dodać raty, opis i widoczność. Odbiorca zobaczy propozycję dopiero po kliknięciu „Wyślij propozycję”.',
        'Review the terms. The button below lets you add installments, a note and visibility. The recipient only sees the proposal after you click “Send proposal”.'),inline=False)
    e.set_footer(text=tr('Zerwanie: −10 reputacji. Uzgodnione raty i zaległości pozostają do zapłaty.','Breaking: −10 reputation. Agreed installments and arrears remain payable.'))
    return e


class TreatyView(i18n.LocalizedView):
    def __init__(self,viewer,t):
        super().__init__(timeout=600);self.viewer,self.t=viewer,t
        self.accept.label=tr('Akceptuj wszystkie warunki','Accept all terms')
        self.accept.disabled=t['status']!='proposed' or str(viewer)!=t['b_owner']
        self.end.label=tr('Zerwij (−10 reputacji)','Break (−10 reputation)') if t['status']=='active' else tr('Odrzuć / wycofaj','Decline / withdraw')
        self.end.disabled=t['status'] not in ('draft','proposed','active') or str(viewer) not in (t['a_owner'],t['b_owner'])
        self.edit_terms.label=tr('Raty, opis i widoczność','Installments, note & visibility')
        self.edit_terms.disabled=t['status'] not in ('draft','proposed') or str(viewer)!=t['a_owner']
        self.submit.label=tr('Wyślij propozycję','Send proposal')
        self.submit.disabled=t['status']!='draft' or str(viewer)!=t['a_owner']

    @i18n.localized
    async def interaction_check(self,interaction):
        if interaction.user.id==self.viewer:return True
        await interaction.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    async def update(self,interaction):
        t=service.get_treaty(self.t['id'],interaction.user.id,gm_only(interaction))
        await interaction.response.edit_message(embed=treaty_embed(t),view=TreatyView(self.viewer,t))

    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success)
    @i18n.localized
    async def accept(self,interaction,button):
        try:service.accept(self.t['id'],interaction.user.id,self.t['version'])
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await self.update(interaction)

    @discord.ui.button(label='End',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def end(self,interaction,button):
        try:service.end(self.t['id'],interaction.user.id,self.t['status'])
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await self.update(interaction)

    @discord.ui.button(label='Edit terms',row=1)
    @i18n.localized
    async def edit_terms(self,interaction,button):
        try:t=service.get_treaty(self.t['id'],interaction.user.id)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        if t['status'] not in ('draft','proposed') or str(interaction.user.id)!=t['a_owner']:
            await interaction.response.send_message(tr('Tylko autor oczekującej propozycji może ją zmienić.','Only the author of a pending proposal can amend it.'),ephemeral=True);return
        from cogs.panel import FieldsModal
        terms=json.loads(t['terms_json'])
        async def submit(i,amount,months,payer,note,visibility):
            payer={'autor':'proposer','odbiorca':'recipient'}.get(payer.strip().lower(),payer.strip().lower())
            visibility={'publiczne':'public','prywatne':'private'}.get(visibility.strip().lower(),visibility.strip().lower())
            try:service.amend_tribute(t['id'],i.user.id,float(amount or 0),int(months or 0),payer,note,visibility)
            except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True);return
            await show_treaty(i,t['id'])
        await interaction.response.send_modal(FieldsModal(tr('Warunki traktatu','Treaty terms'),[
            {'label':tr('Złoto miesięcznie (0 = brak rat)','Monthly gold (0 = no installments)'), 'default':str(terms['tribute_gold'])},
            {'label':tr('Liczba rat','Number of installments'),'default':str(terms['tribute_months'])},
            {'label':tr('Płatnik: autor lub odbiorca','Payer: proposer or recipient'),
             'default':({'proposer':'autor','recipient':'odbiorca'}[terms['payer']] if i18n.current_language()=='pl' else terms['payer'])},
            {'label':tr('Opis dla stron i GM','Note for parties and GM'),'default':terms['note'],'required':False,'max_length':2000,'style':discord.TextStyle.paragraph},
            {'label':tr('Ogłoszenie: publiczne lub prywatne','Announcement: public or private'),
             'default':({'public':'publiczne','private':'prywatne'}[t['visibility']] if i18n.current_language()=='pl' else t['visibility'])},
        ],submit))

    @discord.ui.button(label='Send proposal',style=discord.ButtonStyle.primary,row=1)
    @i18n.localized
    async def submit(self,interaction,button):
        try:service.submit_proposal(self.t['id'],interaction.user.id,self.t['version'])
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await self.update(interaction)


async def show_treaty(interaction,tid):
    try:t=service.get_treaty(tid,interaction.user.id,gm_only(interaction))
    except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
    await interaction.response.send_message(embed=treaty_embed(t),view=TreatyView(interaction.user.id,t),ephemeral=True)


async def propose_simple(interaction,nation,kind):
    n=get_nation_by_owner(str(interaction.user.id));target=get_nation_by_name(nation)
    if not n or not target:await interaction.response.send_message(i18n.t(i18n.current_language(),'nation_not_found'),ephemeral=True);return
    try:tid=service.propose(n['id'],interaction.user.id,target['id'],kind,draft=True)
    except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
    await show_treaty(interaction,tid)


class GuaranteeView(i18n.LocalizedView):
    def __init__(self,viewer,call_id):
        super().__init__(timeout=600);self.viewer,self.call_id=viewer,call_id
        self.honor.label=tr('Dołącz do wojny obronnej','Join the defensive war')
        self.decline.label=tr('Odmów (−10 reputacji)','Decline (−10 reputation)')

    @i18n.localized
    async def interaction_check(self,interaction):
        if interaction.user.id==self.viewer:return True
        await interaction.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    async def answer(self,interaction,join):
        try:service.respond_guarantee(self.call_id,interaction.user.id,join)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await interaction.response.edit_message(content=tr('Odpowiedź zapisana.','Response recorded.'),view=None)

    @discord.ui.button(label='Honor',style=discord.ButtonStyle.success)
    @i18n.localized
    async def honor(self,interaction,button):await self.answer(interaction,True)

    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def decline(self,interaction,button):await self.answer(interaction,False)


class TreatyListView(i18n.LocalizedView):
    def __init__(self,viewer,rows,page=1,has_next=False):
        super().__init__(timeout=600);self.viewer,self.page=viewer,page
        self.previous.disabled=page<=1;self.next_page.disabled=not has_next
        self.choose.options=[discord.SelectOption(label=f"#{r['id']} {service.KINDS[r['kind']][0 if i18n.current_language()=='pl' else 1]}"[:100],value=str(r['id']),
                                                 description=(r['a_name']+' / '+r['b_name']+' · '+i18n.term(r['status']))[:100]) for r in rows]
        self.choose.placeholder=tr('Wybierz traktat','Choose a treaty')

    @i18n.localized
    async def interaction_check(self,interaction):
        if interaction.user.id==self.viewer:return True
        await interaction.response.send_message(i18n.text('This is not your menu.'),ephemeral=True);return False

    @discord.ui.select(row=0)
    @i18n.localized
    async def choose(self,interaction,select):await show_treaty(interaction,int(select.values[0]))

    @discord.ui.button(label='◀',row=1)
    @i18n.localized
    async def previous(self,interaction,button):await TreatiesCog.list_treaties.callback(None,interaction,self.page-1)

    @discord.ui.button(label='▶',row=1)
    @i18n.localized
    async def next_page(self,interaction,button):await TreatiesCog.list_treaties.callback(None,interaction,self.page+1)


class TreatiesCog(commands.Cog):
    treaty=app_commands.Group(name='treaty',description='Diplomatic treaties / Traktaty dyplomatyczne')

    @treaty.command(name='propose',description='Propose a treaty with clear terms / Zaproponuj traktat')
    @i18n.localized
    async def propose(self,interaction:discord.Interaction,nation:str,
                      kind:Literal['peace','non_aggression','alliance','military_access','guarantee'],
                      duration:app_commands.Range[int,1,120]=12,give_gold:float=0.0,receive_gold:float=0.0,
                      give_cells:str='',receive_cells:str='',visibility:Literal['public','private']='public',note:str=''):
        n=get_nation_by_owner(str(interaction.user.id));target=get_nation_by_name(nation)
        if not n or not target:await interaction.response.send_message(i18n.t(i18n.current_language(),'nation_not_found'),ephemeral=True);return
        try:tid=service.propose(n['id'],interaction.user.id,target['id'],kind,duration,visibility,draft=True,
                               give_gold=give_gold,receive_gold=receive_gold,give_cells=give_cells,receive_cells=receive_cells,note=note)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await show_treaty(interaction,tid)

    @treaty.command(name='tribute',description='Add monthly reparations before acceptance / Ustal raty reparacji')
    @i18n.localized
    async def tribute(self,interaction:discord.Interaction,treaty_id:int,amount:float,
                      months:app_commands.Range[int,0,120],payer:Literal['proposer','recipient']='recipient'):
        try:service.amend_tribute(treaty_id,interaction.user.id,amount,months,payer)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        await show_treaty(interaction,treaty_id)

    @treaty.command(name='view',description='Review terms and accept or reject / Przejrzyj i zaakceptuj warunki')
    @i18n.localized
    async def view(self,interaction:discord.Interaction,treaty_id:int):await show_treaty(interaction,treaty_id)

    @treaty.command(name='list',description='Your treaties and pending proposals / Twoje traktaty i propozycje')
    @i18n.localized
    async def list_treaties(self,interaction:discord.Interaction,page:app_commands.Range[int,1,100000]=1):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        with db.cursor() as c:
            c.execute('SELECT t.*,a.name AS a_name,b.name AS b_name FROM treaties t JOIN nations a ON a.id=t.proposer_id '
                      "JOIN nations b ON b.id=t.recipient_id WHERE proposer_id=? OR (recipient_id=? AND submitted_month IS NOT NULL) ORDER BY CASE WHEN status IN ('draft','proposed') THEN 0 WHEN status='active' THEN 1 ELSE 2 END,id DESC LIMIT 21 OFFSET ?",
                      (n['id'],n['id'],(page-1)*20));rows=c.fetchall()
        await interaction.response.send_message(tr('Wybierz traktat. Strona ','Choose a treaty. Page ')+str(page) if rows else tr('Brak traktatów na tej stronie.','No treaties on this page.'),
                                                view=TreatyListView(interaction.user.id,rows[:20],page,len(rows)>20) if rows else None,ephemeral=True)

    @treaty.command(name='calls',description='Answer security guarantee calls / Odpowiedz na wezwania gwarancyjne')
    @i18n.localized
    async def calls(self,interaction:discord.Interaction):
        n=get_nation_by_owner(str(interaction.user.id))
        if not n:await interaction.response.send_message(i18n.t(i18n.current_language(),'no_nation'),ephemeral=True);return
        with db.cursor() as c:
            c.execute("SELECT g.*,a.name AS attacker,d.name AS defender FROM guarantee_calls g JOIN treaties t ON t.id=g.treaty_id "
                      "JOIN nations a ON a.id=g.attacker_id JOIN nations d ON d.id=g.defender_id WHERE t.proposer_id=? AND g.status='pending' ORDER BY g.id LIMIT 1",(n['id'],));call=c.fetchone()
        if not call:await interaction.response.send_message(tr('Brak oczekujących wezwań.','No pending calls.'),ephemeral=True);return
        await interaction.response.send_message(tr(f"{call['attacker']} zaatakowało {call['defender']}. Twoje państwo gwarantuje obronę. Odpowiedz przed kolejnym miesiącem gry.",
                                                   f"{call['attacker']} attacked {call['defender']}. Your nation guarantees its defense. Answer before the next game month."),
                                                view=GuaranteeView(interaction.user.id,call['id']),ephemeral=True)


async def setup(bot):await bot.add_cog(TreatiesCog())
