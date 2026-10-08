"""Private paginated choices shared by player workflows."""
import discord
import i18n
from world_service import tr
from technology_ui import deliver


class Choices(discord.ui.View):
    def __init__(self,uid,options,handler,page=0):
        super().__init__(timeout=600)
        self.uid=uid
        pages=max(1,(len(options)+24)//25);page=min(page,pages-1)
        select=discord.ui.Select(placeholder=tr('Wybierz pozycję','Choose an item'),options=options[page*25:(page+1)*25])
        @i18n.localized
        async def selected(i):
            if await self.interaction_check(i):await handler(i,select.values[0])
        select.callback=selected;self.add_item(select)
        for title,delta in ((tr('Wstecz','Back'),-1),(tr('Dalej','Next'),1)):
            b=discord.ui.Button(label=f'{title} · {page+1}/{pages}',disabled=not 0<=page+delta<pages,row=1)
            @i18n.localized
            async def turn(i,delta=delta):
                if await self.interaction_check(i):
                    await i.response.edit_message(view=Choices(uid,options,handler,page+delta))
            b.callback=turn;self.add_item(b)

    async def interaction_check(self,i):
        if i.user.id==self.uid:return True
        await deliver(i,content=tr('To nie jest Twój panel.','This is not your panel.'))
        return False


async def choose(i,options,handler,content=None):
    if not options:
        await deliver(i,content=tr('Brak dostępnych pozycji.','No available entries.'));return
    await deliver(i,content=content,view=Choices(i.user.id,options,handler))
