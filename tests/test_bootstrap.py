"""Sanity of the governance scaffold, not a substitute for product acceptance tests."""
from pathlib import Path
import unittest,json

class BaselineContracts(unittest.TestCase):
    def test_required_product_contracts_exist(self):
        for name in ('AGENTS.md','docs/PRODUCT_NORTH_STAR.md','docs/USER_EXPERIENCE.md','docs/DECISION_LEDGER.md','config/quality_gates.json'):
            self.assertTrue(Path(name).is_file(),name)
    def test_quality_commands_are_registered(self):
        data=json.loads(Path('config/quality_gates.json').read_text())
        self.assertTrue(data['commands'])
