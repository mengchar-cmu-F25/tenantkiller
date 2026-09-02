"""Discovery and execution for TenantKiller's first mutation operator."""

from __future__ import annotations

import ast
import hashlib
import io
import os
import signal
import shutil
import stat
import subprocess
import tempfile
import tokenize
import time
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
    project_root: str

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
    diagnostic: str | None


@dataclass(frozen=True)
class RunReport:
    baseline_seconds: float
    results: tuple[MutationResult, ...]


class BaselineFailed(RuntimeError):
    """Raised when the supplied test command does not pass before mutation."""

    def __init__(
        self,
        returncode: int | None,
        output: str,
        timed_out: bool = False,
        diagnostic: str | None = None,
    ):
        self.returncode = returncode
        self.output = output
        self.timed_out = timed_out
        self.diagnostic = diagnostic
        reason = diagnostic or ("timed out" if timed_out else f"exited with {returncode}")
        super().__init__(f"baseline test command {reason}")


@dataclass(frozen=True)
class _CommandResult:
    returncode: int | None
    output: str
    timed_out: bool
    seconds: float
    diagnostic: str | None = None


def _scope_keyword(name: str | None) -> bool:
    if not name:
        return False
    root = name.split("__", 1)[0]
    if root.endswith("_id"):
        root = root[:-3]
    return root in _SCOPE_ROOTS


def _project_root_and_files(target: Path) -> tuple[Path, list[Path]]:
    target = target.expanduser()
    if target.is_symlink():
        raise ValueError(f"symlink targets are not supported: {target}")
    target = target.resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    if target.is_file():
        if target.suffix != ".py":
            raise ValueError(f"target file must end in .py: {target}")
        return target.parent, [target]

    files = []
    for path in target.rglob("*.py"):
        relative_parts = path.relative_to(target).parts[:-1]
        if not any(part in _IGNORED_DIRS for part in relative_parts) and not _has_symlink_component(
            target, path
        ):
            files.append(path)
    return target, sorted(files)


def _has_symlink_component(root: Path, path: Path) -> bool:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _contained_regular_file(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"mutation path escapes project root: {relative_path}")
    candidate = root.joinpath(relative)
    if _has_symlink_component(root, candidate):
        raise ValueError(f"mutation target contains a symlink: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise ValueError(f"mutation path escapes project root: {relative_path}") from error
    if not resolved.is_file():
        raise ValueError(f"mutation target is not a regular file: {relative_path}")
    return resolved


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
                        column=_character_column(
                            lines[keyword.lineno - 1], keyword.col_offset
                        )
                        + 1,
                        operator=call.func.attr,
                        keyword=keyword.arg or "",
                        start=span[0],
                        end=span[1],
                        source_digest=source_digest,
                        encoding=encoding,
                        project_root=str(root),
                    )
                )

    return mutations


def _mutated_bytes(source_path: Path, mutation: Mutation) -> bytes:
    raw, text, encoding = _read_source(source_path)
    if hashlib.sha256(raw).hexdigest() != mutation.source_digest:
        raise RuntimeError(f"source changed after discovery: {source_path}")
    if encoding != mutation.encoding:
        raise RuntimeError(f"source encoding changed after discovery: {source_path}")
    if not 0 <= mutation.start < mutation.end <= len(text):
        raise RuntimeError(f"invalid mutation span for {source_path}")
    if mutation.keyword not in text[mutation.start : mutation.end]:
        raise RuntimeError(f"mutation span no longer contains {mutation.keyword}: {source_path}")
    mutated = text[: mutation.start] + text[mutation.end :]
    ast.parse(mutated, filename=str(source_path))
    return mutated.encode(encoding)


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _IGNORED_DIRS}


def _terminate_process_group(process: subprocess.Popen[str]) -> str:
    """Terminate the command and descendants, escalating after a short grace period."""

    if process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.terminate()

    try:
        output, _ = process.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        output, _ = process.communicate()
    return output


def _execute_command(
    command: Sequence[str], workspace: Path, environment: dict[str, str], timeout: float
) -> _CommandResult:
    started = time.monotonic()
    popen_options: dict[str, object] = {}
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(
            list(command),
            cwd=workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            **popen_options,
        )
    except OSError as error:
        return _CommandResult(
            None,
            "",
            False,
            time.monotonic() - started,
            f"could not start test command: {error}",
        )

    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        output = _terminate_process_group(process)
        return _CommandResult(
            process.returncode,
            output,
            True,
            time.monotonic() - started,
            f"timed out after {timeout:g}s; command process group terminated",
        )
    return _CommandResult(
        process.returncode,
        output,
        False,
        time.monotonic() - started,
    )


def _run_in_copy(
    root: Path,
    command: Sequence[str],
    timeout: float,
    mutation: Mutation | None = None,
) -> _CommandResult:
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="tenantkiller-") as temporary:
            workspace = Path(temporary) / "project"
            shutil.copytree(root, workspace, ignore=_copy_ignore, symlinks=True)
            if mutation is not None:
                source = _contained_regular_file(root, mutation.relative_path)
                destination = _contained_regular_file(workspace, mutation.relative_path)
                mode = stat.S_IMODE(destination.stat().st_mode)
                if not mode & stat.S_IWUSR:
                    destination.chmod(mode | stat.S_IWUSR)
                destination.write_bytes(_mutated_bytes(source, mutation))

            environment = os.environ.copy()
            import_roots = [workspace]
            if (workspace / "src").is_dir():
                import_roots.insert(0, workspace / "src")
            previous_pythonpath = environment.get("PYTHONPATH")
            if previous_pythonpath:
                import_roots.append(Path(previous_pythonpath))
            environment["PYTHONPATH"] = os.pathsep.join(map(str, import_roots))
            return _execute_command(command, workspace, environment, timeout)
    except (OSError, RuntimeError, SyntaxError, ValueError) as error:
        stage = "mutant" if mutation is not None else "baseline"
        return _CommandResult(
            None,
            "",
            False,
            time.monotonic() - started,
            f"could not prepare {stage} workspace: {error}",
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
    discovered = discover_mutations(target)
    if mutations is None:
        selected = discovered
    else:
        canonical = {mutation.identifier: mutation for mutation in discovered}
        selected = list(mutations)
        if len({mutation.identifier for mutation in selected}) != len(selected):
            raise ValueError("duplicate mutation identifiers are not allowed")
        for mutation in selected:
            if mutation.project_root != str(root) or canonical.get(mutation.identifier) != mutation:
                raise ValueError(
                    f"mutation {mutation.identifier} was not discovered from target {root}"
                )
    if not selected:
        return RunReport(0.0, ())

    baseline = _run_in_copy(root, command, timeout)
    if baseline.diagnostic or baseline.timed_out or baseline.returncode != 0:
        raise BaselineFailed(
            baseline.returncode,
            baseline.output,
            baseline.timed_out,
            baseline.diagnostic,
        )

    results = []
    for mutation in selected:
        execution = _run_in_copy(root, command, timeout, mutation)
        if execution.diagnostic or execution.timed_out or execution.returncode is None:
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
                diagnostic=execution.diagnostic,
            )
        )
    return RunReport(baseline.seconds, tuple(results))
