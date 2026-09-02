"""Command-line interface for TenantKiller."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    BaselineFailed,
    Mutation,
    _validate_timeout,
    discover_mutations,
    run_mutations,
)


def _mutation_dict(mutation: Mutation) -> dict[str, object]:
    return {
        "id": mutation.identifier,
        "path": mutation.relative_path,
        "line": mutation.line,
        "column": mutation.column,
        "operator": mutation.operator,
        "keyword": mutation.keyword,
        "description": mutation.description,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tenantkiller",
        description="Test whether Django tenant-isolation checks catch missing ORM scope filters.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    list_parser = subparsers.add_parser("list", help="list mutants without changing or testing code")
    list_parser.add_argument("target", type=Path, help="Python file or project directory")
    list_parser.add_argument("--json", action="store_true", help="emit machine-readable output")

    run_parser = subparsers.add_parser("run", help="run each mutant in an isolated temporary copy")
    run_parser.add_argument("target", type=Path, help="Python file or project directory")
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds allowed per test run (default: 120)",
    )
    run_parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="test command after --, for example: -- python -m pytest -q",
    )
    return parser


def _list(args: argparse.Namespace) -> int:
    mutations = discover_mutations(args.target)
    if args.json:
        print(json.dumps([_mutation_dict(item) for item in mutations], indent=2))
    else:
        for mutation in mutations:
            print(f"{mutation.identifier}  {mutation.location}  {mutation.description}")
        print(f"\n{len(mutations)} mutant(s) found; no files changed.")
    return 0


def _run(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        print("error: provide a test command after --", file=sys.stderr)
        return 2
    timeout = _validate_timeout(args.timeout)

    mutations = discover_mutations(args.target)
    if not mutations:
        if args.json:
            print(json.dumps({"baseline": "not-run", "results": [], "summary": {"total": 0}}))
        else:
            print("No supported tenant-scope mutations found; tests were not run.")
        return 0

    try:
        report = run_mutations(
            args.target,
            command,
            timeout=timeout,
            mutations=mutations,
        )
    except BaselineFailed as error:
        if args.json:
            print(
                json.dumps(
                    {
                        "baseline": "timeout" if error.timed_out else "failed",
                        "returncode": error.returncode,
                        "output": error.output,
                        "diagnostic": error.diagnostic,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Baseline failed: {error}", file=sys.stderr)
            if error.output:
                print(error.output[-4000:], file=sys.stderr)
        return 2

    killed = sum(item.status == "KILLED" for item in report.results)
    survived = sum(item.status == "SURVIVED" for item in report.results)
    errors = sum(item.status == "ERROR" for item in report.results)
    total = len(report.results)
    score = killed / total * 100 if total else 0.0

    if args.json:
        print(
            json.dumps(
                {
                    "baseline": "passed",
                    "results": [
                        {
                            **_mutation_dict(item.mutation),
                            "status": item.status.lower(),
                            "returncode": item.returncode,
                            "diagnostic": item.diagnostic,
                        }
                        for item in report.results
                    ],
                    "summary": {
                        "total": total,
                        "killed": killed,
                        "survived": survived,
                        "errors": errors,
                        "mutation_score": round(score, 1),
                    },
                },
                indent=2,
            )
        )
    else:
        print(f"Baseline passed ({report.baseline_seconds:.2f}s).")
        for item in report.results:
            print(
                f"{item.status:<8} {item.mutation.identifier}  "
                f"{item.mutation.location}  {item.mutation.description}"
            )
            if item.diagnostic:
                print(f"         {item.diagnostic}")
            if item.status == "ERROR" and item.output:
                print(f"         {item.output[-1000:].strip()}")
        print(
            f"\n{killed} killed, {survived} survived, {errors} error(s); "
            f"mutation score {score:.1f}%"
        )

    if errors:
        return 2
    return 1 if survived else 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _list(args) if args.subcommand == "list" else _run(args)
    except (FileNotFoundError, SyntaxError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
