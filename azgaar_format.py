"""Azgaar Full Data JSON and native .map codecs; never execute map content."""
import base64
import gzip
import hashlib
import io
import json
import math
import re
from urllib.parse import unquote

from world_service import tr

MAX_BYTES = 24 * 1024 * 1024
MAX_CELLS = 200_000
ENTITY_KEYS = ('states', 'cultures', 'religions')
NATIVE_ENTITIES = {'cultures': 13, 'states': 14, 'burgs': 15, 'religions': 29, 'provinces': 30}
NATIVE_CELLS = {'biome': 16, 'burg': 17, 'culture': 19, 'pop': 21, 'state': 25, 'religion': 26, 'province': 27}


def error(pl, en):
    raise ValueError(tr(pl, en))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def biome_ids(data):
    """Resolve biome names without inventing IDs or changing the map catalogue."""
    raw=data.get('pack',data).get('biomes',data.get('biomesData',{}))
    entries=raw.get('name',[]) if isinstance(raw,dict) else raw
    return {str(item.get('name','') if isinstance(item,dict) else item).strip().casefold():index
            for index,item in enumerate(entries)}


def decode(raw):
    if len(raw) > MAX_BYTES:
        error('Plik mapy może mieć najwyżej 24 MB.', 'Map files must be at most 24 MB.')
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            error('Rozpakowana mapa przekracza 24 MB.', 'Decompressed map exceeds 24 MB.')
    text = raw.decode('utf-8-sig').lstrip()
    if text.startswith('{'):
        data = json.loads(text, parse_constant=lambda _: error('Nieprawidłowa liczba w JSON.', 'Invalid JSON number.'))
        return data
    if not re.match(r'^\d+\.\d+[^|]*\|', text):
        try:
            text = unquote(base64.b64decode(text.strip(), validate=True).decode('utf-8'))
        except (ValueError, UnicodeError):
            error('Użyj Full Data JSON lub projektu .map z Azgaara.', 'Use Azgaar Full Data JSON or a .map project.')
    # SVG can contain ordinary line breaks. Replace only its line endings before splitting records.
    match = re.search(r'<svg\b[\s\S]*?</svg>', text)
    if not match:
        error('Projekt .map nie zawiera SVG.', 'The .map project has no SVG.')
    svg = match.group().replace('\r\n', '\n').replace('\r', '\n')
    placeholder = '\x00AZGAAR_SVG\x00'
    text = text[:match.start()] + placeholder + text[match.end():]
    records = text.replace('\r\n', '\n').split('\n')
    records = [svg if x == placeholder else x for x in records]
    if len(records) < 31 or not re.match(r'^\d+\.\d+[^|]*\|', records[0]) or records[5] != svg:
        error('Nieobsługiwany format projektu .map.', 'Unsupported .map project format.')
    return {'_native_records': records}


def cells(data):
    if not isinstance(data, dict) or not isinstance(data.get('pack', data), dict):
        error('Nieprawidłowa struktura mapy.', 'Invalid map structure.')
    pack = data.get('pack', data)
    raw = pack.get('cells')
    if isinstance(raw, list):
        result = raw
    elif isinstance(raw, dict) and isinstance(raw.get('i'), list):
        result = [{k: v[j] for k, v in raw.items() if isinstance(v, list) and j < len(v)}
                  for j in range(len(raw['i']))]
    else:
        error('Eksportuj w Azgaarze: Export → JSON → Full Data.', 'In Azgaar export: Export → JSON → Full Data.')
    if not result or len(result) > MAX_CELLS:
        error('Mapa jest pusta lub ma ponad 200 000 pól.', 'The map is empty or has over 200,000 cells.')
    ids = set()
    for row in result:
        if not isinstance(row, dict) or type(row.get('i')) is not int or not 0 <= row['i'] < MAX_CELLS or row['i'] in ids:
            error('Nieprawidłowe lub powtórzone ID pola.', 'Invalid or duplicate cell ID.')
        ids.add(row['i'])
        for key in ('state', 'culture', 'religion', 'biome', 'h'):
            if key in row and (type(row[key]) is not int or not 0 <= row[key] <= 65535):
                error('Nieprawidłowe dane pola: ' + key, 'Invalid cell field: ' + key)
        for key in ('pop',):
            if key in row and (not isinstance(row[key], (int, float)) or not math.isfinite(row[key]) or row[key] < 0):
                error('Nieprawidłowa populacja.', 'Invalid population.')
    return result


