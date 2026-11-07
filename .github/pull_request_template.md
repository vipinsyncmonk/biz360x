## What and why

<!-- One or two sentences. Link the issue: Closes #N -->

## Checklist

- [ ] `bash scripts/ci-local.sh` passes (see docs/DEV_SETUP.md)
- [ ] New endpoint? Added to `api/build_openapi.py`, regenerated `api/openapi.yaml`, linted
- [ ] New endpoint has an end-to-end test (happy path + one problem code)
- [ ] Screen -> endpoint map in `docs/API_GUIDE.md` updated if a screen uses it
- [ ] Schema change is a new numbered migration (no edits to applied files)
- [ ] Money is `Decimal`/two-decimal string, writes call `audit()` and `emit()` in the same transaction
- [ ] Any Indian-law figure added or changed is labelled for CA/CS review
