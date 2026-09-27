"""Transactional map exchange. Game ownership and economy stay authoritative by default."""
import copy
import json
from collections import Counter, defaultdict

import db
from azgaar_format import (decode, dumps, error, geometry_key, native_overlay,
                           normalize, write_native)
from world_service import world_lock


def _world(c):
    c.execute('SELECT * FROM azgaar_world WHERE id=1')
    return c.fetchone()


def prepare(raw, map_raw=None):
    source = decode(raw)
    native = decode(map_raw) if map_raw else None
    if native is not None and '_native_records' not in native:
        error('W polu map_file dodaj projekt .map.', 'Attach a .map project in map_file.')
    with db.cursor() as c:
        old = _world(c)
    if '_native_records' in source:
        if not old:
            error('Najpierw wgraj Full Data JSON razem z projektem .map.', 'Import Full Data JSON together with the .map project first.')
        native = source
        data = native_overlay(json.loads(old['data_json']), native)
    else:
        data = normalize(source)
        if native:
            native_overlay(copy.deepcopy(data), native)  # validate the pair; JSON is authoritative
    fingerprint = geometry_key(data)
    if old and old['geometry_hash'] != fingerprint:
        error('To inna geometria mapy. Nie można przepisać istniejącej gry na te same numery pól.',
              'This map has different geometry. Existing game cells cannot be remapped by reused IDs.')
    if not native and old and old['map_text']:
        native = decode(old['map_text'].encode())
        native_overlay(copy.deepcopy(data), native)
    return data, native, fingerprint


def _change_owners(c, targets):
    c.execute('SELECT id,azgaar_cell_id,owner_nation_id FROM provinces')
    changes = [(targets[p['azgaar_cell_id']], p['id']) for p in c.fetchall()
               if p['azgaar_cell_id'] in targets and p['owner_nation_id'] != targets[p['azgaar_cell_id']]]
    c.executemany('DELETE FROM province_labor WHERE province_id=?', [(pid,) for _, pid in changes])
    c.executemany('UPDATE colonies SET nation_id=? WHERE province_id=?', [(nid,pid) for nid,pid in changes if nid is not None])
    c.executemany('DELETE FROM colonies WHERE province_id=?', [(pid,) for nid,pid in changes if nid is None])
    c.executemany('UPDATE provinces SET owner_nation_id=? WHERE id=?', changes)
    return len(changes)


def import_map(raw, map_raw=None, *, resync=False, sync_owners=False):
    from cogs.provinces import _process_azgaar, _upsert_provinces
    from province_admin import totals
    data, native, fingerprint = prepare(raw, map_raw)
    provinces, message = _process_azgaar(data)
    if message:
        raise ValueError(message)
    with db.atomic() as c:
        world_lock(c)
        old = _world(c)
        if old and old['geometry_hash'] != fingerprint:
            error('Mapa zmieniła się w trakcie importu. Spróbuj ponownie.', 'The map changed during import. Try again.')
        c.execute('SELECT azgaar_cell_id,owner_nation_id FROM provinces')
        before = {p['azgaar_cell_id']: p['owner_nation_id'] for p in c.fetchall()}
        c.execute('SELECT * FROM azgaar_states')
        mapping = {s['state_id']: s for s in c.fetchall()}
        c.execute('SELECT id,name FROM nations')
        nations = c.fetchall()
        assigned = {s['nation_id'] for s in mapping.values() if s['nation_id']}
        for state in data['pack']['states']:
            sid = state['i']
            previous = mapping.get(sid)
            nid = previous['nation_id'] if previous else None
            linked = previous['linked'] if previous else 0
            if sid and not previous and not state.get('removed'):
                names = {str(state.get(k, '')).casefold() for k in ('name', 'fullName')} - {''}
                matches = [n for n in nations if n['name'].casefold() in names and n['id'] not in assigned]
                if len(matches) == 1:
                    nid, linked = matches[0]['id'], 1
                    assigned.add(nid)
            c.execute('INSERT INTO azgaar_states(state_id,nation_id,linked,data_json) VALUES(?,?,?,?) '
                      'ON CONFLICT(state_id) DO UPDATE SET data_json=excluded.data_json',
                      (sid, nid, linked, dumps(state)))
            mapping[sid] = dict(nation_id=nid, linked=linked)
        stats = _upsert_provinces(provinces, resync=resync, preserve_game=True)
        c.executemany('INSERT INTO azgaar_cells(cell_id,state_id,culture_id,religion_id) VALUES(?,?,?,?) '
                      'ON CONFLICT(cell_id) DO UPDATE SET state_id=excluded.state_id,culture_id=excluded.culture_id,religion_id=excluded.religion_id',
                      [(r['i'],r['state'],r['culture'],r['religion']) for r in data['pack']['cells']])
        # Existing game borders are only changed by the explicit sync_owners option.
        targets = {r['i']: mapping.get(r['state'], {}).get('nation_id') if r.get('h',0)>=20 else None
                   for r in data['pack']['cells'] if r['i'] not in before or sync_owners}
        changed = _change_owners(c, targets)
        for kind in ('cultures', 'religions'):
            c.execute('DELETE FROM azgaar_entities WHERE kind=?', (kind,))
            for entity in data['pack'][kind]:
                c.execute('INSERT INTO azgaar_entities(kind,entity_id,data_json) VALUES(?,?,?)', (kind, entity['i'], dumps(entity)))
        map_text = '\r\n'.join(native['_native_records']) if native else None
        c.execute('INSERT INTO azgaar_world(id,data_json,map_text,geometry_hash) VALUES(1,?,?,?) '
                  'ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,map_text=excluded.map_text,geometry_hash=excluded.geometry_hash',
                  (dumps(data), map_text, fingerprint))
        totals(c, [n['id'] for n in nations])
        stats.update(states=sum(bool(s['i']) and not s.get('removed', False) for s in data['pack']['states']),
                     cultures=len(data['pack']['cultures'])-1, religions=len(data['pack']['religions'])-1,
                     owners_changed=changed, native=bool(native),
                     pending=sum(bool(s['i']) and not s.get('removed', False) and not mapping[s['i']]['linked'] for s in data['pack']['states']))
        return stats


