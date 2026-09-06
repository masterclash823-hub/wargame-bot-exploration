"""Validated, atomic trade settlement for SQLite and PostgreSQL."""
import json
import math

import db


def validate_gold(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError("Amounts must be finite, non-negative numbers.")


def parse_resources(raw):
    try:
        resources = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Use a JSON resource object, e.g. {"wood":50}.') from exc
    if not isinstance(resources, dict):
        raise ValueError('Use a JSON resource object, e.g. {"wood":50}.')
    for key, amount in resources.items():
        if not key.strip() or key != key.strip().lower() or key == "gold":
            raise ValueError("Use lowercase resource names; enter gold in the gold field.")
        validate_gold(amount)
    return resources


def accept_trade(trade_id, owner_id, is_gm=False):
    """Lock the trade and both balances, validate, then commit exactly once."""
    with db.cursor() as c:
        if not db.USE_POSTGRES:
            c.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if db.USE_POSTGRES else ""
        c.execute("SELECT * FROM trades WHERE id=?" + lock, (trade_id,))
        trade = c.fetchone()
        if not trade:
            raise ValueError(f"Trade #{trade_id} not found.")
        c.execute("SELECT * FROM nations WHERE id IN (?,?) ORDER BY id" + lock,
                  (trade["from_nation_id"], trade["to_nation_id"]))
        nations = {n["id"]: n for n in c.fetchall()}
        fn = nations.get(trade["from_nation_id"])
        tn = nations.get(trade["to_nation_id"])
        if not fn or not tn or fn["id"] == tn["id"]:
            raise ValueError("Trade needs two different existing nations.")
        if not is_gm and tn["owner_id"] != str(owner_id):
            raise ValueError("This trade is not addressed to your nation.")
        if trade["status"] != "pending":
            raise ValueError(f"Trade #{trade_id} is already {trade['status']}.")
        give_res = parse_resources(trade["offer_resources_json"])
        recv_res = parse_resources(trade["receive_resources_json"])
        give_gold, recv_gold = trade["offer_gold"], trade["receive_gold"]
        validate_gold(give_gold)
        validate_gold(recv_gold)
        fn_res, tn_res = json.loads(fn["resources_json"]), json.loads(tn["resources_json"])
        for nation, balance, resources, gold in (
                (fn, fn_res, give_res, give_gold), (tn, tn_res, recv_res, recv_gold)):
            if nation["treasury"] < gold:
                raise ValueError(f"{nation['name']} does not have enough gold.")
            for resource, amount in resources.items():
                if balance.get(resource, 0) < amount:
                    raise ValueError(f"{nation['name']} does not have enough {resource}.")
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
    return trade, fn, tn, give_res, recv_res, give_gold, recv_gold
