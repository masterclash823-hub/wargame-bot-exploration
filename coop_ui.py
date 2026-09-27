"""Owner/GM controls for revocable cooperative nation access."""
import asyncio
import discord
import db
import i18n
from utils import gm_only,get_nation_by_name,get_nation_by_owner
from world_service import tr
from nation_access import set_coop,members


class CoopView(i18n.LocalizedView):
    def __init__(self,uid,nid):
        super().__init__(timeout=600)
        self.uid,self.nid=uid,nid
        for remove in (False,True):
            select=discord.ui.UserSelect(placeholder=tr('Usuń coopa','Remove co-op') if remove else tr('Dodaj coopa','Add co-op'),row=int(remove))
            async def selected(i,s=select,remove=remove):
                if not await self.interaction_check(i):return
                player=s.values[0]
                if player.bot:
                    await i.response.send_message(tr('Wybierz konto gracza.','Choose a player account.'),ephemeral=True);return
                await i.response.defer(ephemeral=True)
                try:await asyncio.to_thread(set_coop,self.nid,i.user.id,player.id,remove=remove,gm=gm_only(i))
                except ValueError as exc:
                    await i.followup.send(str(exc),ephemeral=True);return
                await i.followup.send(embed=self.embed(),view=CoopView(self.uid,self.nid),ephemeral=True,
                                      allowed_mentions=discord.AllowedMentions.none())
            select.callback=i18n.localized(selected)
            self.add_item(select)

    async def interaction_check(self,i):
        with db.cursor() as c:
            c.execute('SELECT owner_id FROM nations WHERE id=?',(self.nid,));n=c.fetchone()
        if i.user.id==self.uid and n and (gm_only(i) or n['owner_id']==str(i.user.id)):return True
        await i.response.send_message(tr('Dostęp dla głównego właściciela i GM.', 'Primary owner and GM only.'),ephemeral=True)
        return False

    def embed(self):
        with db.cursor() as c:
            c.execute('SELECT name FROM nations WHERE id=?',(self.nid,));n=c.fetchone()
        return discord.Embed(title=tr('Współdzielenie — ','Co-op — ')+(n['name'] if n else '?'),
            description=tr('Wspólne zasoby, wojsko, gospodarka, eventy i limit wypraw. Każdy gracz korzysta z własnego /panel. '
                           'Coop nie może dodawać kolejnych graczy ani przekazywać państwa.\n\nGracze: ',
                           'Shared resources, forces, economy, events and expedition limit. Each player uses their own /panel. '
                           'Co-op members cannot add more players or transfer the nation.\n\nPlayers: ')
                           +', '.join(f'<@{uid}>' for uid in members(self.nid)))


async def show(i,nation=''):
    n=get_nation_by_name(nation) if nation else get_nation_by_owner(str(i.user.id))
    if not n or (not gm_only(i) and n['owner_id']!=str(i.user.id)):
        await i.response.send_message(tr('Dostęp dla głównego właściciela i GM.', 'Primary owner and GM only.'),ephemeral=True);return
    view=CoopView(i.user.id,n['id'])
    await i.response.send_message(embed=view.embed(),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
