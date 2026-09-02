from __future__ import annotations

import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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

    def test_handles_multiline_and_reports_unicode_character_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "models.py"
            source.write_text(
                """\
标签 = '客户'; first = records.get(org_id=current_org)
result = records.filter(
    active=True,
    company_id=current_company,
)
""",
                encoding="utf-8",
            )

            first, second = discover_mutations(source)

            self.assertEqual(first.column, 32)
            self.assertEqual(second.line, 4)
            self.assertEqual(second.keyword, "company_id")

    def test_skips_symlinked_python_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.py"
            outside.write_text(
                "result = records.filter(tenant_id=current_tenant)\n",
                encoding="utf-8",
            )
            link = root / "linked.py"
            try:
                link.symlink_to(outside)
                self.assertEqual(discover_mutations(root), [])
                with self.assertRaisesRegex(ValueError, "symlink targets"):
                    discover_mutations(link)
            finally:
                outside.unlink(missing_ok=True)


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

    def test_rejects_forged_out_of_tree_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text(
                "result = records.filter(tenant_id=current_tenant)\n",
                encoding="utf-8",
            )
            outside = root.parent / f"{root.name}-outside.py"
            outside.write_text("do not change\n", encoding="utf-8")
            mutation = discover_mutations(root)[0]
            forged = replace(mutation, relative_path=f"../{outside.name}")
            try:
                with self.assertRaisesRegex(ValueError, "was not discovered"):
                    run_mutations(
                        root,
                        [sys.executable, "-c", "raise SystemExit(0)"],
                        mutations=[forged],
                    )
                self.assertEqual(outside.read_text(encoding="utf-8"), "do not change\n")
            finally:
                outside.unlink(missing_ok=True)

    def test_src_layout_imports_the_temporary_mutant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "tk_synthetic_package"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            source = package / "service.py"
            source.write_text(
                """\
class Manager:
    def get(self, **kwargs):
        return kwargs

def visible(manager, company_id):
    return manager.get(company_id=company_id, active=True)
""",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_scope.py").write_text(
                """\
import unittest
from tk_synthetic_package.service import Manager, visible

class ScopeTest(unittest.TestCase):
    def test_company_scope(self):
        self.assertIn('company_id', visible(Manager(), 9))
""",
                encoding="utf-8",
            )

            report = run_mutations(
                root,
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                timeout=15,
            )

            self.assertEqual(report.results[0].status, "KILLED")

    def test_writes_mutant_when_original_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text(
                """\
class Manager:
    def filter(self, **kwargs): return kwargs
def visible(manager): return manager.filter(org_id=3)
""",
                encoding="utf-8",
            )
            (root / "test_app.py").write_text(
                """\
import unittest
from app import Manager, visible
class TestScope(unittest.TestCase):
    def test_scope(self): self.assertIn('org_id', visible(Manager()))
""",
                encoding="utf-8",
            )
            source.chmod(0o444)

            report = run_mutations(
                root,
                [sys.executable, "-m", "unittest", "discover", "-q"],
                timeout=15,
            )

            self.assertEqual(report.results[0].status, "KILLED")
            self.assertEqual(source.stat().st_mode & 0o777, 0o444)

    def test_reports_mutant_preparation_failure_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "result = records.filter(tenant_id=tenant_id)\n",
                encoding="utf-8",
            )

            with patch.object(Path, "write_bytes", side_effect=PermissionError("read only")):
                report = run_mutations(
                    root,
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=15,
                )

            result = report.results[0]
            self.assertEqual(result.status, "ERROR")
            self.assertIn("could not prepare mutant workspace", result.diagnostic or "")
            self.assertIn("read only", result.diagnostic or "")

    def test_timeout_kills_descendants_and_reports_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "result = records.filter(tenant_id=tenant_id, active=True)\n",
                encoding="utf-8",
            )
            marker = root.parent / f"{root.name}-descendant-marker"
            script = root / "check.py"
            script.write_text(
                """\
from pathlib import Path
import subprocess
import sys
import time

if 'tenant_id=tenant_id' in Path('app.py').read_text(encoding='utf-8'):
    raise SystemExit(0)
child = "import pathlib,sys,time; time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('escaped')"
subprocess.Popen([sys.executable, '-c', child, sys.argv[1]])
time.sleep(5)
""",
                encoding="utf-8",
            )
            try:
                report = run_mutations(
                    root,
                    [sys.executable, "check.py", str(marker)],
                    timeout=0.2,
                )
                result = report.results[0]
                self.assertEqual(result.status, "ERROR")
                self.assertIn("timed out after 0.2s", result.diagnostic or "")
                time.sleep(1.0)
                self.assertFalse(marker.exists())
            finally:
                marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
