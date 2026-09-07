import json
import os
import tempfile
import unittest

import db
from cogs.colonialism import (
    colony_advance_readiness,
    expand_colony,
    invest_in_colony,
    tick_colonies,
)
from cogs.provinces import _process_azgaar, _upsert_provinces


class ColonyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = os.path.join(self.tmp.name, "colonies.db")
        db.USE_POSTGRES = False
        db.init_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO nations(owner_id,name,treasury,tech_json) VALUES(?,?,?,?)",
                ("1", "Testland", 2000, json.dumps({"colonial": 5})),
            )
            self.nation_id = cur.lastrowid

    def tearDown(self):
        self.tmp.cleanup()

    def import_map(self):
        data = {
            "pack": {
                "biomes": {"name": ["Marine", "Grassland"]},
                "cells": [
                    {"i": 1, "biome": 1, "h": 30, "c": [2]},
                    {"i": 2, "biome": 1, "h": 30, "c": [1, 3]},
                    {"i": 3, "biome": 1, "h": 30, "c": [2]},
                ],
            }
        }
        provinces, error = _process_azgaar(data)
        self.assertIsNone(error)
        stats = _upsert_provinces(provinces, resync=True)
        self.assertEqual(stats["neighbor_links"], 4)
        return provinces

    def make_colony(self, cell_id=1, status="settlement", investment="{}", months=0):
        with db.cursor() as cur:
            cur.execute("SELECT id FROM provinces WHERE azgaar_cell_id=?", (cell_id,))
            province_id = cur.fetchone()["id"]
            cur.execute("UPDATE provinces SET owner_nation_id=? WHERE id=?", (self.nation_id, province_id))
            cur.execute(
                "INSERT INTO colonies(nation_id,province_id,name,status,investment_json,months_in_status) "
                "VALUES(?,?,?,?,?,?)",
                (self.nation_id, province_id, "Source", status, investment, months),
            )

    def test_import_saves_symmetric_adjacency_and_expansion_uses_it(self):
        self.import_map()
        self.make_colony()
        result = expand_colony(self.nation_id, 1, 2, "Frontier")
        self.assertEqual(result["cost"], 500)
        with db.cursor() as cur:
            cur.execute("SELECT treasury FROM nations WHERE id=?", (self.nation_id,))
            self.assertEqual(cur.fetchone()["treasury"], 1500)
            cur.execute(
                "SELECT c.status,p.owner_nation_id FROM colonies c JOIN provinces p ON p.id=c.province_id "
                "WHERE p.azgaar_cell_id=2"
            )
            row = cur.fetchone()
        self.assertEqual(row["status"], "outpost")
        self.assertEqual(row["owner_nation_id"], self.nation_id)

    def test_parallel_array_export_also_reads_neighbors(self):
        provinces, error = _process_azgaar({"pack": {"cells": {
            "i": [10, 11], "c": [[11], [10]], "biome": [0, 0], "h": [20, 20]
        }}})
        self.assertIsNone(error)
        self.assertEqual(provinces[0]["neighbors"], [11])
        self.assertEqual(provinces[1]["neighbors"], [10])

    def test_expansion_rejects_non_neighbor_and_outpost_source(self):
        self.import_map()
        self.make_colony(status="outpost")
        with self.assertRaises(ValueError):
            expand_colony(self.nation_id, 1, 2, "Too soon")
        with db.cursor() as cur:
            cur.execute("UPDATE colonies SET status='settlement'")
        with self.assertRaisesRegex(ValueError, "not adjacent"):
            expand_colony(self.nation_id, 1, 3, "Too far")

    def test_investment_rejects_negative_and_caps_overpayment(self):
        self.import_map()
        self.make_colony(status="outpost", investment=json.dumps({"gold": 250}))
        with self.assertRaises(ValueError):
            invest_in_colony(self.nation_id, 1, -100)
        result = invest_in_colony(self.nation_id, 1, 1000)
        self.assertEqual(result["applied"], 50)
        self.assertEqual(result["invested"], 300)
        with db.cursor() as cur:
            cur.execute("SELECT treasury FROM nations WHERE id=?", (self.nation_id,))
            self.assertEqual(cur.fetchone()["treasury"], 1950)

    def test_progress_and_advance_requirements(self):
        self.import_map()
        self.make_colony(status="outpost", investment=json.dumps({"gold": 300}), months=5)
        with db.cursor() as cur:
            cur.execute("SELECT * FROM colonies")
            colony = cur.fetchone()
            cur.execute("SELECT * FROM nations WHERE id=?", (self.nation_id,))
            nation = cur.fetchone()
        _, blocker = colony_advance_readiness(colony, nation)
        self.assertIn("5/6", blocker)
        promoted = tick_colonies(self.nation_id, 1)
        with db.cursor() as cur:
            cur.execute("SELECT * FROM colonies")
            colony = cur.fetchone()
        self.assertEqual(promoted[0]["to"], "settlement")
        self.assertEqual(colony["status"], "settlement")
        self.assertEqual(colony["months_in_status"], 0)
        self.assertEqual(json.loads(colony["investment_json"]), {})

    def test_payment_promotes_immediately_when_time_is_already_complete(self):
        self.import_map()
        self.make_colony(status="outpost", investment=json.dumps({"gold": 250}), months=8)
        result = invest_in_colony(self.nation_id, 1, 50)
        self.assertEqual(result["advanced_to"], "settlement")
        with db.cursor() as cur:
            cur.execute("SELECT status,months_in_status FROM colonies")
            colony = cur.fetchone()
        self.assertEqual(colony["status"], "settlement")
        self.assertEqual(colony["months_in_status"], 2)


if __name__ == "__main__":
    unittest.main()
