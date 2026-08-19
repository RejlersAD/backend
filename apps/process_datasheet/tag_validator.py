"""
Soft-coded tag validation for engineering instruments and valves.

Configuration is driven by VALID_TAG_PREFIXES and DEMO_PREFIXES — edit these
lists without touching any other code when new tag formats are introduced.
"""

# ─── Configurable: Add new valid prefixes here ──────────────────────────────
VALID_TAG_PREFIXES = [
    # Valves
    'MOV', 'SDV', 'XV', 'FV', 'LV', 'PCV', 'PSV', 'PRV', 'BDV', 'PZV',
    'EV', 'HV', 'TV', 'CV', 'RV', 'SV', 'NV', 'GOV', 'SOV', 'AOV',
    # Instruments — pressure
    'PT', 'PIT', 'PIC', 'PC', 'PDT', 'PDIT', 'PDIC',
    # Instruments — flow
    'FT', 'FIT', 'FIC', 'FC', 'FE', 'FM',
    # Instruments — level
    'LT', 'LIT', 'LIC', 'LC', 'LE', 'LG',
    # Instruments — temperature
    'TT', 'TIT', 'TIC', 'TC', 'TE', 'TG',
    # Instruments — analysis / general
    'AT', 'AIT', 'AIC', 'AC',
    'XI', 'XII', 'XIC',   # misc discrete inputs
    'PG',                  # pressure gauge
    'ZT', 'ZIT',           # position transmitters
    # Other
    'RO', 'HC', 'HS', 'SS', 'WIT', 'WIC',
]

# ─── Configurable: Prefixes that identify DEMO / test / mock data ────────────
DEMO_PREFIXES = [
    'DEMO-', 'DEMO_',
    'TEST-', 'TEST_',
    'MOCK-', 'MOCK_',
    'SAMPLE-', 'SAMPLE_',
    'DUMMY-', 'DUMMY_',
    'PLACEHOLDER-',
]


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_tag(valve: dict) -> str:
    """Return the best tag string from a valve dict."""
    return (valve.get('tag_no') or valve.get('tag') or '').strip()


def is_demo_tag(tag: str) -> bool:
    """Return True if the tag looks like placeholder / demo data."""
    tag_upper = (tag or '').upper().strip()
    return any(tag_upper.startswith(p) for p in DEMO_PREFIXES)


def has_valid_engineering_prefix(tag: str) -> bool:
    """
    Return True if the tag starts with a known engineering prefix
    followed by '-' or '_' (e.g. MOV-8001, PG_100).
    """
    tag_upper = (tag or '').upper().strip()
    return any(
        tag_upper.startswith(p + '-') or tag_upper.startswith(p + '_')
        for p in VALID_TAG_PREFIXES
    )


def validate_and_filter_valves(valves: list) -> tuple:
    """
    Filter out DEMO/mock valves and collect warning messages.

    Returns:
        (valid_valves, warnings)  — valves without DEMO tags, plus warning strings
    """
    valid = []
    warnings = []

    for valve in valves:
        tag = get_tag(valve)

        if is_demo_tag(tag):
            warnings.append(
                f"Tag '{tag}' has a DEMO/mock prefix and was excluded from results. "
                "Real extraction may have failed — check OpenAI Vision API configuration and PDF quality."
            )
        else:
            if tag and not has_valid_engineering_prefix(tag):
                warnings.append(
                    f"Tag '{tag}' does not match any known engineering prefix — included with caution."
                )
            valid.append(valve)

    return valid, warnings
