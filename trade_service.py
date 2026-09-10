"""Validated, atomic trade settlement for SQLite and PostgreSQL."""
import json
import math

import db
import i18n


def validate_gold(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(i18n.text('Amounts must be finite, non-negative numbers.'))


def parse_resources(raw):
    try:
        resources = i18n.game_json(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(i18n.text('Use a JSON resource object, e.g. {"wood":50}.')) from exc
    if not isinstance(resources, dict):
        raise ValueError(i18n.text('Use a JSON resource object, e.g. {"wood":50}.'))
    for key, amount in resources.items():
        if not key.strip() or key != key.strip().lower() or key == "gold":
            raise ValueError(i18n.text('Use lowercase resource names; enter gold in the gold field.'))
        validate_gold(amount)
    return resources


def accept_trade(trade_id, owner_id, is_gm=False, monthly=False):
    """Lock the trade and both balances, validate, then commit exactly once."""
    with db.atomic() as c:
        lock = " FOR UPDATE" if db.USE_POSTGRES else ""
        c.execute("SELECT * FROM trades WHERE id=?" + lock, (trade_id,))
        trade = c.fetchone()
        if not trade:
            raise ValueError(i18n.text('Trade #{p0} not found.', p0=trade_id))
        c.execute("SELECT * FROM nations WHERE id IN (?,?) ORDER BY id" + lock,
                  (trade["from_nation_id"], trade["to_nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
        fn = nations.get(trade["from_nation_id"])
        tn = nations.get(trade["to_nation_id"])
        if not fn or not tn or fn["id"] == tn["id"]:
            raise ValueError(i18n.text('Trade needs two different existing nations.'))
        if not is_gm and tn["owner_id"] != str(owner_id):
            raise ValueError(i18n.text('This trade is not addressed to your nation.'))
        if trade["status"] != "pending":
            raise ValueError(i18n.text('Trade #{p0} is already {p1}.', p0=trade_id, p1=i18n.term(trade['status'])))
        c.execute("SELECT * FROM trade_contracts WHERE trade_id=? AND status='proposed'",(trade_id,))
        contract=c.fetchone()
        if contract and not monthly:
            raise ValueError(i18n.text('Monthly contract: confirm with /trade accept monthly:True.'))
        if monthly and not contract:
            raise ValueError(i18n.text('The author must first mark this offer as monthly.'))
        give_res = parse_resources(trade["offer_resources_json"])
        recv_res = parse_resources(trade["receive_resources_json"])
        give_gold, recv_gold = trade["offer_gold"], trade["receive_gold"]
        validate_gold(give_gold)
        validate_gold(recv_gold)
        fn_res, tn_res = json.loads(fn["resources_json"]), json.loads(tn["resources_json"])
        for nation, balance, resources, gold in (
                (fn, fn_res, give_res, give_gold), (tn, tn_res, recv_res, recv_gold)):
            if nation["treasury"] < gold:
                raise ValueError(i18n.text('{p0} does not have enough gold.', p0=nation['name']))
            for resource, amount in resources.items():
                if balance.get(resource, 0) < amount:
                    raise ValueError(i18n.text('{p0} does not have enough {p1}.', p0=nation['name'], p1=i18n.term(resource)))
        for source, destination, resources in (
                (fn_res, tn_res, give_res), (tn_res, fn_res, recv_res)):
            for resource, amount in resources.items():
                source[resource] = source.get(resource, 0) - amount
                destination[resource] = destination.get(resource, 0) + amount
        for nation, resources, delta in (
                (fn, fn_res, recv_gold - give_gold), (tn, tn_res, give_gold - recv_gold)):
            c.execute("UPDATE nations SET resources_json=?,treasury=? WHERE id=?",
                      (json.dumps(resources), nation["treasury"] + delta, nation["id"]))
        c.execute("UPDATE trades SET status='accepted',resolved_at=CURRENT_TIMESTAMP WHERE id=?",
                  (trade_id,))
        if contract:
            from economy_engine import _config
            month=int(_config(c,'current_year','1'))*12+int(_config(c,'current_month','1'))-1
            c.execute("UPDATE trade_contracts SET status='active',last_month=? WHERE trade_id=?",(month,trade_id))
    return trade, fn, tn, give_res, recv_res, give_gold, recv_gold