def bind(state_id, nation):
    """GM maps a source country to an existing GM-created nation. Never steal occupied cells."""
    from province_admin import totals
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM nations WHERE LOWER(name)=LOWER(?)', (nation,))
        n = c.fetchone()
        c.execute('SELECT * FROM azgaar_states WHERE state_id=?', (state_id,))
        state = c.fetchone()
        if not n or not state or not state_id or json.loads(state['data_json']).get('removed'):
            error('Nie znaleziono państwa gry lub państwa mapy.', 'Game nation or map state not found.')
        from nation_decay import require_playable
        require_playable(c, n['id'])
        if state['linked'] and state['nation_id'] != n['id']:
            error('To państwo mapy jest już powiązane (lub zostało usunięte z gry).', 'This map state is already linked (or was deleted from the game).')
        c.execute('SELECT state_id FROM azgaar_states WHERE nation_id=? AND state_id<>?', (n['id'], state_id))
        if c.fetchone():
            error('Państwo gry ma już inne ID mapy.', 'The game nation already has another map ID.')
        c.execute('UPDATE azgaar_states SET nation_id=?,linked=1 WHERE state_id=?', (n['id'], state_id))
        c.execute('SELECT p.azgaar_cell_id FROM provinces p JOIN azgaar_cells a ON a.cell_id=p.azgaar_cell_id '
                  'WHERE a.state_id=? AND p.active=1 AND p.owner_nation_id IS NULL AND p.terrain<>?', (state_id, 'water'))
        rows = c.fetchall()
        _change_owners(c, {p['azgaar_cell_id']: n['id'] for p in rows})
        totals(c, [n['id']])
        return len(rows)


def set_identity(cell_id, culture_id=None, religion_id=None):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT a.cell_id FROM azgaar_cells a JOIN provinces p ON p.azgaar_cell_id=a.cell_id WHERE a.cell_id=? AND p.active=1', (cell_id,))
        if not c.fetchone():
            error('Nie znaleziono pola zaimportowanej mapy.', 'Imported map cell not found.')
        for kind, value, column in (('cultures', culture_id, 'culture_id'), ('religions', religion_id, 'religion_id')):
            if value is None:
                continue
            c.execute('SELECT data_json FROM azgaar_entities WHERE kind=? AND entity_id=?', (kind, value))
            entity = c.fetchone()
            if not entity or json.loads(entity['data_json']).get('removed'):
                error('Nie ma takiej kultury lub religii. Sprawdź /admin map_entities.', 'Unknown culture or religion. Check /admin map_entities.')
            c.execute(f'UPDATE azgaar_cells SET {column}=? WHERE cell_id=?', (value, cell_id))


