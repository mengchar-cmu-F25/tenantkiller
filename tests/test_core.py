from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tenantkiller.core import discover_mutations, run_mutations


class DiscoveryTests(unittest.TestCase):
    def test_finds_supported_scope_keywords_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "views.py"
            source.write_text(
                """\
def query(manager, request):
    first = manager.filter(tenant_id=request.tenant_id, active=True)
    second = manager.get(status='ready', organization__slug=request.slug)
    ignored = manager.exclude(company_id=request.company_id)
    ordinary = manager.filter(status='ready')
    return first, second, ignored, ordinary
""",
                encoding="utf-8",
            )

            mutations = discover_mutations(source)

            self.assertEqual([item.operator for item in mutations], ["filter", "get"])
            self.assertEqual(
                [item.keyword for item in mutations],
                ["tenant_id", "organization__slug"],
            )

    def test_handles_multiline_and_unicode_before_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "models.py"
            source.write_text(
                """\
label = '客户'
result = records.filter(
    active=True,
    company_id=current_company,
)
""",
                encoding="utf-8",
            )

            mutation = discover_mutations(source)[0]

            self.assertEqual(mutation.line, 4)
            self.assertEqual(mutation.keyword, "company_id")


class RunnerTests(unittest.TestCase):
    def test_kills_mutant_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app.py"
            app.write_text(
                """\
class Manager:
    def filter(self, **kwargs):
        return kwargs

def visible(manager, tenant_id):
    return manager.filter(tenant_id=tenant_id, active=True)
""",
                encoding="utf-8",
            )
            test_file = root / "test_app.py"
            test_file.write_text(
                """\
import unittest
from app import Manager, visible

class TenantTest(unittest.TestCase):
    def test_query_is_scoped(self):
        self.assertEqual(visible(Manager(), 7), {'tenant_id': 7, 'active': True})
""",
                encoding="utf-8",
            )
            before = app.read_bytes()

            report = run_mutations(
                root,
                [sys.executable, "-m", "unittest", "discover", "-q"],
                timeout=15,
            )

            self.assertEqual(len(report.results), 1)
            self.assertEqual(report.results[0].status, "KILLED")
            self.assertEqual(app.read_bytes(), before)

    def test_reports_surviving_mutant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "result = records.get(company=company, slug='demo')\n",
                encoding="utf-8",
            )
            (root / "test_smoke.py").write_text(
                "import unittest\n\nclass Smoke(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )

            report = run_mutations(
                root,
                [sys.executable, "-m", "unittest", "discover", "-q"],
                timeout=15,
            )

            self.assertEqual(report.results[0].status, "SURVIVED")


if __name__ == "__main__":
    unittest.main()

