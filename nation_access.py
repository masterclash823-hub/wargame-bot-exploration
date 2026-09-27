"""One playable nation per account, with revocable cooperative access."""
import db


def find_nation(uid, cursor=None):
    if cursor is None:
        with db.cursor() as c:
            return find_nation(uid,c)
    cursor.execute('SELECT n.* FROM nations n WHERE n.owner_id=? OR EXISTS '
                   '(SELECT 1 FROM nation_coops co WHERE co.nation_id=n.id AND co.user_id=?)',
                   (str(uid),str(uid)))
    return cursor.fetchone()


def can_manage(nid,uid,cursor=None):
    if cursor is None:
        with db.cursor() as c:
            return can_manage(nid,uid,c)
    cursor.execute('SELECT n.id FROM nations n WHERE n.id=? AND '
                   '(n.owner_id=? OR EXISTS (SELECT 1 FROM nation_coops co WHERE co.nation_id=n.id AND co.user_id=?)) '
                   "AND NOT EXISTS (SELECT 1 FROM nation_decay d WHERE d.nation_id=n.id AND d.status='ruins')",
                   (nid,str(uid),str(uid)))
    return cursor.fetchone() is not None


def members(nid):
    with db.cursor() as c:
        c.execute('SELECT owner_id AS user_id FROM nations WHERE id=? UNION SELECT user_id FROM nation_coops WHERE nation_id=?',
                  (nid,nid))
        return [row['user_id'] for row in c.fetchall()]


def set_coop(nid,actor,player,*,remove=False,gm=False):
    from world_service import world_lock,tr
    from economy_engine import lock_nation
    with db.atomic() as c:
        world_lock(c)
        n=lock_nation(c,nid)
        if not gm and n['owner_id']!=str(actor):
            raise ValueError(tr('Coopem zarządza główny właściciel lub GM.', 'Only the primary owner or GM can manage co-op access.'))
        if str(player)==n['owner_id']:
            raise ValueError(tr('To główny właściciel państwa.', 'This is the primary nation owner.'))
        if remove:
            c.execute('DELETE FROM nation_coops WHERE nation_id=? AND user_id=?',(nid,str(player)))
            if not c.rowcount:raise ValueError(tr('Ten gracz nie jest coopem tego państwa.', 'This player is not a co-op member of this nation.'))
        else:
            if find_nation(player,c):
                raise ValueError(tr('Gracz jest już przypisany do państwa. Najpierw usuń poprzedni przydział.',
                                    'This player already belongs to a nation. Remove the previous assignment first.'))
            c.execute('INSERT INTO nation_coops(nation_id,user_id) VALUES(?,?)',(nid,str(player)))
        text=('Co-op removed: ' if remove else 'Co-op added: ')+str(player)
        c.execute("INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,'system',?)",(nid,text))
    return n
