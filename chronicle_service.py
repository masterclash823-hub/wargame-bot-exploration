"""Daily public digest. No history scraping, AI invention or private action payloads."""
import json
from datetime import datetime,timezone,timedelta

import discord
import db
import i18n
from flags import flag_text,flagged_embed
from world_service import SCORES,GOALS,tr
from treaty_service import KINDS


def utcnow():return datetime.now(timezone.utc)


def configure(guild_id,channel_id,hour,language,now=None):
    if not 0<=hour<=23 or language not in ('pl','en'):raise ValueError('Invalid report settings')
    now=now or utcnow()
    due=now.replace(hour=hour,minute=0,second=0,microsecond=0)
    if due<=now:due+=timedelta(days=1)
    with db.atomic() as c:
        c.execute('INSERT INTO news_settings(guild_id,channel_id,hour_utc,language,next_due_at) VALUES(?,?,?,?,?) '
                  'ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,hour_utc=excluded.hour_utc,'
                  'language=excluded.language,next_due_at=excluded.next_due_at,enabled=1',
                  (str(guild_id),str(channel_id),hour,language,due.isoformat()))
    return due


def top_actions(c,until):
    start=until-timedelta(days=1)
    c.execute('SELECT x.*,n.name,n.flag,o.name AS other_name FROM world_activity x '
              'JOIN nations n ON n.id=x.nation_id LEFT JOIN nations o ON o.id=x.other_nation_id '
              'WHERE x.occurred_at>=? AND x.occurred_at<? ORDER BY x.id DESC',
              (start.isoformat(),until.isoformat()))
    rows=sorted(c.fetchall(),key=lambda x:(SCORES.get(x['kind'],0),x['id']),reverse=True)
    if not rows:return []
    first=rows[0]
    # Prefer a different nation when available; repeated building clicks cannot dominate.
    other=next((r for r in rows[1:] if r['nation_id']!=first['nation_id']),None)
    if other is None:other=next((r for r in rows[1:] if r['kind']!=first['kind']),None)
    if other is None and len(rows)>1:other=rows[1]
    return [first]+([other] if other else [])


def prepare_due(now=None):
    now=now or utcnow()
    with db.atomic() as c:
        c.execute('SELECT * FROM news_settings WHERE enabled=1 AND next_due_at<=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(now.isoformat(),))
        for settings in c.fetchall():
            first=datetime.fromisoformat(settings['next_due_at'])
            due=first+timedelta(days=(now-first).days)
            # Catch up one recent digest after downtime, rather than flooding old days.
            report=top_actions(c,due)
            c.execute('INSERT INTO news_deliveries(guild_id,slot,channel_id,language,report_json,updated_at) '
                      'VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,slot) DO NOTHING',
                      (settings['guild_id'],due.isoformat(),settings['channel_id'],settings['language'],json.dumps(report),now.isoformat()))
            c.execute('UPDATE news_settings SET next_due_at=? WHERE guild_id=?',((due+timedelta(days=1)).isoformat(),settings['guild_id']))


def claim(guild_id,slot,now=None):
    now=now or utcnow()
    with db.atomic() as c:
        c.execute('SELECT * FROM news_deliveries WHERE guild_id=? AND slot=?'+(' FOR UPDATE' if db.USE_POSTGRES else ''),(str(guild_id),slot))
        d=c.fetchone()
        if not d or d['status']!='pending':return None
        c.execute('SELECT * FROM news_settings WHERE guild_id=?',(str(guild_id),));s=c.fetchone()
        if not s or not s['enabled'] or s['channel_id']!=d['channel_id']:
            c.execute("UPDATE news_deliveries SET status='cancelled' WHERE guild_id=? AND slot=?",(str(guild_id),slot));return None
        c.execute("UPDATE news_deliveries SET status='sending',updated_at=? WHERE guild_id=? AND slot=?",(now.isoformat(),str(guild_id),slot))
        return d


def finish(d,status,message_id=None,error=''):
    with db.atomic() as c:
        c.execute("UPDATE news_deliveries SET status=?,message_id=?,error=?,updated_at=? WHERE guild_id=? AND slot=? AND (status!='sent' OR ?='sent')",
                  (status,str(message_id) if message_id else None,error[:250],utcnow().isoformat(),d['guild_id'],d['slot'],status))


def marker(slot):return 'Chronicle · '+datetime.fromisoformat(slot).strftime('%Y-%m-%d %H:%M UTC')


def render(d):
    lang=d['language'];pl=lang=='pl'
    e=discord.Embed(title='📰 Dziennik świata' if pl else '📰 World chronicle',color=discord.Color.gold())
    rows=json.loads(d['report_json'])
    e.description=('Dwa najciekawsze publiczne działania z ostatnich 24 godzin.' if pl else
                   'Two public highlights from the last 24 hours.') if len(rows)==2 else (
                   f'W ostatnich 24 godzinach zapisano {len(rows)} publicznych działań.' if pl else f'{len(rows)} public actions were recorded in the last 24 hours.')
    clean=lambda value:discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))
    for row in rows:
        p=json.loads(row['payload_json']);kind=row['kind'];other=clean(row.get('other_name') or '—')
        labels={
            'building':('Rozwój infrastruktury','Infrastructure development'),
            'colony':('Nowa kolonia','New colony'),'expansion':('Rozrost kolonii','Colonial expansion'),
            'research':('Postęp naukowy','Scientific progress'),'project':('Ukończony projekt','Project completed'),
            'event':('Rozstrzygnięte wydarzenie','Event concluded'),'battle':('Rozstrzygnięta bitwa','Battle resolved'),
            'war':('Wypowiedzenie wojny','War declared'),'treaty':('Podpisany traktat','Treaty signed'),
            'breach':('Naruszenie traktatu','Treaty broken'),'goal':('Osiągnięty cel państwowy','National goal achieved'),
            'guarantee':('Dotrzymana gwarancja','Guarantee honored'),
        }
        title=labels[kind][0 if pl else 1]
        detail=''
        if kind=='building':detail=f"{i18n.term(p['building'],lang)} · {p['level']}/3 · #{p['cell']}"
        elif kind in ('colony','expansion'):detail=('Prowincja ' if pl else 'Province ')+str(p['cell'])
        elif kind=='research':detail=('Uczeni ogłosili przełom. Szczegóły pozostają prywatne.' if pl else 'Scholars report a breakthrough. Details remain private.')
        elif kind=='goal':detail=GOALS[p['code']][0 if pl else 1]+(' · +10 prestiżu' if pl else ' · +10 prestige')
        elif kind in ('treaty','breach'):detail=KINDS[p['kind']][0 if pl else 1]+' · '+other
        elif kind in ('war','guarantee'):detail=other
        elif kind=='battle':detail=other+' · '+({'attacker':('wygrana atakującego','attacker victory'),'defender':('wygrana obrońcy','defender victory'),'draw':('remis','draw')}[p['winner']][0 if pl else 1])
        elif kind=='event':detail=('Zakończono trzy decyzje. Szczegóły wyborów pozostają prywatne.' if pl else 'Three decisions concluded. Choices remain private.')
        else:detail='🏛️'
        e.add_field(name=(title+' — '+flag_text(row['flag'])+' '+clean(row['name']))[:256],value=detail[:1000],inline=False)
    flagged_embed(e,*[(r['flag'],r['name']) for r in rows])
    e.set_footer(text=marker(d['slot']))
    return e


