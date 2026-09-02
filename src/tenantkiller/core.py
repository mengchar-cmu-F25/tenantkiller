"""Discovery and execution for TenantKiller's first mutation operator."""

from __future__ import annotations

import ast
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_OPERATORS = {"filter", "get"}
_SCOPE_ROOTS = {"tenant", "organization", "org", "company"}
_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}
_IGNORABLE_TOKENS = {
    tokenize.COMMENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.INDENT,
    tokenize.NEWLINE,
    tokenize.NL,
}


@dataclass(frozen=True)
class Mutation:
    """A source edit that removes one tenant-scope ORM keyword."""

    identifier: str
    relative_path: str
    line: int
    column: int
    operator: str
    keyword: str
    start: int
    end: int
    source_digest: str
    encoding: str

    @property
    def location(self) -> str:
        return f"{self.relative_path}:{self.line}:{self.column}"

    @property
    def description(self) -> str:
        return f"remove {self.keyword}= from .{self.operator}()"


@dataclass(frozen=True)
class MutationResult:
    mutation: Mutation
    status: str
    returncode: int | None
    output: str


@dataclass(frozen=True)
class RunReport:
    baseline_seconds: float
    results: tuple[MutationResult, ...]


class BaselineFailed(RuntimeError):
    """Raised when the supplied test command does not pass before mutation."""

    def __init__(self, returncode: int | None, output: str, timed_out: bool = False):
        self.returncode = returncode
        self.output = output
        self.timed_out = timed_out
        reason = "timed out" if timed_out else f"exited with {returncode}"
        super().__init__(f"baseline test command {reason}")


@dataclass(frozen=True)
class _CommandResult:
    returncode: int | None
    output: str
    timed_out: bool
    seconds: float


def _scope_keyword(name: str | None) -> bool:
    if not name:
        return False
    root = name.split("__", 1)[0]
    if root.endswith("_id"):
        root = root[:-3]
    return root in _SCOPE_ROOTS


def _project_root_and_files(target: Path) -> tuple[Path, list[Path]]:
    target = target.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    if target.is_file():
        if target.suffix != ".py":
            raise ValueError(f"target file must end in .py: {target}")
        return target.parent, [target]

    files = []
    for path in target.rglob("*.py"):
        relative_parts = path.relative_to(target).parts[:-1]
        if not any(part in _IGNORED_DIRS for part in relative_parts):
            files.append(path)
    return target, sorted(files)


def _read_source(path: Path) -> tuple[bytes, str, str]:
    raw = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw, raw.decode(encoding), encoding


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _character_column(line: str, utf8_column: int) -> int:
    return len(line.encode("utf-8")[:utf8_column].decode("utf-8"))


def _absolute_ast_position(
    text_lines: list[str], starts: list[int], line: int, utf8_column: int
) -> int:
    return starts[line - 1] + _character_column(text_lines[line - 1], utf8_column)


def _absolute_token_position(starts: list[int], position: tuple[int, int]) -> int:
    line, column = position
    return starts[line - 1] + column


def _removal_span(
    text: str,
    lines: list[str],
    starts: list[int],
    call: ast.Call,
    keyword: ast.keyword,
) -> tuple[int, int] | None:
    start = _absolute_ast_position(lines, starts, keyword.lineno, keyword.col_offset)
    end = _absolute_ast_position(
        lines, starts, keyword.end_lineno, keyword.end_col_offset
    )
    call_start = _absolute_ast_position(lines, starts, call.lineno, call.col_offset)
    call_end = _absolute_ast_position(lines, starts, call.end_lineno, call.end_col_offset)

    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    before = None
    after = None
    for token in tokens:
        if token.type in _IGNORABLE_TOKENS:
            continue
        token_start = _absolute_token_position(starts, token.start)
        token_end = _absolute_token_position(starts, token.end)
        if call_start <= token_end <= start:
            before = (token, token_start, token_end)
        if after is None and end <= token_start <= call_end:
            after = (token, token_start, token_end)

    candidate_spans: list[tuple[int, int]] = []
    if after and after[0].type == tokenize.OP and after[0].string == ",":
        removal_end = after[2]
        while removal_end < len(text) and text[removal_end] in " \t":
            removal_end += 1
        candidate_spans.append((start, removal_end))
    if before and before[0].type == tokenize.OP and before[0].string == ",":
        candidate_spans.append((before[1], end))
    candidate_spans.append((start, end))

    for candidate_start, candidate_end in candidate_spans:
        mutated = text[:candidate_start] + text[candidate_end:]
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue
        return candidate_start, candidate_end
    return None


