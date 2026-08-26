"""
Valve Standards — soft-coded constants
───────────────────────────────────────
Single source of truth for every choice/enum used across models, serializers,
views, and the loader command. Add a new unit/section/product-form here only
— nowhere else in this app should hardcode these lists.
"""

# ── Material family (derived from group_no prefix, e.g. '1.1' -> family 1) ──
FAMILY_CARBON_LOW_ALLOY = 1
FAMILY_STAINLESS = 2
FAMILY_NICKEL_ALLOY = 3

FAMILY_NAMES = {
    FAMILY_CARBON_LOW_ALLOY: 'carbon/low-alloy steel',
    FAMILY_STAINLESS: 'stainless steel',
    FAMILY_NICKEL_ALLOY: 'nickel alloy',
}
FAMILY_CHOICES = [(k, v) for k, v in FAMILY_NAMES.items()]

# ── Product forms (Table 1 material specs) ──────────────────────────────────
PRODUCT_FORM_FORGING = 'forging'
PRODUCT_FORM_CASTING = 'casting'
PRODUCT_FORM_PLATE = 'plate'
PRODUCT_FORM_BAR = 'bar'
PRODUCT_FORM_TUBULAR = 'tubular'
PRODUCT_FORM_CHOICES = [
    (PRODUCT_FORM_FORGING, 'Forging'),
    (PRODUCT_FORM_CASTING, 'Casting'),
    (PRODUCT_FORM_PLATE, 'Plate'),
    (PRODUCT_FORM_BAR, 'Bar'),
    (PRODUCT_FORM_TUBULAR, 'Tubular'),
]

# ── Pressure-temperature rating (Table 2) ────────────────────────────────────
CLASS_SECTION_STANDARD = 'A'
CLASS_SECTION_SPECIAL = 'B'
CLASS_SECTION_CHOICES = [
    (CLASS_SECTION_STANDARD, 'Standard'),
    (CLASS_SECTION_SPECIAL, 'Special'),
]

TEMP_UNIT_C = 'C'
TEMP_UNIT_F = 'F'
TEMP_UNIT_CHOICES = [(TEMP_UNIT_C, 'Celsius'), (TEMP_UNIT_F, 'Fahrenheit')]

PRESSURE_UNIT_BAR = 'bar'
PRESSURE_UNIT_PSIG = 'psig'
PRESSURE_UNIT_CHOICES = [(PRESSURE_UNIT_BAR, 'bar'), (PRESSURE_UNIT_PSIG, 'psig')]

# ── Length/diameter unit (Tables 3, 4, Appendix A-1) ─────────────────────────
LENGTH_UNIT_MM = 'mm'
LENGTH_UNIT_IN = 'in'
LENGTH_UNIT_CHOICES = [(LENGTH_UNIT_MM, 'mm'), (LENGTH_UNIT_IN, 'in')]

# ── Default standard seeded by the loader ────────────────────────────────────
DEFAULT_STANDARD_CODE = 'ASME_B16_34'
DEFAULT_STANDARD_TITLE = 'Valves — Flanged, Threaded, and Welding End'
DEFAULT_STANDARD_EDITION_YEAR = 2004

# ── Bundled seed data (relative to this app's directory) ────────────────────
SEED_DATA_RELATIVE_PATH = 'data/consolidated_asme_b16_34.json'

# ── ASME B31.3 (Process Piping) — allowable stresses, quality factors, ──────
# ── physical properties. Same Standard-scoped pattern as B16.34 above. ──────
B31_3_STANDARD_CODE = 'ASME_B31_3'
B31_3_STANDARD_TITLE = 'Process Piping'
B31_3_STANDARD_EDITION_YEAR = 2020
B31_3_SEED_DATA_RELATIVE_PATH = 'data/consolidated_asme_b31_3.json'

# ── Bulk-insert batch size for the large ratings table (24k+ rows) ──────────
BULK_CREATE_BATCH_SIZE = 2000

# ── ASME B16.5 (Pipe Flanges and Flanged Fittings) — same Standard-scoped ───
# ── pattern as B16.34/B31.3 above. MaterialGroup rows are NOT shared across
# ── standards even where group numbers coincide (e.g. '1.1') — each Standard
# ── owns its own MaterialGroup set (see the unique_together fix on
# ── MaterialGroup, migration 0004).
B16_5_STANDARD_CODE = 'ASME_B16_5'
B16_5_STANDARD_TITLE = 'Pipe Flanges and Flanged Fittings'
B16_5_STANDARD_EDITION_YEAR = 2017
# Supporting extracted-data JSON is intentionally NOT bundled under this
# app's data/ directory — it lives next to the source PDF. The loader
# requires --source (or the env var below) to point at it.
B16_5_SOURCE_DATA_ENV_VAR = 'ASME_B16_5_DATA_PATH'
