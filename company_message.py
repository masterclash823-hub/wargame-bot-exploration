"""Read only the player-selected Discord message used as an improvement proposal."""
import re
import discord
import companies as co
from world_service import tr

LINK = re.compile(r'https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)')


def message_proposal(message, uid, guild_id):
    if not guild_id or not message.guild or message.guild.id != guild_id:
        raise ValueError(tr('Wybierz wiadomość z tego serwera.', 'Choose a message from this server.'))
    if message.author.bot or message.author.id != uid:
        raise ValueError(tr('Zgłoś własną wiadomość z opisem pomysłu.', 'Submit your own message describing the idea.'))
    text=message.content.strip()
    if not text:
        raise ValueError(tr('Nie mogę odczytać opisu. Użyj menu wiadomości: Aplikacje → company_improve.',
                            'Cannot read the description. Use the message menu: Apps → company_improve.'))
    if not 20 <= len(text) <= co.IMPROVEMENT_TEXT_LIMIT:
        raise ValueError(tr('Opis musi mieć 20–4000 znaków. Tekst nie jest ucinany.',
                            'The description must contain 20–4000 characters. Text is never truncated.'))
    source=dict(guild_id=str(guild_id),channel_id=str(message.channel.id),message_id=str(message.id),
                author_id=str(uid),url=f'https://discord.com/channels/{guild_id}/{message.channel.id}/{message.id}')
    return text,source


async def from_link(interaction, link):
    match=LINK.fullmatch(link.strip().strip('<>'))
    if not match:
        raise ValueError(tr('Wklej link do wiadomości Discorda.', 'Paste a Discord message link.'))
    guild_id,channel_id,message_id=map(int,match.groups())
    if not interaction.guild or guild_id != interaction.guild.id:
        raise ValueError(tr('Wybierz wiadomość z tego serwera.', 'Choose a message from this server.'))
    try:
        channel=interaction.client.get_channel(channel_id)
        if channel is None:channel=await interaction.client.fetch_channel(channel_id)
        if not getattr(channel,'guild',None) or channel.guild.id != guild_id:
            raise ValueError(tr('Wybierz kanał tego serwera.', 'Choose a channel on this server.'))
        permissions=channel.permissions_for(interaction.user)
        if not permissions.view_channel or not permissions.read_message_history:
            raise ValueError(tr('Nie masz dostępu do tej wiadomości.', 'You cannot access this message.'))
        message=await channel.fetch_message(message_id)
    except (discord.HTTPException,AttributeError) as exc:
        raise ValueError(tr('Wiadomość jest niedostępna. Spróbuj jej menu: Aplikacje → company_improve.',
                            'Message unavailable. Try its menu: Apps → company_improve.')) from exc
    return message_proposal(message,interaction.user.id,guild_id)


async def from_reply(ctx):
    reference=ctx.message.reference
    if not ctx.guild or not reference or not reference.message_id or reference.channel_id != ctx.channel.id:
        raise ValueError(tr('Odpowiedz na własny opis komendą !company_improve farm (lub inną specjalizacją).',
                            'Reply to your own description with !company_improve farm (or another specialty).'))
    try:
        message=await ctx.channel.fetch_message(reference.message_id)
    except discord.HTTPException as exc:
        raise ValueError(tr('Nie mogę odczytać wskazanej wiadomości.', 'Cannot read the referenced message.')) from exc
    return message_proposal(message,ctx.author.id,ctx.guild.id)
