# BUHASA matching profile

The field-review follow-up is specified in
`../../docs/features/s3d-review-field-enhancements.md`. Canonical editing of an
existing legacy decision explicitly upgrades its batch to BUHASA. Approval may
include a validated `source_reference` (page, optional table ID and box with
coordinate space), saved atomically with review and bound to this job's actual
document/actor. The existing decision JSON and snapshot inputs retain this
metadata; absent references preserve the legacy shape. No coordinates or
table IDs are invented when extraction supplies only a page.

The current contract is recorded in workspace
`../../docs/features/s3d-buhasa-matching.md`. Legacy routes remain compatible;
there is no migration or automatic engineering approval.

Use `POST matching-run/` with `codelist_profile: "BUHASA"`. Evidence appears in
`discriminators_matched.codelists`. Resolve fields using
`POST matching-decision-review/` with `review_decision: "normalized"` and
`codelist_selections` containing complete dictionary entries. BUHASA commodity
approvals/remaps require an eligible `candidate_id` and commodity code.
Unresolved codelists or row selections block freeze and released downloads.

Both JSON files in `apps/spec_customization/data/` must remain byte-identical
to generated frontend assets. Reload backend processes after updating them.
Their fingerprint participates only in BUHASA snapshot inputs; existing
non-BUHASA frozen manifest construction remains unchanged.

Tests: `python manage.py test apps.spec_customization.tests_buhasa_codelists
--settings=config.settings_release_test --noinput`, in the isolated environment
specified by workspace CONTRIBUTING. No live database test is authorized.
