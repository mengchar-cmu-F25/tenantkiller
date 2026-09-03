# Synthetic Django scenarios

Two scenarios use six invented orders across three tenants, real Django ORM,
and process-local in-memory SQLite. No real repository, external database,
credentials, or service is involved. The published `django_tenants` demo is
unchanged.

Run from a source checkout with Python 3.11 or newer:

```bash
python -m pip install -e . "Django>=5.2,<5.3"
python -m unittest discover -s tests -p test_django_scenarios.py -v
```

The two automated tests run four passing baselines and four selected mutations.
They check the expected failure output and compare original example contents
before and after every run. Missing Django fails the tests; scenarios are not
silently skipped.

| Scenario | Mutation ID | Weak check | Additional strong check |
|---|---|---|---|
| Open orders | `TK-3358E4FD62` | Own order appears; all returned rows remain open and not deleted | Returned tenant IDs must be exactly `{1}` |
| Single order | `TK-E5B0D03C67` | Two own-tenant primary keys still select their requested rows | Tenant 1 must not retrieve Tenant 2's order `201` |

The weak checks also prove that `status`, `deleted`, and `pk` conditions remain
effective after removing `tenant_id`. Both weak suites **SURVIVE** their
selected mutation. Both strong suites **KILL** the same mutation while retaining
the weak business checks.

To inspect the actual CLI reports:

```bash
tenantkiller list examples/django_scenarios
tenantkiller run --select TK-3358E4FD62 --show-output examples/django_scenarios -- python -m unittest -q checks.WeakFilterTest
tenantkiller run --select TK-3358E4FD62 --show-output examples/django_scenarios -- python -m unittest -q checks.StrongFilterTest
tenantkiller run --select TK-E5B0D03C67 --show-output examples/django_scenarios -- python -m unittest -q checks.WeakGetTest
tenantkiller run --select TK-E5B0D03C67 --show-output examples/django_scenarios -- python -m unittest -q checks.StrongGetTest
```

Each weak command intentionally exits `1`; each strong command exits `0`.
Strong output identifies `foreign tenants leaked into open orders` or
`DoesNotExist not raised ... foreign order must remain invisible`.
These controlled examples validate the mutation workflow, not a production
application's tenant safety. All fixture data and code use the repository's
MIT license.
