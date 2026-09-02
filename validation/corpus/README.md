# Small real-repository corpus

## Result

**Product HOLD; feature development STOP.** One manually inspected edit caused
the intended targeted-test failure, but this four-repository sample does not
support a standalone product claim. Two relevant scoping libraries are outside
the operator model, and three of the four discovered candidates are test-only.

This is a deliberately small falsification sample, not a benchmark or a
population estimate. No repository was chosen because it produced a favorable
mutation score.

## Pinned sample

All scans used TenantKiller commit
`3d4f20ee406685e03f85ecc6386893f9f0d12023`. “Relevant” below means a manually
reviewed production query whose removed keyword changes tenant or organization
scope; it does not mean a vulnerability.

| Repository and commit | Why included | Python files | Candidates | Review |
| --- | --- | ---: | ---: | --- |
| `bennylope/django-organizations@f8953c4ad568ee5ad5113a3a2aa762c0053bc273` | Organization-domain library with an existing two-membership fixture | 95 | 3 | 1 relevant production query; 2 test-only |
| `citusdata/django-multitenant@72cb58970e707922facd8d5674d10b1027ba63eb` | README describes shared-table tenant context | 69 | 0 | Its injected context is outside this operator |
| `raphaelm/django-scopes@eaac8b8c894d56981caca7d0cd31e3f84d012989` | README describes multi-tenant query safeguards | 20 | 0 | Its scope dimensions/managers are outside this operator |
| `netbox-community/netbox@1fae2d01117b5df1a6b1a90f2fe72d4c7c26eeaf` | Vocabulary-collision control, not an isolation claim | 1,221 | 1 | Test-only GraphQL expected-query helper |

The NetBox candidate uses `tenant__name` in
`netbox/dcim/tests/test_api.py:158`. NetBox documents tenancy as resource
assignment or dependency; the sample therefore checks whether the word
“tenant” alone creates misleading candidates.

## Dynamic result

The django-organizations baseline passed. Running all three root-scan mutants
against
`tests/test_models.py::OrgModelTests::test_remove_user` produced:

| Candidate | Source kind | Result | Causal interpretation |
| --- | --- | --- | --- |
| `src/organizations/abstract.py:128`, `.get(organization=self)` | Production | `KILLED` | Targeted traceback showed `MultipleObjectsReturned` with two rows |
| `tests/test_mixins.py:48`, `.get(organization=self.foo)` | Test | `SURVIVED` | Not a production test gap; the selected test does not use this setup |
| `tests/test_models.py:147`, `.filter(organization__name="Nirvana")` | Test | `SURVIVED` | Not a production test gap; this is another test's own query |

Summary: baseline passed; 3 candidates; 1 killed; 2 survived; 0 execution
errors; CLI mutation score 33.3%. The whole-root baseline plus three mutants
took 3.77 seconds on the author's machine. The
[`django-organizations result`](../django-organizations/result.json) is the
recorded observation, not an independently self-verifying artifact.

Seven warm discovery runs over dependency-free snapshots had median wall times
of 0.047 seconds (django-organizations), 0.028 seconds
(django-multitenant), 0.006 seconds (django-scopes), and 2.580 seconds
(NetBox). Timings are environment observations, not reproducibility checks or
performance guarantees.

Every checkout had an empty `git status --porcelain` before and after its scan;
django-organizations was also clean after mutation execution. Git already
provides the corpus integrity check needed here, so no additional verification
framework was added.

## Reproduce

Extract TenantKiller at the fixed revision as shown in the
[django-organizations steps](../django-organizations/README.md), clone each
repository at its full commit above, and run:

```bash
TENANTKILLER_SOURCE=/absolute/path/to/tenantkiller-at-3d4f20e/src
UPSTREAM_CHECKOUT=/absolute/path/to/upstream-checkout
git -C "$UPSTREAM_CHECKOUT" status --porcelain
PYTHONPATH="$TENANTKILLER_SOURCE" python3 -m tenantkiller \
  list "$UPSTREAM_CHECKOUT" --json
git -C "$UPSTREAM_CHECKOUT" status --porcelain
```

For the dynamic whole-root observation, follow the fixed-revision environment
steps in [`../django-organizations/README.md`](../django-organizations/README.md),
then run:

```bash
UPSTREAM_CHECKOUT=/absolute/path/to/django-organizations
"$UPSTREAM_CHECKOUT/.venv/bin/tenantkiller" \
  run --json --timeout 120 "$UPSTREAM_CHECKOUT" -- \
  "$UPSTREAM_CHECKOUT/.venv/bin/python" -m pytest \
  tests/test_models.py::OrgModelTests::test_remove_user \
  -q --no-cov --tb=short
```

Exit status 1 is expected here because the two test-file mutants survive.

A pinned commit is not trusted code. Dependency installation and test execution
can run arbitrary third-party code and should happen in a disposable VM or
container. TenantKiller's temporary copy is not a security sandbox.

## Decision rule

Do not broaden the operator or build infrastructure. First inspect five real
applications that manually repeat supported tenant predicates. Resume product
work only if at least three contain multiple genuine production candidates and
at least one maintainer prefers this workflow to a manual edit or an existing
mutation runner. If not, keep the repository as research evidence or move the
operator into an established tool.