def normalize(data):
    if not isinstance(data, dict) or '_native_records' in data:
        error('Najpierw wgraj Full Data JSON, aby zapisać geografię.', 'Import Full Data JSON first to store the geography.')
    # Full Data and the older parallel-array export. Never accept Minimal/Grid Cells as a world.
    pack = data.get('pack', data)
    rows = cells(data)
    info, grid = data.get('info'), data.get('grid')
    if (not isinstance(info, dict) or not info.get('mapId') or info.get('seed') is None
            or not isinstance(grid, dict) or not grid.get('points') or not grid.get('cells')
            or any('p' not in r or 'c' not in r or 'h' not in r for r in rows)):
        error('Brakuje pełnej geometrii i identyfikatora świata. Użyj Export → JSON → Full Data.',
              'Full geometry and world identity are missing. Use Export → JSON → Full Data.')
    for kind, key in zip(ENTITY_KEYS, ('state', 'culture', 'religion')):
        entities = pack.get(kind)
        if not isinstance(entities, list) or not entities or len(entities) > 65536:
            error('Brak listy ' + kind + '. Użyj Full Data JSON.', 'Missing ' + kind + '. Use Full Data JSON.')
        valid = set()
        for i, entity in enumerate(entities):
            if i == 0 and not entity:
                entities[i] = {'i': 0, 'name': 'Unassigned'}
                entity = entities[i]
            if not isinstance(entity, dict) or entity.get('i', i) != i:
                error('Niespójne ID w liście ' + kind, 'Inconsistent IDs in ' + kind)
            entity['i'] = i
            if not entity.get('removed'):
                valid.add(i)
        for row in rows:
            if row.get(key, 0) not in valid:
                error('Pole wskazuje nieistniejącą pozycję: ' + kind, 'Cell references a missing entity: ' + kind)
            row.setdefault(key, 0)
    pack['cells'] = sorted(rows, key=lambda r: r['i'])
    if 'pack' not in data:
        data = {'pack': pack}
    return data


def geometry_key(data):
    rows = cells(data)
    # IDs alone are not enough: a regenerated world can reuse every one of them.
    identity = [[r['i'], r.get('p'), r.get('g'), r.get('h'), r.get('c')] for r in rows]
    info = data.get('info', {})
    payload = [info.get('mapId'), info.get('seed'), identity]
    return hashlib.sha256(dumps(payload).encode()).hexdigest()


def native_overlay(data, native):
    """Native saves omit packed geometry. Reuse verified Full Data geometry, not a guessed graph."""
    records = native['_native_records']
    header = records[0].split('|')
    info = data.get('info', {})
    grid = data.get('grid', {})
    stored_grid = json.loads(records[6])
    if len(header) < 7 or not info.get('mapId') or str(info['mapId']) != header[6] or str(info.get('seed')) != header[3]:
        error('Projekt .map i JSON muszą pochodzić z tej samej mapy.', 'The .map project and JSON must come from the same map.')
    if not grid.get('points') or grid['points'] != stored_grid.get('points') or grid.get('boundary') != stored_grid.get('boundary'):
        error('Brak zgodnej siatki. Wgraj Full Data JSON z tego samego zapisu mapy.', 'No matching grid. Import Full Data JSON from the same map save.')
    grid_rows = cells({'cells': grid.get('cells')})
    if sorted(r['i'] for r in grid_rows) != list(range(len(grid_rows))):
        error('Niekompletna siatka mapy.', 'Incomplete map grid.')
    for key, index in (('h', 7), ('t', 10)):
        values = [int(x) for x in records[index].split(',')]
        if len(values) != len(grid_rows) or any(r.get(key) != values[r['i']] for r in grid_rows):
            error('Zmieniono geografię mapy. Użyj projektu zgodnego z zaimportowanym JSON.', 'Map geography changed. Use a project matching the imported JSON.')
    if len(records) > 51 and records[51] and json.loads(records[51]):
        error('Mapa ma niestandardową siatkę GraphOverride; eksport .map nie jest jeszcze obsługiwany.', 'This map has a custom GraphOverride graph; .map exchange is not supported yet.')
    pack = data['pack']
    rows = pack['cells']
    if [r['i'] for r in rows] != list(range(len(rows))):
        error('Projekt .map wymaga kompletnej listy pól.', 'The .map project requires a complete cell list.')
    for key, index in NATIVE_CELLS.items():
        values = [float(x) if key == 'pop' else int(x) for x in records[index].split(',')]
        if len(values) != len(rows):
            error('Liczba pól .map nie zgadza się z JSON.', '.map cell count does not match JSON.')
        for row, value in zip(rows, values):
            row[key] = value
    for kind, index in NATIVE_ENTITIES.items():
        pack[kind] = json.loads(records[index])
    return normalize(data)


def write_native(native, data):
    records = list(native['_native_records'])
    for kind, index in NATIVE_ENTITIES.items():
        records[index] = dumps(data['pack'][kind])
    for key, index in NATIVE_CELLS.items():
        records[index] = ','.join(str(r.get(key, 0)) for r in data['pack']['cells'])
    return '\r\n'.join(records).encode('utf-8')
