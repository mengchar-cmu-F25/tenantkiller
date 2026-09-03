from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tenantkiller.core import discover_mutations, run_mutations


class DjangoScenarioTests(unittest.TestCase):
    def _check_scenario(self, operator: str, suite: str, failure: str) -> None:
        root = Path(__file__).resolve().parents[1] / "examples" / "django_scenarios"
        originals = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
        candidates = [item for item in discover_mutations(root) if item.operator == operator]
        self.assertEqual(len(candidates), 1)

        for strength, status, returncode in (("Weak", "SURVIVED", 0), ("Strong", "KILLED", 1)):
            with self.subTest(suite=suite, strength=strength):
                # Returning a report also proves that this suite's unmodified baseline passed.
                report = run_mutations(
                    root,
                    [sys.executable, "-B", "-m", "unittest", "-q", f"checks.{strength}{suite}"],
                    mutations=candidates,
                    timeout=15,
                )
                self.assertEqual(len(report.results), 1)
                result = report.results[0]
                self.assertEqual(result.status, status, result.output)
                self.assertEqual(result.returncode, returncode, result.output)
                if status == "KILLED":
                    self.assertIn(failure, result.output)
                    self.assertIn("Ran 2 tests", result.output)
                else:
                    self.assertIn("OK", result.output)
                self.assertEqual({path for path in root.iterdir() if path.is_file()}, set(originals))
                for path, contents in originals.items():
                    self.assertEqual(path.read_bytes(), contents, f"example changed: {path.name}")

    def test_filter_keeps_business_constraints_while_losing_tenant_scope(self) -> None:
        self._check_scenario("filter", "FilterTest", "foreign tenants leaked into open orders")

    def test_get_keeps_primary_key_while_losing_tenant_scope(self) -> None:
        self._check_scenario("get", "GetTest", "DoesNotExist not raised")


if __name__ == "__main__":
    unittest.main()
