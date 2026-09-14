"""Recoverable result delivery to the original channel, with one GM-role mention."""
import json
import asyncio
from datetime import datetime,timezone,timedelta
import discord
import config
import db
from world_service import tr,world_lock
from flags import flagged_embed


def gm_role(guild):
    if config.GM_ROLE_ID:
        return guild.get_role(int(config.GM_ROLE_ID)) if config.GM_ROLE_ID.isdigit() else None
    return next((r for r in guild.roles if r.name.strip().casefold()==config.GM_ROLE_NAME.strip().casefold()),None)


def check_channel(channel,guild):
    role=gm_role(guild)
    if not role:raise ValueError(tr('Nie znaleziono skonfigurowanej roli Game Master.','The configured Game Master role was not found.'))
    p=channel.permissions_for(guild.me)
    send=p.send_messages_in_threads if isinstance(channel,discord.Thread) else p.send_messages
    if not all((p.view_channel,send,p.embed_links,p.read_message_history)):
        raise ValueError(tr('Bot potrzebuje dostępu do kanału, wysyłania wiadomości, osadzeń i odczytu historii.',
                            'The bot needs channel access, send, embed and message-history permissions.'))
    if not role.mentionable and not p.mention_everyone:
        raise ValueError(tr('Aby ping GM działał, włącz możliwość oznaczania roli Game Master lub nadaj botowi uprawnienie do oznaczania ról.',
                            'To ping the GM, make the Game Master role mentionable or allow the bot to mention roles.'))
    return role


def marker(eid):return f'Exploration #{eid}'


def render(row):
    s=json.loads(row['state_json']);pl=s['lang']=='pl'
    verdict=('WYPRAWA UDANA' if pl else 'EXPEDITION SUCCEEDED') if s['success'] else ('WYPRAWA NIEUDANA' if pl else 'EXPEDITION FAILED')
    e=discord.Embed(title=verdict+' · '+row['name'][:140],description=s['text'],color=discord.Color.green() if s['success'] else discord.Color.red())
    flagged_embed(e,(row['flag'],row['name']))
    e.set_footer(text=marker(row['id']))
    return e


def mark_sent(eid,message_id):
    with db.atomic() as c:
        world_lock(c)
        c.execute("UPDATE explorations SET publication_status='sent',message_id=? WHERE id=? AND publication_status='sending'",(str(message_id),eid))


async def publish(bot,eid):
    with db.cursor() as c:
        c.execute('SELECT e.*,n.name,n.flag FROM explorations e JOIN nations n ON n.id=e.nation_id WHERE e.id=?',(eid,));r=c.fetchone()
    if not r or r['status']!='resolved' or r['publication_status']=='sent':return
    guild=bot.get_guild(int(r['guild_id']))
    if not guild:return
    channel=bot.get_channel(int(r['channel_id']))
    if channel is None:channel=await bot.fetch_channel(int(r['channel_id']))
    if channel.guild.id!=guild.id:return
    role=check_channel(channel,guild)
    if r['publication_status']=='sending':
        # A crash after Discord accepted a message must not cause a second role ping.
        started=datetime.fromisoformat(r['publication_started'])
        if datetime.now(timezone.utc)-started<timedelta(minutes=2):return
        seen=0
        async for message in channel.history(limit=100,after=started-timedelta(seconds=10)):
            seen+=1
            if message.author.id==bot.user.id and any(e.footer.text==marker(eid) for e in message.embeds):
                mark_sent(eid,message.id);return
        if seen>=100:return  # Incomplete history: preserve uncertain delivery instead of duplicating it.
        with db.cursor() as c:c.execute("UPDATE explorations SET publication_status='pending' WHERE id=? AND publication_status='sending' AND publication_started=?",(eid,r['publication_started']))
    with db.atomic() as c:
        world_lock(c)
        c.execute("UPDATE explorations SET publication_status='sending',publication_started=?,role_id=? WHERE id=? AND publication_status='pending'",
                  (datetime.now(timezone.utc).isoformat(),str(role.id),eid))
        if c.rowcount!=1:return
    try:
        message=await asyncio.wait_for(channel.send(content=role.mention,embed=render(r),nonce=f'exploration-{eid}',allowed_mentions=discord.AllowedMentions(everyone=False,users=False,roles=[role],replied_user=False)),timeout=30)
    except (discord.Forbidden,discord.NotFound):
        with db.cursor() as c:c.execute("UPDATE explorations SET publication_status='pending' WHERE id=? AND publication_status='sending'",(eid,))
        raise
    # Other network failures are uncertain; history recovery above checks before retrying.
    mark_sent(eid,message.id)