def discover_mutations(target: str | Path) -> list[Mutation]:
    """Find removable tenant/org/company kwargs in ``filter`` and ``get`` calls."""

    root, files = _project_root_and_files(Path(target))
    mutations: list[Mutation] = []

    for path in files:
        raw, text, encoding = _read_source(path)
        tree = ast.parse(text, filename=str(path))
        lines = text.splitlines(keepends=True)
        starts = _line_starts(text)
        relative_path = path.relative_to(root).as_posix()
        source_digest = hashlib.sha256(raw).hexdigest()

        calls = sorted(
            (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for call in calls:
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in _OPERATORS:
                continue
            for keyword in call.keywords:
                if not _scope_keyword(keyword.arg):
                    continue
                span = _removal_span(text, lines, starts, call, keyword)
                if span is None:
                    continue
                seed = (
                    f"{relative_path}:{keyword.lineno}:{keyword.col_offset}:"
                    f"{call.func.attr}:{keyword.arg}"
                )
                identifier = "TK-" + hashlib.sha256(seed.encode()).hexdigest()[:10].upper()
                mutations.append(
                    Mutation(
                        identifier=identifier,
                        relative_path=relative_path,
                        line=keyword.lineno,
                        column=keyword.col_offset + 1,
                        operator=call.func.attr,
                        keyword=keyword.arg or "",
                        start=span[0],
                        end=span[1],
                        source_digest=source_digest,
                        encoding=encoding,
                    )
                )

    return mutations


def _mutated_bytes(source_path: Path, mutation: Mutation) -> bytes:
    raw, text, encoding = _read_source(source_path)
    if hashlib.sha256(raw).hexdigest() != mutation.source_digest:
        raise RuntimeError(f"source changed after discovery: {source_path}")
    mutated = text[: mutation.start] + text[mutation.end :]
    ast.parse(mutated, filename=str(source_path))
    return mutated.encode(encoding)


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _IGNORED_DIRS}


def _run_in_copy(
    root: Path,
    command: Sequence[str],
    timeout: float,
    mutation: Mutation | None = None,
) -> _CommandResult:
    import time

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tenantkiller-") as temporary:
        workspace = Path(temporary) / "project"
        shutil.copytree(root, workspace, ignore=_copy_ignore, symlinks=True)
        if mutation is not None:
            source = root / mutation.relative_path
            destination = workspace / mutation.relative_path
            destination.write_bytes(_mutated_bytes(source, mutation))

        environment = os.environ.copy()
        previous_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(workspace) + (
            os.pathsep + previous_pythonpath if previous_pythonpath else ""
        )
        try:
            completed = subprocess.run(
                list(command),
                cwd=workspace,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return _CommandResult(None, output, True, time.monotonic() - started)
        except OSError as error:
            return _CommandResult(None, str(error), False, time.monotonic() - started)
        return _CommandResult(
            completed.returncode,
            completed.stdout,
            False,
            time.monotonic() - started,
        )


def run_mutations(
    target: str | Path,
    command: Sequence[str],
    *,
    timeout: float = 120.0,
    mutations: Sequence[Mutation] | None = None,
) -> RunReport:
    """Run a passing baseline and then each mutant in a fresh temporary copy."""

    if not command:
        raise ValueError("test command cannot be empty")
    root, _ = _project_root_and_files(Path(target))
    selected = list(mutations) if mutations is not None else discover_mutations(target)
    if not selected:
        return RunReport(0.0, ())

    baseline = _run_in_copy(root, command, timeout)
    if baseline.timed_out or baseline.returncode != 0:
        raise BaselineFailed(baseline.returncode, baseline.output, baseline.timed_out)

    results = []
    for mutation in selected:
        execution = _run_in_copy(root, command, timeout, mutation)
        if execution.timed_out or execution.returncode is None:
            status = "ERROR"
        elif execution.returncode == 0:
            status = "SURVIVED"
        else:
            status = "KILLED"
        results.append(
            MutationResult(
                mutation=mutation,
                status=status,
                returncode=execution.returncode,
                output=execution.output,
            )
        )
    return RunReport(baseline.seconds, tuple(results))

