"""Wartime readiness shared by diplomacy, player actions and the calendar."""


def at_war(c,nid):
    c.execute("SELECT 1 FROM relations WHERE status='war' AND (nation_a_id=? OR nation_b_id=?) LIMIT 1",(nid,nid))
    return c.fetchone() is not None


def activate_wartime_reserves(c,nations=None):
    """Caller holds the world lock; activate reserves without changing deployments."""
    ids=tuple(sorted(set(nations))) if nations is not None else None
    if ids==():return 0
    scope=' AND u.nation_id IN ('+','.join('?' for _ in ids)+')' if ids is not None else ''
    c.execute("UPDATE military_posture SET mode='active',ready_month=0 "
              "WHERE mode IN ('reserve','mobilizing') AND unit_id IN ("
              "SELECT u.id FROM military_units u WHERE EXISTS (SELECT 1 FROM relations r "
              "WHERE r.status='war' AND (r.nation_a_id=u.nation_id OR r.nation_b_id=u.nation_id))"+scope+')',ids or ())
    return c.rowcount