def identity(cell_id):
    with db.cursor() as c:
        c.execute('SELECT * FROM azgaar_cells WHERE cell_id=?', (cell_id,))
        row = c.fetchone()
        if not row:
            return {}
        result = {}
        for kind, key in (('cultures', 'culture_id'), ('religions', 'religion_id')):
            c.execute('SELECT data_json FROM azgaar_entities WHERE kind=? AND entity_id=?', (kind, row[key]))
            entity = c.fetchone()
            result[kind] = json.loads(entity['data_json']).get('name', '—') if entity else '—'
        c.execute('SELECT data_json FROM azgaar_states WHERE state_id=?', (row['state_id'],))
        entity = c.fetchone()
        result['states'] = json.loads(entity['data_json']).get('name', '—') if entity else '—'
        return result


def catalog(kind):
    with db.cursor() as c:
        if kind == 'states':
            c.execute('SELECT s.state_id,s.linked,s.data_json,n.name AS nation FROM azgaar_states s LEFT JOIN nations n ON n.id=s.nation_id ORDER BY s.state_id')
        elif kind in ('cultures', 'religions'):
            c.execute('SELECT entity_id AS state_id,data_json FROM azgaar_entities WHERE kind=? ORDER BY entity_id', (kind,))
        else:
            raise ValueError('Invalid catalog')
        return [dict(r, entity=json.loads(r['data_json'])) for r in c.fetchall() if not json.loads(r['data_json']).get('removed')]


def _state_record(n, sid):
    return dict(i=sid, name=n['name'], fullName=n['name'], color=f'#{(sid*2654435761) & 0xffffff:06x}',
                expansionism=1, capital=0, center=0, culture=0, type='Generic', form=n['government_type'],
                formName=n['government_type'], provinces=[], campaigns=[], military=[], diplomacy=[],
                coa={'t1': 'argent', 'shield': 'heater'}, salesTax=0, pollTax=0, treasury=0, alert=1)


def _reconcile(pack, capital_cells):
    """Reconcile Azgaar's burgs, capitals and administrative provinces after border changes."""
    cells = {r['i']: r for r in pack['cells']}
    groups = defaultdict(list)
    for cell in cells.values():
        groups[cell['state']].append(cell)
    burg_groups = defaultdict(list)
    for b in pack.get('burgs', []):
        if not isinstance(b, dict) or not b.get('i') or b.get('removed') or b.get('cell') not in cells:
            continue
        cell = cells[b['cell']]
        b['state'], b['culture'] = cell['state'], cell['culture']
        b['capital'] = 0
        burg_groups[b['state']].append(b)
    for state in pack['states']:
        sid = state['i']
        rows = groups[sid]
        burgs = burg_groups[sid]
        capital = next((b for b in burgs if b['cell'] == capital_cells.get(sid)), None)
        capital = capital or next((b for b in burgs if b['i'] == state.get('capital')), None) or next(iter(burgs), None)
        if sid and capital:
            capital['capital'] = 1
        state['capital'] = capital['i'] if sid and capital else 0
        if rows:
            center = next((r for r in rows if r['i'] == capital_cells.get(sid)), None)
            center = center or next((r for r in rows if capital and r['i'] == capital['cell']), None) or rows[0]
            state['center'] = center['i']
            if center.get('p'):
                state['pole'] = center['p']
            state['culture'] = Counter(r['culture'] for r in rows).most_common(1)[0][0]
        state.update(cells=len(rows), area=sum(r.get('area', 0) for r in rows),
                     rural=sum(r.get('pop', 0) for r in rows), urban=sum(b.get('population', 0) for b in burgs),
                     burgs=len(burgs), provinces=[])
        state['neighbors'] = sorted({cells[c]['state'] for r in rows for c in r.get('c', []) if c in cells and cells[c]['state'] != sid})
        # Bot military and diplomacy are private game data, not part of the map exchange.
        if state.get('_bot_linked'):
            state.pop('label', None)
            state['military'] = []
        state.pop('_bot_linked', None)
    provinces = pack.setdefault('provinces', [0])
    original = list(provinces)
    province_parts = defaultdict(lambda: defaultdict(list))
    for r in cells.values():
        if r.get('province') and r['state']:
            province_parts[r['province']][r['state']].append(r)
        elif not r['state']:
            r['province'] = 0
    for province in original:
        if not isinstance(province, dict) or not province.get('i') or province.get('removed'):
            continue
        parts = province_parts[province['i']]
        if not parts:
            province['removed'] = True
            continue
        for j, (sid, rows) in enumerate(sorted(parts.items(), key=lambda p: (-len(p[1]), p[0]))):
            part = province if j == 0 else copy.deepcopy(province)
            if j:
                part['i'] = len(provinces)
                provinces.append(part)
            part['state'] = sid
            part['center'] = rows[0]['i']
            part['burg'] = next((r.get('burg', 0) for r in rows if r.get('burg')), 0)
            for r in rows:
                r['province'] = part['i']
            pack['states'][sid]['provinces'].append(part['i'])
    for kind, key in (('cultures', 'culture'), ('religions', 'religion')):
        identities = defaultdict(list)
        for r in cells.values():
            identities[r[key]].append(r)
        for entity in pack[kind]:
            rows = identities[entity['i']]
            entity.update(cells=len(rows), area=sum(r.get('area', 0) for r in rows), rural=sum(r.get('pop', 0) for r in rows))
            if rows and entity.get('center') not in {r['i'] for r in rows}:
                entity['center'] = rows[0]['i']


