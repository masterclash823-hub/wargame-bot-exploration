import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import i18n
import war_service
import war_ui
from world_service import tr


class WarsCog(commands.Cog):
    def __init__(self,bot):self.bot=bot
    war=app_commands.Group(name='war',description='Wars and automatic battles / Wojny i automatyczne bitwy')

    @war.command(name='status',description='War inbox and next actions / Panel wojen i oczekujących działań')
    @i18n.localized
    async def status(self,interaction:discord.Interaction):await war_ui.show(interaction,self.bot)

    @war.command(name='attack',description='Offer a battle using your plan / Zaproponuj bitwę z własnym planem')
    @i18n.localized
    async def attack(self,interaction:discord.Interaction,nation:str,plan_id:int,cell_id:int):
        await interaction.response.defer(ephemeral=True)
        from cogs.combat import _nat_name
        n=await asyncio.to_thread(_nat_name,nation)
        if not n:await interaction.followup.send(tr('Nie znaleziono państwa.','Nation not found.'),ephemeral=True);return
        try:eid=await asyncio.to_thread(war_service.challenge,interaction.user.id,n['id'],plan_id,cell_id)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True);return
        await interaction.followup.send(tr(f'Wyzwanie #{eid} czeka na obrońcę w /war status.',f'Challenge #{eid} awaits the defender in /war status.')+'\n'+war_ui.RULES(),ephemeral=True)

    @war.command(name='defend',description='Review automatic defense / Potwierdź automatyczną obronę')
    @i18n.localized
    async def defend(self,interaction:discord.Interaction,challenge_id:int,plan_id:int):
        await war_ui.preview(interaction,challenge_id,plan_id)

    @war.command(name='cancel',description='Cancel or decline a pending challenge / Anuluj lub odrzuć wyzwanie')
    @i18n.localized
    async def cancel(self,interaction:discord.Interaction,challenge_id:int):
        await interaction.response.defer(ephemeral=True)
        try:await asyncio.to_thread(war_service.close,interaction.user.id,challenge_id)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True);return
        await war_ui.show(interaction,self.bot)


async def setup(bot):await bot.add_cog(WarsCog(bot))
