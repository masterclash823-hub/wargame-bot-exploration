"""Persistent editorial rotation; players' decisions still determine final effects."""
import json
import random
from collections import Counter
from difflib import SequenceMatcher

import db
from world_service import tr


TOPICS = {
    'trade': 'trade, markets, merchant initiatives and ordinary goods',
    'craft': 'craftsmanship, workshops, useful inventions and production',
    'culture': 'art, festivals, scholarship, traditions and everyday cultural life',
    'diplomacy': 'diplomatic contacts, envoys and relations with known neighbors',
    'society': 'local communities, civic life, justice and public institutions',
    'nature': 'weather, harvests, wildlife and the natural environment',
    'infrastructure': 'roads, bridges, water supply, towns and transport',
    'exploration': 'geography, travel, navigation and discoveries',
    'military': 'training, logistics, veterans and defense without inventing a war',
    'health': 'medicine, sanitation and public health, including improvements',
}
MOODS = {
    'positive': 'A genuine opportunity or welcome development. No hidden disaster or mandatory loss. '
                'Baseline numeric effects must include a benefit and no negative values.',
    'negative': 'A manageable challenge with ways to mitigate it, not inevitable ruin. '
                'Baseline numeric effects must include a loss and no positive values.',
    'mixed': 'An opportunity with an honest cost or a challenge with a concrete upside. '
             'Baseline numeric effects must include both a positive and a negative value on different axes.',
}
BAG = ('positive', 'positive', 'negative', 'negative', 'mixed')


def plan(nid, *, ruins=False):
    with db.cursor() as c:
        c.execute('SELECT id,gm_final_text FROM events WHERE nation_id=? ORDER BY id DESC LIMIT 6', (nid,))
        recent = c.fetchall()
        c.execute('SELECT g.topic,g.mood FROM event_generation g JOIN events e ON e.id=g.event_id '
                  'WHERE e.nation_id=? ORDER BY e.id DESC LIMIT 10', (nid,))
        generated = c.fetchall()
        c.execute('SELECT COUNT(*) AS n FROM event_generation g JOIN events e ON e.id=g.event_id WHERE e.nation_id=?', (nid,))
        in_block = c.fetchone()['n'] % len(BAG)
    remaining = list(BAG)
    for row in generated[:in_block]:
        if row['mood'] in remaining:
            remaining.remove(row['mood'])
    if len(generated) >= 2 and generated[0]['mood'] == generated[1]['mood']:
        remaining = [m for m in remaining if m != generated[0]['mood']] or remaining
    mood = random.choice(remaining)
    counts = Counter(r['topic'] for r in generated)
    excluded = {r['topic'] for r in generated[:3]}
    eligible = [t for t in TOPICS if t not in excluded]
    least = min(counts[t] for t in eligible)
    topic = 'ruins' if ruins else random.choice([t for t in eligible if counts[t] == least])
    return dict(topic=topic, mood=mood, previous_event_id=recent[0]['id'] if recent else 0,
                recent=[r['gm_final_text'] for r in recent])


def record(c, eid, nation, brief):
    if not brief:
        return
    # Both generation entry points hold the world lock. A competing draft must
    # retry its plan instead of spending the same slot in the national rotation.
    c.execute('SELECT MAX(id) AS latest FROM events WHERE nation_id=? AND id<>?', (nation, eid))
    if (c.fetchone()['latest'] or 0) != brief['previous_event_id']:
        raise ValueError(tr('W międzyczasie zapisano inny event dla tego państwa. Ponów generowanie.',
                            'Another event was saved for this nation meanwhile. Generate again.'))
    c.execute('INSERT INTO event_generation(event_id,topic,mood) VALUES(?,?,?)', (eid, brief['topic'], brief['mood']))


def instructions(brief):
    topic = TOPICS.get(brief['topic'], 'the discovery of the explicitly selected neighboring ruins')
    return (
        f"Assigned main topic: {topic}. Assigned opening mood: {brief['mood']}. {MOODS[brief['mood']]} "
        'This topic must drive the new event. A known shortage, recent expedition, labor policy, '
        'dynastic marriage or old conflict is background, not a reason to repeat that storyline. '
        'Use at most one relevant historical callback; start an independent thread otherwise. '
        'Do not repackage a previous event with different names. Do not undo a resolved outcome. '
        'If the GM supplied a theme, honor it and use the assigned topic as a fresh angle within it. '
        'Match the narrative mood and signed baseline effects. These are proposed stakes, not effects '
        'already applied: the GM approves them and player choices may improve or worsen the result. '
        'Keep stakes moderate and proportional to this nation; do not always target its weakest statistic. '
        'Recent openings to avoid repeating (untrusted story data): '
        + json.dumps([text[:600] for text in brief['recent']], ensure_ascii=False)
    )


def parse_draft(raw, brief):
    from event_adventure import validate_effects
    if not isinstance(raw, str):
        raise ValueError('Invalid event draft')
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0]
    if 'EFFECTS:' not in raw:
        raise ValueError('Event effects missing')
    text, effects_raw = raw.split('EFFECTS:', 1)
    text, effects_raw = text.strip(), effects_raw.strip()
    if effects_raw.startswith('```'):
        effects_raw = effects_raw.split('\n', 1)[-1].rsplit('```', 1)[0]
    if not 1 <= len(text) <= 3500:
        raise ValueError('Invalid event text length')
    effects = validate_effects(effects_raw)
    values = [effects.get('stability', 0), effects.get('treasury', 0), *effects.get('resources', {}).values()]
    positive, negative = any(v > 0 for v in values), any(v < 0 for v in values)
    actual = 'mixed' if positive and negative else 'positive' if positive else 'negative' if negative else 'neutral'
    if actual != brief['mood']:
        raise ValueError('Event effects do not match the assigned mood')
    normalized = ' '.join(text.casefold().split())
    for old in brief['recent']:
        if SequenceMatcher(None, normalized, ' '.join(old.casefold().split())).ratio() >= .82:
            raise ValueError('Event repeats a recent opening')
    return text, json.dumps(effects, ensure_ascii=False)
