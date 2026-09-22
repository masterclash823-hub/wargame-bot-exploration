"""Prepare one missing draft per playable nation; never publish or apply effects."""
import asyncio
import json
import logging

import db
import i18n
import event_variety
from event_adventure import validate_effects
from nation_decay import require_playable
from world_service import world_lock


def candidate(nid):
    with db.cursor() as c:
        c.execute('SELECT * FROM nations WHERE id=?',(nid,))
        n=c.fetchone()
        if not n: raise ValueError('Nation no longer exists')
        require_playable(c,nid)
        c.execute("SELECT id FROM events WHERE nation_id=? AND status='draft' ORDER BY id DESC LIMIT 1",(nid,))
        existing=c.fetchone()
        n['event_language']=i18n.get_user_language(n['owner_id'])
        if not existing:n['event_brief']=event_variety.plan(nid)
        return n,existing['id'] if existing else None


def save_missing(nation,text,effects):
    if not isinstance(text,str) or not text.strip() or len(text)>4000:
        raise ValueError('Invalid event text')
    effects=json.dumps(validate_effects(effects),ensure_ascii=False)
    with db.atomic() as c:
        world_lock(c)
        require_playable(c,nation['id'])
        c.execute('SELECT owner_id FROM nations WHERE id=?',(nation['id'],))
        current=c.fetchone()
        if not current or current['owner_id']!=nation['owner_id'] or i18n.get_user_language(current['owner_id'])!=nation['event_language']:
            raise ValueError('Nation ownership or language changed during generation')
        # Also protects against another process or a GM creating a draft meanwhile.
        c.execute("SELECT id FROM events WHERE nation_id=? AND status='draft' ORDER BY id DESC LIMIT 1",(nation['id'],))
        existing=c.fetchone()
        if existing:return existing['id'],'existing'
        eid=db.insert_returning_id("INSERT INTO events(nation_id,ai_draft_text,gm_final_text,effects_json,status) VALUES(?,?,?,?,'draft')",
                                   (nation['id'],text.strip(),text.strip(),effects))
        event_variety.record(c,eid,nation['id'],nation.get('event_brief'))
        return eid,'created'


async def generate_all(generate,theme='',progress=None):
    with db.cursor() as c:
        c.execute("SELECT n.id,n.name FROM nations n WHERE NOT EXISTS (SELECT 1 FROM nation_decay d WHERE d.nation_id=n.id AND d.status='ruins') ORDER BY n.id")
        nations=c.fetchall()
    results=[None]*len(nations)
    queue=asyncio.Queue()
    for i,n in enumerate(nations):queue.put_nowait((i,n))
    done=0
    async def worker():
        nonlocal done
        while not queue.empty():
            i,entry=queue.get_nowait()
            result=dict(entry,status='failed',event_id=None)
            try:
                n,existing=await asyncio.to_thread(candidate,entry['id'])
                if existing:
                    result.update(status='existing',event_id=existing)
                else:
                    with i18n.using_language(n['event_language']):
                        text,effects=await asyncio.wait_for(generate(n,theme=theme,strict=True),timeout=70)
                    eid,status=await asyncio.to_thread(save_missing,n,text,effects)
                    result.update(status=status,event_id=eid)
            except Exception:
                logging.exception('Event draft generation failed for nation %s',entry['id'])
            results[i]=result
            done+=1
            if progress:await progress(done,len(nations))
    # Bound parallel API requests while allowing one country's failure to be retried.
    await asyncio.gather(*(worker() for _ in range(min(2,len(nations)))))
    return results
