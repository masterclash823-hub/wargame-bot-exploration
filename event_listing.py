"""Compact event index, with visibility checked before pagination."""
import discord
import db
from utils import short_date

PAGE_SIZE=15
STATUS={'draft':'📝','posted':'📜','active':'🎲','resolved':'✅'}


def rows(target_id,is_gm,owner_nation_id):
    sql="SELECT e.id,e.nation_id,e.status,e.posted_at,e.created_at,n.name AS nname," \
        "SUBSTR(COALESCE(NULLIF(e.gm_final_text,''),e.ai_draft_text,''),1,201) AS gm_final_text " \
        "FROM events e JOIN nations n ON n.id=e.nation_id " \
        "LEFT JOIN event_publications p ON p.event_id=e.id WHERE 1=1"
    args=[]
    if target_id is not None:
        sql+=' AND e.nation_id=?';args.append(target_id)
    if not is_gm:
        sql+=" AND e.status IN ('posted','active','resolved') AND (e.nation_id=? OR COALESCE(p.visibility,'public')<>'private')"
        args.append(owner_nation_id if owner_nation_id is not None else -1)
    with db.cursor() as c:
        c.execute(sql+' ORDER BY e.id DESC',tuple(args))
        return c.fetchall()


def pages(rows,lang,is_gm):
    title=('📜 Wydarzenia' if lang=='pl' else '📜 Events')+((' — Widok GM' if lang=='pl' else ' — GM View') if is_gm else '')
    hint='/event edit · /event effects · /event post' if is_gm else '/event play'
    def empty():return discord.Embed(title=title,description=hint,color=discord.Color.purple())
    result=[];embed=empty()
    for r in rows:
        date=short_date(r['posted_at'] or r['created_at'])
        name=discord.utils.escape_markdown(' '.join(r['nname'].split()))[:150]
        label=f"{STATUS.get(r['status'],'❓')} #{r['id']} — {name} ({date})"[:256]
        text=' '.join((r['gm_final_text'] or '').split())
        text=discord.utils.escape_markdown(text[:180])+('…' if len(text)>180 else '')
        text=text or '—'
        if len(embed.fields)>=PAGE_SIZE or len(embed)+len(label)+len(text)>5500:
            result.append(embed);embed=empty()
        embed.add_field(name=label,value=text,inline=False)
    if embed.fields:result.append(embed)
    return result
