import unittest

from app import costs_data


class CostDataTests(unittest.TestCase):
    def test_legacy_ter_is_not_marked_official(self):
        specs = costs_data.get_scheme_cost_specs(1, "360 One Balanced Hybrid Fund", "", "Direct")
        self.assertEqual(specs["ter_status"], costs_data.STATUS_LEGACY)

    def test_unmatched_scheme_ter_is_unknown(self):
        specs = costs_data.get_scheme_cost_specs(2, "Definitely Not A Real Scheme Name Xyz", "", "Direct")
        self.assertIsNone(specs["expense_ratio"])
        self.assertEqual(specs["ter_status"], costs_data.STATUS_UNKNOWN)

    def test_estimate_current_ter_drag(self):
        # Average holding value * TER% * (days / 365.25) -- 365.25, not 365, so 365
        # days is very slightly under "1 year" and the result is a hair below the
        # naive 105000 * 1% = 1050.0.
        drag = costs_data.estimate_current_ter_drag(100000, 110000, 365, 1.0)
        expected = 105000 * 0.01 * (365 / 365.25)
        self.assertAlmostEqual(drag, expected, places=6)

    def test_estimate_current_ter_drag_requires_a_ter(self):
        self.assertIsNone(costs_data.estimate_current_ter_drag(100000, 110000, 365, None))


if __name__ == "__main__":
    unittest.main()
