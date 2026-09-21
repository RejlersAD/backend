# Invoice workbook summary

`invoice_workbook_summary.json` is an aggregate snapshot of the supplied workbook,
not a live database total. Finance and Executive Financial Performance share this
snapshot. The outgoing-invoice read permission is checked before the file is read.
Company, currency, period and reference-date filters do not change its scope.

The snapshot includes every invoice row from `External Invoice ` (the sheet name
has a trailing space), rows 6 through 4409. Duplicate invoice numbers remain
separate rows. Later worksheet totals are excluded. Payment-status groups use
column R with surrounding whitespace and letter case normalized; the live
register's derived statuses are not substituted.

- L: numeric Invoice Amount cells, in mixed original currencies.
- M: numeric cached Inv Amt. (AED) cells, in AED. The known subtotal excludes two
  error cells and five blanks.
- AA: numeric Actual Payment Received cells, in mixed original currencies.
- G: distinct recorded RAD Project codes, excluding blank, error and `N/A` cells.

Text amounts are not parsed as money, formulas are not recalculated, and rounding
is applied only after summing numeric cells with Decimal. Coverage records the
numeric, blank, text and error cell counts for each amount column. Only aggregates
and provenance are packaged; invoice numbers, customer names and project codes
are not retained.

The L and AA currency breakdowns are classified independently from each amount
cell's explicit Excel currency format and column AE (`Inv. CUR`). When both
sources state a code, they must agree; disagreements are retained in a separate
conflict group. When only one source states a code it is used. Missing, invalid
and error currencies remain explicit groups. Generic numeric/date formats and
ambiguous dollar symbols do not imply a currency, and AA never inherits L's
currency. Explicit aliases such as the euro symbol and lowercase codes are
normalized; no exchange rates or amount ratios are used.

Each currency group includes separate L/AA row counts, numeric/blank/text/error
coverage, rounded amounts and exact Decimal sums. An amount is null when that
group has no numeric cells; numeric zero remains zero. Each field's group counts
and coverage reconcile to all invoice rows. Exact sums reconcile to the existing
headline totals; `currency_rounding_adjustment` records any difference introduced
by summing separately rounded group amounts. A source conflict retains its amount
in the full-workbook total without assigning it to an unverified currency.

Payment status amounts use the numeric cached M (`Inv Amt. (AED)`) values, so all
status rows share the AED basis. Each row includes its known subtotal, exact
Decimal sum and numeric/blank/text/error coverage. Missing amounts remain null;
recorded zero and negative amounts are preserved. Group coverage and exact sums
reconcile to the full M total. `payment_status_rounding_adjustment` records any
difference between separately rounded status amounts and the headline AED total.

To regenerate from the same source, run from the backend directory:

```powershell
python manage.py generate_invoice_workbook_summary "C:\path\Updated!Invoice Tracking _MASTER FILE- Latest (2).xlsx" --last-row 4409 --snapshot-at "2026-09-21T13:50:49.630323+00:00" --output apps/finance/data/invoice_workbook_summary.json
```

For a replacement workbook, provide its verified last invoice row and omit
`--snapshot-at` to capture the new UTC timestamp. Review the new source hash,
coverage and counts and update the snapshot regression assertions before release.
Commit and deploy the reviewed JSON to publish a replacement snapshot; changing
the source spreadsheet alone does not update the dashboard.
The command writes only the requested JSON artifact; it does not import or update
invoice records and requires no database migration.
