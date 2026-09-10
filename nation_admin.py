"""GM review of nation ownership transfer, with a fresh role and ownership check."""
import discord
import i18n
from utils import gm_only
from world_service import transfer_nation,tr


class TransferView(i18n.LocalizedView):
    def __init__(self,gm_id,nation,player_id):
        super().__init__(timeout=120);self.gm_id,self.nation,self.player_id=gm_id,nation,player_id
        self.confirm.label=tr('Przekaż państwo temu graczowi','Transfer the nation to this player')

    @discord.ui.button(label='Transfer',style=discord.ButtonStyle.danger)
    @i18n.localized
    async def confirm(self,interaction,button):
        if interaction.user.id!=self.gm_id or not gm_only(interaction):
            await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
        try:transfer_nation(self.nation['id'],self.player_id,self.nation['owner_id'],interaction.user.id)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        self.stop()
        await interaction.response.edit_message(content=tr('Państwo przekazane. Nowy właściciel może otworzyć /panel.','Nation transferred. The new owner can open /panel.'),embed=None,view=None)
