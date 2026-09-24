# Specification extraction and rating coverage

Release review, 24 September 2026, corrected two conditions introduced by remote
commit `ca374e83` without changing engineering ratings, approval policy or schema.

Running-header detection requires the same normalized line on at least two
distinct pages and the configured fraction of the chunk's pages. Numeric source
values remain distinct. Repeated lines are removed only in the configured page
boundaries; explicit page-counter patterns still apply. Single-page content is
preserved when it does not match those explicit patterns.

The existing authenticated class `asme-validation/` read returns `skipped` with
`method: no_data`, `allowed_bar_g: null` and `ok: null` for points outside the
reference table. Explicit printed temperature intervals retain their stated
coverage; bounded interpolation remains available. An incomplete table cannot
receive an overall `pass`. A confirmed exceedance still produces `fail`, with
uncovered points retained separately. No approval or engineering certification
is implied by this advisory response.

Verification: the 11 existing specification tests and 11 new boundary regressions
pass under `config.settings_release_test` on Python 3.13 and the production image's
Python 3.11. The latter uses a read-only source mount, temporary logs/storage,
SQLite and `--network none`. These checks do not validate live reference data,
provider extraction or database migration application.

```powershell
..\.venv\Scripts\python.exe manage.py test apps.spec_customization.tests apps.spec_customization.tests_release_boundaries --settings=config.settings_release_test --noinput
```
