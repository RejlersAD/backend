# SALES-CLIENT-CRUD-01 — Client fields and CRUD

The existing `/api/v1/sales/clients/` POST/list and detail GET/PUT/PATCH/DELETE
remain the authoritative CRUD routes, guarded by their existing action grants
and Sales record visibility. Create/update supports company_name, legal_name,
industry_type, city, website, account_manager, country and currency, alongside
the existing client fields. No verification/approval bypass is added.

Sales migration 0033 adds optional currency (three ASCII letters, normalized to
uppercase, or blank). Existing clients remain blank until reviewed; it is an
account preference and changes no opportunity, proposal or accounting amount.
Detail and list projections return currency; list also includes city.

GET `clients/form-options/` returns industry `{value,label}` choices from the
model, active account-owner `{value,label}` choices under existing Sales owner
visibility and default_owner. Newly assigned owners must be in this projection;
an unchanged historical assignment is retained. A null account_manager on PATCH
unassigns ownership; existing create behavior defaults a missing/null owner to
the actor. No new access role, organization policy or department filter is added.

The browser uses the current full record for editing and refreshes detail/list
after saving. Create includes all requested fields and selects the created client.
Delete is in More client actions, requires explicit confirmation and clears the
deleted selection after HTTP 204. A registered document workspace retains its
existing 409 boundary. Any other related business/history record also prevents
deletion with HTTP 409; setting status inactive preserves that client and links.
Validation/permission/conflict errors preserve the draft or confirmation state.

Tests use synthetic records and production HTTP guards; browser checks use the
real Sales route/Layout and isolated API fixtures. Currency rollout requires
migration 0033 before restarting application workers. No real client is deleted
by verification and production deployment remains separate.
