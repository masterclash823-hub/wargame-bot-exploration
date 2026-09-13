"""Consensual diplomatic proposals concerning adult fictional dynasty members."""
import db
import i18n
from world_service import world_lock, owned, month_index, tr

PEOPLE = {'self': ('Władca / władczyni', 'Ruler'),
          'son': ('Dorosły syn', 'Adult son'), 'daughter': ('Dorosła córka', 'Adult daughter')}


def person(key):
    return tr(*PEOPLE[key])


def current(c, tid):
    c.execute("SELECT * FROM dynastic_marriages WHERE treaty_id=? AND status IN ('proposed','active') ORDER BY id DESC", (tid,))
    return c.fetchone()


def alliance(c, tid):
    c.execute('SELECT * FROM treaties WHERE id=?', (tid,))
    t = c.fetchone()
    if not t or t['kind'] != 'alliance' or t['status'] != 'active' or t['expires_month'] <= month_index(c):
        raise ValueError(tr('Mariaż wymaga aktywnego sojuszu.', 'A marriage requires an active alliance.'))
    return t


def available(c, nid, slot):
    c.execute("SELECT m.id FROM dynastic_marriages m JOIN treaties t ON t.id=m.treaty_id "
              "WHERE m.status='active' AND t.status='active' AND t.expires_month>? AND "
              '((m.proposer_id=? AND m.proposer_person=?) OR (m.recipient_id=? AND m.recipient_person=?))',
              (month_index(c), nid, slot, nid, slot))
    if c.fetchone():
        raise ValueError(tr('Ta postać jest już związana aktywnym mariażem.', 'This person already has an active dynastic marriage.'))


def propose(tid, uid, own_person, other_person):
    if own_person not in PEOPLE or other_person not in PEOPLE:
        raise ValueError(tr('Wybierz władcę, dorosłego syna lub dorosłą córkę.', 'Choose the ruler, an adult son or an adult daughter.'))
    with db.atomic() as c:
        world_lock(c)
        t = alliance(c, tid)
        c.execute('SELECT * FROM nations WHERE id IN (?,?) ORDER BY id', (t['proposer_id'], t['recipient_id']))
        parties = c.fetchall()
        a = next((n for n in parties if n['owner_id'] == str(uid)), None)
        if not a:
            raise ValueError(tr('Nie jesteś stroną sojuszu.', 'You are not an alliance party.'))
        b = next(n for n in parties if n['id'] != a['id'])
        owned(c, a['id'], uid)
        if current(c, tid):
            raise ValueError(tr('Sojusz ma już mariaż lub oczekującą propozycję.', 'This alliance already has a marriage or pending proposal.'))
        available(c, a['id'], own_person); available(c, b['id'], other_person)
        return db.insert_returning_id('INSERT INTO dynastic_marriages(treaty_id,proposer_id,recipient_id,proposer_owner,recipient_owner,proposer_person,recipient_person,created_month) VALUES(?,?,?,?,?,?,?,?)',
                                     (tid, a['id'], b['id'], a['owner_id'], b['owner_id'], own_person, other_person, month_index(c)))


def answer(mid, uid, accept):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT * FROM dynastic_marriages WHERE id=?', (mid,))
        m = c.fetchone()
        if not m or m['status'] != 'proposed' or m['created_month'] + 6 <= month_index(c):
            raise ValueError(tr('Propozycja nie jest już aktualna.', 'This proposal is no longer pending.'))
        t = alliance(c, m['treaty_id'])
        c.execute('SELECT * FROM nations WHERE id IN (?,?)', (m['proposer_id'], m['recipient_id']))
        parties = {n['id']: n for n in c.fetchall()}
        for prefix in ('proposer', 'recipient'):
            if parties[m[prefix+'_id']]['owner_id'] != m[prefix+'_owner']:
                raise ValueError(tr('Zmienił się właściciel państwa. Przygotuj nową propozycję.', 'Nation ownership changed. Prepare a new proposal.'))
        if str(uid) != m['recipient_owner'] and (accept or str(uid) != m['proposer_owner']):
            raise ValueError(tr('Tylko odbiorca może zaakceptować mariaż.', 'Only the recipient may accept the marriage.'))
        if accept:
            for prefix in ('proposer', 'recipient'):
                available(c, m[prefix+'_id'], m[prefix+'_person'])
        c.execute('UPDATE dynastic_marriages SET status=? WHERE id=?', ('active' if accept else 'rejected', mid))
        if accept:
            c.execute('UPDATE treaties SET version=version+1 WHERE id=?',(t['id'],))
            for prefix, other in (('proposer', 'recipient'), ('recipient', 'proposer')):
                n = parties[m[prefix+'_id']]
                with i18n.using_language(i18n.get_user_language(n['owner_id'])):
                    entry = tr('Mariaż dynastyczny: ', 'Dynastic marriage: ') + person(m[prefix+'_person']) + ' ↔ ' + person(m[other+'_person']) + ' (' + parties[m[other+'_id']]['name'] + ').'
                c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)', (n['id'], 'diplomacy_private', entry))
            if t['visibility'] == 'public':
                from world_service import activity
                activity(c, 'marriage', m['proposer_id'], f'marriage:{mid}', other=m['recipient_id'])


def stability_bonus(c, nid):
    c.execute("SELECT COUNT(*) AS n FROM dynastic_marriages m JOIN treaties t ON t.id=m.treaty_id "
              "WHERE m.status='active' AND t.status='active' AND t.expires_month>? AND (m.proposer_id=? OR m.recipient_id=?)",
              (month_index(c), nid, nid))
    return min(.5, .25 * c.fetchone()['n'])


def tick(c, month):
    c.execute("UPDATE dynastic_marriages SET status='ended' WHERE status IN ('active','proposed') AND treaty_id IN "
              "(SELECT id FROM treaties WHERE status!='active' OR expires_month<=?)", (month,))
    c.execute("UPDATE dynastic_marriages SET status='expired' WHERE status='proposed' AND created_month+6<=?", (month,))
