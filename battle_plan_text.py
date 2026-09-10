"""Lossless plan storage and private downloadable copies."""
import io
import json
import discord

MAX_ORDERS = 100_000
MAX_FILE_BYTES = MAX_ORDERS * 4 + 3


def unpack(plan):
    raw = str(plan['orders_text'] or '')
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get('format') == 'battle_plan_v2':
            return {key: data[key] for key in ('orders_text', 'location_text', 'forces_note')}
    except (ValueError, TypeError, KeyError):
        pass
    locations = json.loads(plan['provinces_json'] or '[]')
    location = str(locations[0]) if locations else ''
    # Legacy suffix: split at the stored location, not at player-authored text.
    marker = f' | Location: {location} | Forces:'
    orders, sep, note = raw.rpartition(marker)
    return dict(orders_text=orders if sep else raw, location_text=location,
                forces_note=note.strip() if sep else '')


def pack(orders, location, note):
    return json.dumps(dict(format='battle_plan_v2', orders_text=orders,
                           location_text=location, forces_note=note), ensure_ascii=False)


def plan_file(plan_id, data):
    text = f"Plan #{plan_id}\nLocation: {data['location_text']}\nForces: {data['forces_note']}\n\n{data['orders_text']}"
    return discord.File(io.BytesIO(text.encode('utf-8')), filename=f'battle-plan-{plan_id}.txt')
