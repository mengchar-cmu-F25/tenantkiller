# Recorded observation: django-organizations

This is one manually reproducible observation, not a security proof or a
self-verifying benchmark. It fixes both inputs:

- TenantKiller `3d4f20ee406685e03f85ecc6386893f9f0d12023`
- `bennylope/django-organizations`
  `f8953c4ad568ee5ad5113a3a2aa762c0053bc273`

The production query at `src/organizations/abstract.py:128` calls
`.get(user=user, organization=self)`. The existing
`OrgModelTests.test_remove_user` fixture gives the same user memberships in two
organizations. Removing `organization=self` was observed to raise
`OrganizationUser.MultipleObjectsReturned` with two rows in that test.

Repository-root discovery found three candidates: this production query and
two queries in test files. Running all three against the targeted test reported
one killed and two surviving mutants. The survivors were test code that the
selected test did not execute, so they are scan noise rather than production
isolation gaps.

[`result.json`](result.json) records the observed environment and failure. It
does not verify itself or establish that django-organizations, its full test
suite, or an application using it is tenant-safe.

## Reproduce manually

Run this in a disposable VM or container. A temporary clone protects an
existing checkout from the source edit, but it does not sandbox build hooks or
third-party test code from the host.

```bash
TENANTKILLER_ROOT=/absolute/path/to/tenantkiller
OBSERVATION_TMP="$(mktemp -d)"
mkdir "$OBSERVATION_TMP/tenantkiller"

git -C "$TENANTKILLER_ROOT" archive \
  3d4f20ee406685e03f85ecc6386893f9f0d12023 \
  | tar -x -C "$OBSERVATION_TMP/tenantkiller"

git clone https://github.com/bennylope/django-organizations.git \
  "$OBSERVATION_TMP/django-organizations"
UPSTREAM_CHECKOUT="$OBSERVATION_TMP/django-organizations"
git -C "$UPSTREAM_CHECKOUT" checkout \
  f8953c4ad568ee5ad5113a3a2aa762c0053bc273
test -z "$(git -C "$UPSTREAM_CHECKOUT" status --porcelain)"

(
  cd "$UPSTREAM_CHECKOUT"
  uv sync --frozen --python 3.13.9 --group tests
)
uv pip install --python "$UPSTREAM_CHECKOUT/.venv/bin/python" \
  "$OBSERVATION_TMP/tenantkiller"

"$UPSTREAM_CHECKOUT/.venv/bin/tenantkiller" \
  list "$UPSTREAM_CHECKOUT" --json
(
  cd "$UPSTREAM_CHECKOUT"
  .venv/bin/python -m pytest \
    tests/test_models.py::OrgModelTests::test_remove_user \
    -q --no-cov --tb=short
)
```

The list must contain `TK-0CFF6996D8` at
`src/organizations/abstract.py:128`. Apply the one reviewed edit inside the
disposable clone:

```bash
git -C "$UPSTREAM_CHECKOUT" apply <<'PATCH'
diff --git a/src/organizations/abstract.py b/src/organizations/abstract.py
--- a/src/organizations/abstract.py
+++ b/src/organizations/abstract.py
@@ -125,5 +125,5 @@
         """
         Deletes a user from an organization.
         """
-        org_user = self._org_user_model.objects.get(user=user, organization=self)
+        org_user = self._org_user_model.objects.get(user=user)
         org_user.delete()
PATCH

git -C "$UPSTREAM_CHECKOUT" diff --check
git -C "$UPSTREAM_CHECKOUT" diff -- src/organizations/abstract.py
(
  cd "$UPSTREAM_CHECKOUT"
  .venv/bin/python -m pytest \
    tests/test_models.py::OrgModelTests::test_remove_user \
    -q --no-cov --tb=short
)
```

The observed mutant exited 1 and its traceback named the edited line,
`OrganizationUser.MultipleObjectsReturned`, and two matching rows. Finally,
restore only the disposable edit and confirm the checkout is clean:

```bash
git -C "$UPSTREAM_CHECKOUT" restore --source=HEAD -- \
  src/organizations/abstract.py
test -z "$(git -C "$UPSTREAM_CHECKOUT" status --porcelain)"
```
