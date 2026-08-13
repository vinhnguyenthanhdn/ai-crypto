## What this changes

<!-- One or two sentences. If it closes an issue, write "Closes #N". -->

## The behaviour it fixes

<!-- Name the wrong behaviour and the correct one. "flat blocks return a swing point
     instead of None" beats "improves swing detection". -->

## How you verified it

<!-- Paste the commands you ran and their output. A diff that looks right is not
     evidence; a run is. -->

```
```

## Effect size

<!-- For a change to a signal, an indicator, or a cost model: how many calls or bars
     change result, out of how many. Paste the script or snippet you measured with.
     "No measurable change" is a valid answer and worth knowing. -->

## Scope

Files touched:

<!-- List them. Fixing one behaviour should touch the lines implementing that
     behaviour, plus a test. A pull request that rewrites a whole module to fix one
     comparison will be sent back, even when the rewrite is good, because it cannot be
     reviewed against the issue it claims to close. -->

## Checklist

- [ ] Every `scripts/test_*.py` passes: `for f in scripts/test_*.py; do PYTHONPATH=. .venv/bin/python "$f" || echo "FAIL $f"; done`
- [ ] The new behaviour has a test, and that test fails on `main`
- [ ] No public function was removed or had its signature changed
- [ ] No new dependency, or the PR says why one is needed
- [ ] If any published number in `docs/` is now invalid, the PR says which
