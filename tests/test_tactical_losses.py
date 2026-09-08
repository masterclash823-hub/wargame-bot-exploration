import math
import random
import unittest

from battle_resolution import allocate_losses, normalize_exposure, _entries


class AllocationTests(unittest.TestCase):
    def test_frontline_takes_losses_before_sheltered_reserve(self):
        rows = allocate_losses([(1, 10, 20), (2, 10, 10)], 30,
            {'1': {'weight': 4, 'reason': 'Frontline assault'},
             '2': {'weight': .25, 'reason': 'Sheltered reserve'}})
        self.assertEqual([r['lost'] for r in rows], [6, 0])
        self.assertEqual(rows[1]['reason'], 'Sheltered reserve')

    def test_capped_frontline_redistributes_remaining_losses(self):
        rows = allocate_losses([(1, 1, 1), (2, 10, 10)], 80,
            {'1': {'weight': 4, 'reason': 'Exposed'}, '2': {'weight': .25, 'reason': 'Reserve'}})
        self.assertEqual([r['lost'] for r in rows], [1, 8])

    def test_rounding_is_once_per_side(self):
        rows = allocate_losses([(1, 1, 1), (2, 1, 1), (3, 1, 1)], 5)
        self.assertEqual(sum(r['lost'] for r in rows), 1)

    def test_malformed_and_foreign_exposure_cannot_change_budget(self):
        raw = {'1': {'weight': float('nan'), 'reason': 'Invalid'},
               '2': {'weight': 999, 'reason': 'Exposed'}, '3': None,
               '4': {'weight': .5, 'reason': ''},
               '999': {'weight': 4, 'reason': 'Foreign unit'}}
        clean = normalize_exposure(raw)
        self.assertNotIn('1', clean)
        self.assertNotIn('3', clean)
        self.assertNotIn('4', clean)
        self.assertEqual(clean['2']['weight'], 4)
        self.assertEqual([r['unit_id'] for r in allocate_losses([(1, 5, 5)], 20, raw)], [1])

    def test_budget_and_caps_across_sizes_and_weights(self):
        rng = random.Random(12)
        for _ in range(200):
            units = [(i, rng.randint(1, 100), 100) for i in range(rng.randint(0, 20))]
            exposure = {str(i): {'weight': rng.uniform(.25, 4), 'reason': 'Assessment'} for i, _, _ in units}
            percent = rng.randint(0, 100)
            rows = allocate_losses(units, percent, exposure)
            self.assertEqual(sum(r['lost'] for r in rows), math.ceil(sum(u[1] for u in units) * percent / 100))
            self.assertTrue(all(0 <= r['lost'] <= r['committed'] for r in rows))

    def test_duplicate_plan_entries_are_combined(self):
        self.assertEqual(_entries('[{"unit_id":1,"qty":3},{"unit_id":1,"qty":2}]'), [(1, 5)])
