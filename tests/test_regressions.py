from __future__ import annotations

import io
import json
import os
import signal
import sys
import tempfile
import unittest
import venv
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tenantkiller.cli import main
from tenantkiller.core import BaselineFailed, discover_mutations, run_mutations


class ResultContractTests(unittest.TestCase):
    def test_pytest_failures_and_runner_errors_keep_their_real_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "class Rows:\n    def get(self, **kwargs): return kwargs\n"
                "def visible(): return Rows().get(tenant_id=7)\n",
                encoding="utf-8",
            )
            (root / "test_app.py").write_text(
                "from app import visible\n"
                "def test_scope(): assert visible() == {'tenant_id': 7}\n",
                encoding="utf-8",
            )
            (root / "conftest.py").write_text(
                """\
import pytest
from app import visible

def pytest_addoption(parser):
    parser.addoption('--abort-mode')

def pytest_sessionstart(session):
    if visible(): return
    mode = session.config.getoption('--abort-mode')
    if mode == '2': raise KeyboardInterrupt('synthetic interruption')
    if mode == '3': raise RuntimeError('synthetic internal error')
    if mode == '4': raise pytest.UsageError('synthetic usage error')
    # Exercise the exit-code-6 contract on pytest versions without --max-warnings.
    if mode == '6': pytest.exit('synthetic warning-limit exit', returncode=6)

def pytest_collection_modifyitems(config, items):
    if not visible() and config.getoption('--abort-mode') == '5': items.clear()
""",
                encoding="utf-8",
            )
            for code, evidence in (
                (1, "AssertionError"), (2, "KeyboardInterrupt"), (3, "INTERNALERROR"),
                (4, "synthetic usage error"), (5, "no tests ran"),
                (6, "synthetic warning-limit exit"),
            ):
                with self.subTest(code=code):
                    output = io.StringIO()
                    with patch.dict(os.environ, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}), redirect_stdout(output):
                        exit_code = main([
                            "run", "--json", directory, "--", sys.executable, "-B",
                            "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "--assert=plain", f"--abort-mode={code}",
                        ])
                    report = json.loads(output.getvalue())
                    result = report["results"][0]
                    self.assertEqual(report["baseline"], "passed")
                    self.assertEqual(result["returncode"], code)
                    self.assertIn(evidence, result["output"])
                    self.assertEqual(exit_code, 0 if code == 1 else 2)
                    self.assertEqual(result["status"], "killed" if code == 1 else "error")
                    self.assertEqual(report["summary"]["complete"], code == 1)
                    self.assertEqual(report["summary"]["mutation_score"], 100.0 if code == 1 else None)
                    if code != 1:
                        self.assertTrue(result["diagnostic"])

    @unittest.skipUnless(os.name == "posix", "POSIX signal return codes")
    def test_signal_termination_is_error_not_a_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            report = run_mutations(directory, [sys.executable, "-u", "-c", (
                "from pathlib import Path; import os, signal; "
                "print('captured before signal'); "
                "scoped = 'tenant_id=7' in Path('app.py').read_text(); "
                "os.kill(os.getpid(), signal.SIGTERM) if not scoped else None"
            )])
            result = report.results[0]
            self.assertEqual(result.returncode, -signal.SIGTERM)
            self.assertEqual(result.status, "ERROR")
            self.assertIn("signal", result.diagnostic)
            self.assertIn("captured before signal", result.output)

    def test_custom_exit_contract_is_explicit_and_replaces_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            for code, options, expected_status, expected_exit in (
                (7, [], "ERROR", 2),
                (7, ["--failure-exit-code", "7", "--failure-exit-code", "9"], "KILLED", 0),
                (1, ["--failure-exit-code", "7"], "ERROR", 2),
            ):
                with self.subTest(code=code, options=options):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = main(["run", *options, directory, "--", sys.executable, "-c", (
                            "from pathlib import Path; "
                            f"raise SystemExit(0 if 'tenant_id=7' in Path('app.py').read_text() else {code})"
                        )])
                    self.assertEqual(exit_code, expected_exit)
                    self.assertIn(expected_status, output.getvalue())
                    if expected_exit == 2:
                        self.assertIn("incomplete", output.getvalue())
                        self.assertNotIn("%", output.getvalue())

    def test_invalid_failure_exit_codes_fail_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for codes in ([], [0], [-15], [True], [1.5], ["1"]):
                with self.subTest(codes=codes), patch("tenantkiller.core._execute_command") as execute:
                    with self.assertRaisesRegex(ValueError, "failure exit codes"):
                        run_mutations(directory, [sys.executable], failure_exit_codes=codes)
                    execute.assert_not_called()

    def test_mixed_kill_and_error_never_produce_a_complete_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app.py").write_text(
                "rows.get(tenant_id=7)\nrows.get(company_id=9)\n", encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["run", "--json", directory, "--", sys.executable, "-c", (
                    "from pathlib import Path; text = Path('app.py').read_text(); "
                    "raise SystemExit(1 if 'tenant_id=7' not in text else 3 if 'company_id=9' not in text else 0)"
                )])
            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual([result["status"] for result in report["results"]], ["killed", "error"])
            self.assertEqual(report["summary"], {
                "total": 2, "killed": 1, "survived": 0, "errors": 1,
                "complete": False, "mutation_score": None,
            })

    def test_custom_failure_code_cannot_make_a_failing_baseline_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            with self.assertRaises(BaselineFailed) as failed:
                run_mutations(directory, [sys.executable, "-c", "raise SystemExit(7)"], failure_exit_codes=[7])
            self.assertEqual(failed.exception.returncode, 7)

    def test_redundant_scope_survives_correct_cross_tenant_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                """\
class Rows(list):
    def filter(self, **kwargs):
        return Rows(row for row in self if all(row[k] == v for k, v in kwargs.items()))
def visible(rows, tenant):
    return rows.filter(tenant_id=tenant).filter(tenant_id=tenant)
""",
                encoding="utf-8",
            )
            (root / "check.py").write_text(
                "from app import Rows, visible\n"
                "rows = Rows([{'tenant_id': 1, 'id': 'own'}, {'tenant_id': 2, 'id': 'foreign'}])\n"
                "assert visible(rows, 1) == [rows[0]]\n"
                "assert visible(rows, 2) == [rows[1]]\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["run", directory, "--", sys.executable, "-B", "check.py"])
            self.assertEqual(exit_code, 1)
            self.assertIn("2 survived", output.getvalue())
            self.assertIn("redundant safeguard", output.getvalue())
            self.assertIn("unexecuted path", output.getvalue())

    def test_zero_candidates_do_not_claim_security_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch("tenantkiller.cli.run_mutations") as run, redirect_stdout(output):
                exit_code = main(["run", directory, "--", sys.executable])
            self.assertEqual(exit_code, 0)
            self.assertIn("no security assurance", output.getvalue().lower())
            run.assert_not_called()


class SourceScopeTests(unittest.TestCase):
    def test_source_scope_keeps_root_tests_fixtures_and_relative_venv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = root / "app" / "services"
            service.mkdir(parents=True)
            (service / "orders.py").write_text(
                "class Rows:\n    def filter(self, **kwargs): return kwargs\n"
                "def visible(): return Rows().filter(tenant_id=7)\n",
                encoding="utf-8",
            )
            (root / "fixture.json").write_text('{"tenant_id": 7}', encoding="utf-8")
            (root / "check.py").write_text(
                "import json\nfrom pathlib import Path\nfrom app.services.orders import visible\n"
                "assert visible() == json.loads(Path('fixture.json').read_text())\n",
                encoding="utf-8",
            )
            candidates = discover_mutations(root)
            self.assertEqual(len(candidates), 1)
            (root / "template.py").write_text("{{ invalid template\n", encoding="utf-8")
            (root / "test_helper.py").write_text("rows.get(company_id=9)\n", encoding="utf-8")
            venv.create(root / ".venv", with_pip=False)
            interpreter = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
            sources = ["--source", "app", "--source", "app/services/orders.py"]
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["list", "--json", *sources, directory])
            self.assertEqual(exit_code, 0)
            self.assertEqual([item["id"] for item in json.loads(output.getvalue())], [candidates[0].identifier])
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main([
                    "run", "--json", *sources, "--select", candidates[0].identifier,
                    directory, "--", interpreter, "-B", "check.py",
                ])
            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["baseline"], "passed")
            self.assertEqual(report["results"][0]["path"], "app/services/orders.py")
            self.assertIn("AssertionError", report["results"][0]["output"])
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_invalid_source_paths_fail_before_any_source_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, outside = base / "project", base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            (outside / "secret.py").write_text("{{ must not be read\n", encoding="utf-8")
            (root / "linked").symlink_to(outside, target_is_directory=True)
            (root / "linked.py").symlink_to(outside / "secret.py")
            for source in ("../outside", str(outside), "linked", "linked/secret.py", "linked.py"):
                with self.subTest(source=source), patch("tenantkiller.core._read_source") as read:
                    with self.assertRaises(ValueError):
                        discover_mutations(root, sources=["app.py", source])
                    with patch("tenantkiller.core._execute_command") as execute:
                        with self.assertRaises(ValueError):
                            run_mutations(root, [sys.executable], sources=[source])
                        execute.assert_not_called()
                    read.assert_not_called()
            self.assertEqual(len(discover_mutations(root, sources=["."])), 1)

    def test_syntax_errors_inside_source_scope_remain_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text("{{ invalid template\n", encoding="utf-8")
            with self.assertRaises(SyntaxError):
                discover_mutations(root, sources=["bad.py"])

    def test_source_paths_must_exist_and_be_copyable_python_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            (root / "note.txt").write_text("not Python", encoding="utf-8")
            (root / ".venv").mkdir()
            for sources in ([], ["note.txt"], [".venv"]):
                with self.subTest(sources=sources), self.assertRaises(ValueError):
                    discover_mutations(root, sources=sources)
            with self.assertRaises(FileNotFoundError):
                discover_mutations(root, sources=["missing"])
            with self.assertRaisesRegex(ValueError, "project directory"):
                discover_mutations(root / "app.py", sources=["app.py"])

    def test_runner_rejects_selection_outside_its_source_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("app.py", "other.py"):
                (root / name).write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            other = discover_mutations(root, sources=["other.py"])[0]
            with patch("tenantkiller.core._execute_command") as execute:
                with self.assertRaisesRegex(ValueError, "was not discovered"):
                    run_mutations(root, [sys.executable], sources=["app.py"], mutations=[other])
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
