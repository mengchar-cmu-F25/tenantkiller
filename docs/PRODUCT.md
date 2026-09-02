# TenantKiller: one-page product definition

## Decision: HOLD

TenantKiller is a research prototype for one question: **if a Django query lost
an explicit tenant or organization keyword, would a chosen test notice?** One
manually inspected run produced the intended failure, but the first small
corpus shows too little applicability and too much root-scan noise to justify
more product code, new operators, packaging, or deployment.

## Intended user and job

The possible user is a Django security or platform engineer working on a
shared-schema SaaS application that manually repeats `tenant`, `organization`,
`org`, or `company` predicates and already has reliable targeted tests. They
want a repeatable alternative to hand-editing a query, not a certificate that
the application is tenant-safe.

The minimal workflow is:

1. Review syntactically discovered candidates.
2. Remove one supported keyword in a temporary filesystem copy.
3. Run a user-supplied test command after a passing baseline.
4. Inspect the exact failure before calling a `KILLED` result causal; inspect
   the source before calling a `SURVIVED` result a test gap.

The temporary copy is not a sandbox. The test command can access the network,
databases, services, absolute paths, and anything allowed to the invoking user.

## Difference hypothesis

A missing tenant predicate is more explainable and security-relevant than a
generic mutation such as changing a constant. That may make one tiny,
domain-specific operator useful even when broad mutation tools already exist.
This remains a hypothesis: TenantKiller has not shown that teams prefer it to a
manual edit or a custom operator in an established mutation runner.

## Evidence

Using TenantKiller revision `3d4f20ee406685e03f85ecc6386893f9f0d12023`
against `bennylope/django-organizations@f8953c4`, removing
`organization=self` from a production `.get()` made the existing targeted test
fail at the edited line with `OrganizationUser.MultipleObjectsReturned` and two
matching rows. This is a recorded, manually reproducible observation—not a
self-verifying proof—and establishes only that one test fixture catches one
edit.

The [small pinned corpus](../validation/corpus/README.md) is the more important
result. `django-multitenant` documents shared-table tenant context, while
`django-scopes` documents a multi-tenant query safeguard; both yielded zero
candidates because their scoping mechanisms are outside this operator. A
repository-root scan of django-organizations yielded three candidates, but two
were in test code; the CLI reported them as survivors even though they were not
production isolation gaps. A 1,221-file NetBox vocabulary control yielded one
test-only candidate. These are observations from four repositories, not
population estimates, but they invalidate any broad “Django tenant scanner”
claim.

## v0.1 boundary

Version 0.1 only recognizes tenant-like keyword arguments on method calls named
`.filter()` and `.get()`. It does not establish that the receiver is a Django
queryset. It misses custom scope names, automatic managers, `Q` objects,
positional predicates, SQL, middleware, tasks, caches, and database-enforced
isolation. Any non-zero mutant command is labeled `KILLED`, including unrelated
or flaky failures. Root scans also include test code, and the CLI cannot yet
select an individual reviewed candidate.

## Only justified next step

Do not add code. Find five real applications in the intended niche and manually
review the current operator's production candidates. Continue as a standalone
project only if at least three applications contain repeated, genuinely
tenant-isolating candidates and at least one maintainer says this workflow is
preferable to a manual edit or existing mutation tooling. Otherwise stop the
standalone product and retain this repository as a compact research artifact or
contribute the operator to an established mutation tool.

## Non-goals

TenantKiller must not claim formal tenant isolation, vulnerability detection,
security certification, safe execution of third-party tests, or protection of
external state. A mutation score alone is not security evidence.
