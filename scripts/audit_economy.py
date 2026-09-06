"""Read the code's economic behavior using isolated, disposable SQLite fixtures.

This is a diagnostic report, not a test suite asserting that bugs are desirable.
No production database or Discord connection is used.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("DISCORD_TOKEN", "offline-test")
os.environ.setdefault("GEMINI_API_KEY", "offline-test")
import db
from cogs import economy


def fixture():
    db.init_db()
    with db.cursor() as c:
        c.execute("INSERT INTO nations(owner_id,name,treasury,stability) VALUES(?,?,?,?)", ("1", "Audit", 100, 100))
        c.execute("INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population) VALUES(?,?,?)", (1, 1, 1000))


def snapshot():
    with db.cursor() as c:
        c.execute("SELECT resources_json,population,treasury FROM nations WHERE id=1")
        nation = dict(c.fetchone())
        c.execute("SELECT population FROM provinces WHERE id=1")
        nation["province_population"] = c.fetchone()["population"]
    nation["resources"] = json.loads(nation.pop("resources_json"))
    return nation


def pg_sql_on_sqlite(cur, sql, params=()):
    # Syntax-only bridge for reaching PG-specific paths; NOT a PG integration test.
    return original_execute(cur, sql.replace("%s", "?").replace("NOW()", "CURRENT_TIMESTAMP"), params)


original_execute = db._UnifiedCursor.execute
results = {}
with tempfile.TemporaryDirectory(prefix="economy-audit-") as temp, patch.object(db, "USE_POSTGRES", False):
    with patch.dict(os.environ, {"DB_PATH": str(Path(temp) / "seed.db")}):
        fixture()
        try:
            economy._seed_buildings()
        except Exception as exc:
            results["sqlite_seed_error"] = f"{type(exc).__name__}: {exc}"
        with patch.object(db._UnifiedCursor, "execute", pg_sql_on_sqlite):
            economy._seed_buildings()
            with db.cursor() as c:
                c.execute("UPDATE building_defs SET effect_json=? WHERE key='market'", ('{"gold":999}',))
            economy._seed_buildings()
            with db.cursor() as c:
                c.execute("SELECT effect_json FROM building_defs WHERE key='market'")
                results["market_after_reseed"] = json.loads(c.fetchone()["effect_json"])
    with patch.dict(os.environ, {"DB_PATH": str(Path(temp) / "inputs.db")}):
        fixture()
        with db.cursor() as c:
            c.execute("INSERT INTO building_defs(key,name,effect_json) VALUES(?,?,?)", ("foundry", "Foundry", '{"gunpowder":6,"iron":-2}'))
            c.execute("UPDATE provinces SET buildings_json=? WHERE id=1", ('["foundry"]',))
        economy._run_tick(1)
        results["production_without_inputs_and_population"] = snapshot()
    with patch.dict(os.environ, {"DB_PATH": str(Path(temp) / "mp_complete.db")}):
        fixture()
        with db.cursor() as c:
            c.execute("INSERT INTO megaprojects(nation_id,name,status,effect_json) VALUES(?,?,?,?)",
                      (1, "Completed", "complete", '{"gold_once":100,"stability":5,"resources_once":{"wood":10}}'))
        economy._run_tick(1)
        results["one_time_effect_keys_treated_as_resources"] = snapshot()["resources"]
    with patch.dict(os.environ, {"DB_PATH": str(Path(temp) / "mp_finish.db")}):
        fixture()
        with db.cursor() as c:
            c.execute("UPDATE provinces SET base_resources_json=? WHERE id=1", ('{"wood":20}',))
            c.execute("INSERT INTO megaprojects(nation_id,name,status,duration_months,effect_json) VALUES(?,?,?,?,?)",
                      (1, "Finishing", "building", 1, '{"resources_once":{"wood":10}}'))
        with patch.object(db._UnifiedCursor, "execute", pg_sql_on_sqlite):
            economy._run_tick(1)
        results["wood_on_completion_expected_30_actual"] = snapshot()["resources"].get("wood")
print(json.dumps(results, ensure_ascii=False, indent=2))
