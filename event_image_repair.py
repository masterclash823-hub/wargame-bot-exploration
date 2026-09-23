"""Replace an illustration without restarting an event or applying its effects."""
import re

import discord
import db
import i18n
import event_adventure as adventure
import event_media
from event_images import find_event_image, from_upload
from event_ui import send_event, render_public_event
from utils import gm_only
from world_service import tr, world_lock


async def repair(bot,interaction,event_id,query='',file=None,message_link=''):
    if not gm_only(interaction):
        await interaction.response.send_message(i18n.t(i18n.current_language(),'gm_only'),ephemeral=True);return
    await interaction.response.defer(ephemeral=True)
    try:state=adventure.load_run(event_id)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True);return
    public=state.get('visibility')=='public'
    mid=event_media.message_id(event_id) if public else None
    if message_link:
        match=re.fullmatch(r'https://(?:www\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)/?',message_link.strip())
        if not public or not match or match[1]!=str(interaction.guild.id) or match[2]!=str(state.get('channel_id')):
            await interaction.followup.send(tr('Podaj link do publicznej wiadomości tego eventu na tym serwerze.',
                                               'Provide this event’s public message link on this server.'),ephemeral=True);return
        mid=int(match[3])
    message=None
    if mid:
        try:
            channel=bot.get_channel(int(state['channel_id'])) or await bot.fetch_channel(int(state['channel_id']))
            p=channel.permissions_for(interaction.guild.me)
            if channel.guild.id!=interaction.guild.id or not all((p.view_channel,p.send_messages,p.embed_links,p.attach_files)):
                raise ValueError
            message=await channel.fetch_message(mid)
            expected=render_public_event(state)
            if (message.author.id!=bot.user.id or not message.embeds
                    or message.embeds[0].title!=expected.title or message.embeds[0].description!=expected.description):
                raise ValueError
        except (discord.HTTPException,ValueError,KeyError):
            await interaction.followup.send(tr('Nie mogę edytować tej wiadomości. Sprawdź link oraz prawa bota do odczytu historii, osadzeń i załączania plików.',
                                               'Cannot edit this message. Check the link and the bot’s history, embed and attachment permissions.'),ephemeral=True);return
    image=None
    # A second call with an old message link can attach the file just saved by GM.
    if message_link and not file and not query:
        cached=event_media.attachment(event_id)
        if cached:
            try:image={**event_media.metadata(event_id),'data':cached.fp.read()}
            finally:cached.close()
    if image is None:
        if file:
            try:image=await from_upload(file)
            except (ValueError,discord.HTTPException):
                await interaction.followup.send(tr('Wgraj prawidłowy JPG, PNG lub WebP do 6 MB i 12 mln pikseli.',
                                                   'Upload a valid JPG, PNG or WebP up to 6 MB and 12 million pixels.'),ephemeral=True);return
        else:image=await find_event_image(state['opening'],query)
    if not image:
        await interaction.followup.send(tr('Nie udało się pobrać ilustracji. Wpisz inne hasła lub dodaj plik JPG, PNG albo WebP. Dotychczasowy obraz pozostał bez zmian.',
                                           'Could not download an illustration. Try other keywords or attach JPG, PNG or WebP. The previous image is unchanged.'),ephemeral=True);return
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT event_id FROM event_runs WHERE event_id=?',(event_id,))
        if not c.fetchone():
            missing=True
        else:
            missing=False
            event_media.save(c,event_id,image)
    if missing:
        await interaction.followup.send(tr('Event został usunięty.','The event was deleted.'),ephemeral=True);return
    notice=tr('Ilustracja zapisana. Gracz zobaczy ją przez /event play. Decyzje i skutki eventu pozostają bez zmian.',
              'Illustration saved. The player can see it through /event play. Event decisions and effects are unchanged.')
    if message:
        try:
            await send_event(message.edit,state,public=True,editing=True)
            event_media.remember_message(event_id,message.id)
            notice+='\n'+tr('Publiczna wiadomość została zaktualizowana.','The public message was updated.')
        except discord.HTTPException:
            notice+='\n'+tr('Nie udało się potwierdzić edycji publicznej wiadomości. Sprawdź ją przed ponowieniem.',
                             'The public edit was not confirmed. Check the message before retrying.')
    elif public:
        notice+='\n'+tr('Aby uzupełnić stary publiczny post, powtórz /event image z opcją message_link i linkiem do tej wiadomości.',
                         'To repair the old public post, repeat /event image with its message_link.')
    await send_event(interaction.followup.send,state,content=notice,ephemeral=True)