def export_map(*, native_format=True, map_raw=None):
    with db.atomic() as c:
        world_lock(c)
        saved = _world(c)
        if not saved:
            error('Najpierw użyj /admin map_import z Full Data JSON.', 'Use /admin map_import with Full Data JSON first.')
        data = json.loads(saved['data_json'])
        native = decode(map_raw) if map_raw else decode(saved['map_text'].encode()) if saved['map_text'] else None
        if native_format and not native:
            error('Dodaj oryginalny projekt .map w parametrze file. Bot zapamięta go na kolejne eksporty.',
                  'Attach the original .map project in file. The bot will remember it for future exports.')
        if native:
            if '_native_records' not in native:
                error('Podaj projekt .map, nie JSON.', 'Provide a .map project, not JSON.')
            native_overlay(copy.deepcopy(data), native)
        c.execute('SELECT * FROM nations ORDER BY id')
        nations = c.fetchall()
        c.execute('SELECT * FROM azgaar_states ORDER BY state_id')
        states = {s['state_id']: s for s in c.fetchall()}
        linked = {s['nation_id']: sid for sid, s in states.items() if s['nation_id']}
        next_id = max(states, default=0) + 1
        for n in nations:
            if n['id'] in linked:
                continue
            if next_id > 65535:
                error('Przekroczono limit państw Azgaara.', 'Azgaar state limit exceeded.')
            record = _state_record(n, next_id)
            c.execute('INSERT INTO azgaar_states(state_id,nation_id,linked,data_json) VALUES(?,?,1,?)', (next_id, n['id'], dumps(record)))
            states[next_id] = dict(state_id=next_id, nation_id=n['id'], linked=1, data_json=dumps(record))
            linked[n['id']] = next_id
            next_id += 1
        pack = data['pack']
        pack['states'] = [json.loads(s['data_json']) for s in states.values()]
        # All IDs remain stable, including removed states (tombstones).
        by_nid = {n['id']: n for n in nations}
        for state in pack['states']:
            mapping = states[state['i']]
            n = by_nid.get(mapping['nation_id'])
            if n:
                state.update(name=n['name'], fullName=n['name'], formName=n['government_type'], removed=False, _bot_linked=True)
            elif mapping['linked']:
                state['removed'] = True
        for kind in ('cultures', 'religions'):
            c.execute('SELECT data_json FROM azgaar_entities WHERE kind=? ORDER BY entity_id', (kind,))
            pack[kind] = [json.loads(r['data_json']) for r in c.fetchall()]
        c.execute('SELECT p.*,a.state_id,a.culture_id,a.religion_id FROM provinces p JOIN azgaar_cells a ON a.cell_id=p.azgaar_cell_id')
        provinces = {p['azgaar_cell_id']: p for p in c.fetchall()}
        for row in pack['cells']:
            p = provinces.get(row['i'])
            if not p or not p['active']:
                row['state'] = 0
                continue
            sid = linked.get(p['owner_nation_id'])
            if sid is None:
                original = states.get(p['state_id'], {})
                sid = p['state_id'] if original and not original['linked'] and not json.loads(original['data_json']).get('removed') else 0
            row.update(state=sid, culture=p['culture_id'], religion=p['religion_id'])
        by_province_id = {p['id']: p for p in provinces.values()}
        capital_cells = {}
        for n in nations:
            p = by_province_id.get(n['capital_province_id'])
            if p and p['owner_nation_id'] == n['id']:
                capital_cells[linked[n['id']]] = p['azgaar_cell_id']
        _reconcile(pack, capital_cells)
        result = write_native(native, data) if native_format else dumps(data).encode()
        if map_raw:
            c.execute('UPDATE azgaar_world SET map_text=? WHERE id=1', ('\r\n'.join(native['_native_records']),))
        return result
