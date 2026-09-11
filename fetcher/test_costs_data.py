import unittest

import costs_data


class CostDataTests(unittest.TestCase):
    def test_unavailable_rule_cannot_calculate_redemption(self):
        result = costs_data.calculate_redemption_from_lots(
            [{"purchase_date": "2026-01-01", "units": 10}],
            "2026-01-10",
            5,
            100,
            None,
            "2026-01-01",
        )
        self.assertFalse(result["calculable"])

    def test_fifo_redemption_applies_each_lot_rule(self):
        rule = '{"rules": [{"start_day": 0, "end_day": 30, "rate_pct": 1.0}, {"start_day": 31, "rate_pct": 0.0}]}'
        result = costs_data.calculate_redemption_from_lots(
            [
                {"purchase_date": "2026-01-01", "units": 10},
                {"purchase_date": "2026-02-01", "units": 10},
            ],
            "2026-02-15",
            15,
            100,
            rule,
            "2026-01-01",
        )
        self.assertTrue(result["calculable"])
        self.assertEqual(result["gross_redemption_value"], 1500)
        self.assertEqual(result["exit_load_penalty_amount"], 5)
        self.assertEqual(result["net_redemption_value"], 1495)

    def test_legacy_ter_is_not_marked_official(self):
        specs = costs_data.get_scheme_cost_specs(1, "360 One Balanced Hybrid Fund", "", "Direct")
        self.assertEqual(specs["ter_status"], costs_data.STATUS_LEGACY)
        self.assertEqual(specs["exit_rule_status"], costs_data.STATUS_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
