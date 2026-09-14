"""Atomic cleanup for the GM's confirmed nation deletion."""
import db
from world_service import world_lock


def delete_nation(nation_id):
    with db.atomic() as c:
        world_lock(c)
        c.execute('SELECT id FROM nations WHERE id=?',(nation_id,))
        if not c.fetchone():
            return False
        # Battles restrict deletion of their plans, unlike other nation children.
        c.execute('SELECT b.id,b.status,b.plan_a_id,b.plan_b_id FROM battles b '
                  'JOIN battle_plans a ON a.id=b.plan_a_id '
                  'JOIN battle_plans z ON z.id=b.plan_b_id '
                  'WHERE a.nation_id=? OR z.nation_id=?',(nation_id,nation_id))
        battles=c.fetchall()
        pending_plans=set()
        for battle in battles:
            if battle['status']=='pending':
                pending_plans.update((battle['plan_a_id'],battle['plan_b_id']))
            c.execute('DELETE FROM battles WHERE id=?',(battle['id'],))
        # Surviving opponents may submit their unmatched plans again.
        for plan_id in pending_plans:
            c.execute("UPDATE battle_plans SET status='unmatched' WHERE id=? AND nation_id<>? "
                      "AND status='matched' AND NOT EXISTS "
                      "(SELECT 1 FROM battles WHERE plan_a_id=? OR plan_b_id=?)",
                      (plan_id,nation_id,plan_id,plan_id))
        c.execute('UPDATE provinces SET owner_nation_id=NULL WHERE owner_nation_id=?',(nation_id,))
        c.execute('DELETE FROM nations WHERE id=?',(nation_id,))
        return True
