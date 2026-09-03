from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tenantkiller.cli import main
from tenantkiller.core import discover_mutations


class CliTests(unittest.TestCase):
    def test_list_is_a_non_mutating_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            original = "result = rows.filter(company_id=company_id)\n"
            source.write_text(original, encoding="utf-8")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(["list", directory])

            self.assertEqual(exit_code, 0)
            self.assertIn("1 mutant(s) found; no files changed", output.getvalue())
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_start_error_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text(
                "result = rows.get(organization_id=organization_id)\n",
                encoding="utf-8",
            )
            error_output = io.StringIO()

            with redirect_stderr(error_output):
                exit_code = main(
                    ["run", directory, "--", "/definitely/missing/tenantkiller-command"]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("could not start test command", error_output.getvalue())

    def test_runs_only_selected_production_candidates_and_preserves_original_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                """\
class Manager:
    def filter(self, **kwargs): return kwargs
    def get(self, **kwargs): return kwargs
def tenant_rows(): return Manager().filter(tenant_id=7)
def company_row(): return Manager().get(company_id=9)
""",
                encoding="utf-8",
            )
            (root / "test_app.py").write_text(
                """\
import unittest
from app import Manager, tenant_rows, company_row
def unused_test_helper(): return Manager().get(organization_id=1)
class ScopeTest(unittest.TestCase):
    def test_tenant(self): self.assertIn('tenant_id', tenant_rows())
    def test_company(self): self.assertIn('company_id', company_row())
""",
                encoding="utf-8",
            )
            before = {path.name: (path.read_bytes(), path.stat().st_mode) for path in root.iterdir()}
            original_cwd = Path.cwd()
            candidates = discover_mutations(root)
            selected = [item.identifier for item in candidates if item.relative_path == "app.py"]
            self.assertEqual(len(candidates), 3)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "run", "--json", "--select", selected[0], "--select", selected[1],
                        "--select", selected[0], directory, "--", sys.executable,
                        "-m", "unittest", "discover", "-q",
                    ]
                )

            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual([item["id"] for item in report["results"]], selected)
            self.assertEqual(report["summary"]["killed"], 2)
            self.assertTrue(all("AssertionError" in item["output"] for item in report["results"]))
            self.assertEqual(Path.cwd(), original_cwd)
            self.assertEqual(
                {path.name: (path.read_bytes(), path.stat().st_mode) for path in root.iterdir()},
                before,
            )

    def test_unknown_selection_fails_before_baseline_even_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            for contents in ("rows.get(tenant_id=7)\n", "rows.get(active=True)\n"):
                with self.subTest(contents=contents):
                    source.write_text(contents, encoding="utf-8")
                    error_output = io.StringIO()
                    with patch("tenantkiller.cli.run_mutations") as run, redirect_stderr(error_output):
                        exit_code = main(
                            ["run", "--select", "TK-UNKNOWN", directory, "--", sys.executable]
                        )
                    self.assertEqual(exit_code, 2)
                    self.assertIn("unknown mutation ID(s): TK-UNKNOWN", error_output.getvalue())
                    run.assert_not_called()

    def test_killed_text_output_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app.py").write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            command = [
                sys.executable, "-c",
                (
                    "from pathlib import Path; "
                    "scoped = 'tenant_id=7' in Path('app.py').read_text(); "
                    "print('scope intact' if scoped else 'tenant scope assertion failed'); "
                    "raise SystemExit(0 if scoped else 1)"
                ),
            ]
            for show_output in (False, True):
                with self.subTest(show_output=show_output):
                    output = io.StringIO()
                    options = ["--show-output"] if show_output else []
                    with redirect_stdout(output):
                        exit_code = main(["run", *options, directory, "--", *command])
                    self.assertEqual(exit_code, 0)
                    self.assertIn("KILLED", output.getvalue())
                    self.assertEqual("tenant scope assertion failed" in output.getvalue(), show_output)

    def test_selected_mutant_timeout_remains_error_with_captured_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text("rows.get(tenant_id=7)\n", encoding="utf-8")
            selected = discover_mutations(source)[0].identifier
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "run", "--json", "--timeout=0.2", "--select", selected, directory, "--",
                        sys.executable, "-u", "-c",
                        (
                            "from pathlib import Path; import time; "
                            "scoped = 'tenant_id=7' in Path('app.py').read_text(); "
                            "print('partial test output'); time.sleep(0 if scoped else 5)"
                        ),
                    ]
                )
            result = json.loads(output.getvalue())["results"][0]
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "error")
            self.assertIn("timed out after 0.2s", result["diagnostic"])
            self.assertIn("partial test output", result["output"])

    def test_rejects_non_finite_and_non_positive_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text(
                "result = rows.get(tenant_id=tenant_id)\n",
                encoding="utf-8",
            )
            for timeout in ("0", "-1", "nan", "inf", "-inf"):
                with self.subTest(timeout=timeout):
                    error_output = io.StringIO()
                    with redirect_stderr(error_output):
                        exit_code = main(
                            [
                                "run",
                                f"--timeout={timeout}",
                                directory,
                                "--",
                                "python",
                                "-c",
                                "raise SystemExit(0)",
                            ]
                        )

                    self.assertEqual(exit_code, 2)
                    self.assertIn(
                        "timeout must be a finite number greater than zero",
                        error_output.getvalue(),
                    )


if __name__ == "__main__":
    unittest.main()
