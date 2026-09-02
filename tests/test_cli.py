from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tenantkiller.cli import main


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


if __name__ == "__main__":
    unittest.main()