async def recover(bot,d):
    channel=bot.get_channel(int(d['channel_id']))
    if not channel:return False
    try:
        async for message in channel.history(limit=100,after=datetime.fromisoformat(d['slot'])-timedelta(minutes=1)):
            if message.author.id==bot.user.id and any(e.footer.text==marker(d['slot']) for e in message.embeds):
                finish(d,'sent',message.id);return True
    except (discord.HTTPException,AttributeError):pass
    return False


async def deliver(bot,d):
    import asyncio
    claimed=claim(d['guild_id'],d['slot'])
    if not claimed:return
    channel=bot.get_channel(int(claimed['channel_id']))
    if not channel:
        finish(claimed,'failed',error='Channel unavailable');return
    try:
        message=await asyncio.wait_for(channel.send(embed=render(claimed),allowed_mentions=discord.AllowedMentions.none()),timeout=20)
    except (discord.Forbidden,discord.NotFound) as exc:
        finish(claimed,'failed',error=type(exc).__name__)
    except Exception as exc:
        # A timeout may have occurred after Discord accepted the message. Never blindly resend.
        finish(claimed,'uncertain',error=type(exc).__name__)
    else:finish(claimed,'sent',message.id)


async def run_reports(bot,now=None):
    now=now or utcnow()
    prepare_due(now)
    with db.cursor() as c:
        c.execute("SELECT * FROM news_deliveries WHERE status='pending' OR (status IN ('sending','uncertain') AND updated_at<?)",((now-timedelta(minutes=5)).isoformat(),))
        deliveries=c.fetchall()
    for d in deliveries:
        if d['status']=='pending':await deliver(bot,d)
        elif not await recover(bot,d):finish(d,'uncertain',error='Check channel before manual retry')


def retry(guild_id,slot):
    with db.atomic() as c:
        c.execute("UPDATE news_deliveries SET status='pending',error='' WHERE guild_id=? AND slot=? AND status IN ('failed','uncertain')",(str(guild_id),slot))
        if not c.rowcount:raise ValueError(tr('Ten raport nie wymaga ponowienia.','This report does not need a retry.'))
