# TenantKiller

TenantKiller answers one narrow question: **would your tests fail if a Django
query accidentally lost its tenant scope?**

It finds `tenant`, `organization`, `org`, and `company` keyword constraints in
Django-style `.filter()` and `.get()` calls. It removes one constraint at a
time in a fresh temporary project copy, runs your test command, and reports the
mutant as:

- `KILLED` — the test command failed; inspect its output to confirm why;
- `SURVIVED` — the tests still passed, exposing a tenant-isolation test gap;
- `ERROR` — the mutant could not be prepared, or the test command timed out or
  could not run. The CLI includes the reason.

TenantKiller never rewrites the target source tree. Symlinked Python files are
skipped, and an explicitly selected symlink is rejected. A passing baseline is
required before any mutant is tested.

## Install

```bash
git clone https://github.com/mengchar-cmu-F25/tenantkiller.git
cd tenantkiller
python -m pip install .
```

Python 3.11 or newer is required. There are no runtime dependencies, and
Django itself is not required for discovery.

## Quickstart

List mutations without running tests or changing files:

```bash
tenantkiller list examples/django_like
```

Run the bundled dependency-free example:

```bash
tenantkiller run examples/django_like -- python -m unittest discover -q
```

Expected result:

```text
Baseline passed (...s).
KILLED   TK-...  app.py:9:27  remove tenant_id= from .filter()

1 killed, 0 survived, 0 error(s); mutation score 100.0%
```

Use the same shape in a real project (place run options before the target):

```bash
tenantkiller run --json . -- python -m pytest tests/tenancy -q
```

Select only the production candidates you reviewed in `tenantkiller list`:

```bash
tenantkiller run --select TK-ID1 --select TK-ID2 --show-output . -- python -m pytest tests/tenancy -q
```

Replace `TK-ID1` and `TK-ID2` with IDs listed for the same target path. Repeated
IDs run once; unknown IDs fail before the baseline runs. Run `list` again after
editing source, because IDs include the source location. Omitting `--select`
runs all discovered candidates, including any in test code.

`--show-output` displays the full captured test output for each mutant; by
default, text reports stay compact and only show an output excerpt for errors.
`--json` always includes each mutant's captured combined stdout/stderr in
`output`. Review test output for secrets before sharing a report.
`--timeout SECONDS` changes the 120-second per-run limit. All run options must
appear before the target path.

Exit codes are `0` when every mutant is killed, `1` when at least one survives,
and `2` for baseline or execution errors.

## Mutation operator in v0.1

The only operator removes one scope keyword from a call whose method name is
`filter` or `get`:

```python
# original
Order.objects.filter(tenant_id=request.tenant_id, status="open")

# temporary mutant
Order.objects.filter(status="open")
```

Recognized roots are `tenant`, `organization`, `org`, and `company`, including
`*_id` and Django lookup forms such as `organization__slug`.

## Deliberate limitations

- Detection is syntactic. TenantKiller does not yet prove that a receiver is a
  Django queryset, so review `tenantkiller list` before a large run.
- It does not cover `exclude`, `Q` objects, positional predicates, managers,
  middleware, Celery, caches, storage paths, or frontend code.
- A non-zero mutant test run is classified as killed; flaky tests can inflate
  the score. Each run uses a fresh copy, but external databases and services
  are not isolated by TenantKiller.
- Commands run with the temporary project and its `src/` directory first on
  `PYTHONPATH`, so editable installs do not silently import the unmutated
  checkout. On timeout, TenantKiller terminates the command's process group.
- The supplied command can still use absolute paths or external services;
  TenantKiller's no-write guarantee covers its own mutation preparation, not
  arbitrary behavior inside a user-supplied test command.
- The temporary copy excludes common VCS, virtual-environment, cache, build,
  and `node_modules` directories. Dependencies should already be installed in
  the environment that invokes TenantKiller.

These constraints are intentional: v0.1 validates the semantic mutation idea
before expanding the operator set.

## Product and real-world evidence

- [One-page product definition](docs/PRODUCT.md)
- [Pinned django-organizations observation](validation/django-organizations/README.md)
  with fixed upstream and TenantKiller revisions
- [Small real-repository corpus](validation/corpus/README.md), including the
  applicability limits that keep the product focused on explicit scope keywords

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```
