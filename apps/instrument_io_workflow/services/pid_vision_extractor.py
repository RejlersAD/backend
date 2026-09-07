"""BYOK Vision-AI instrument/equipment/line tag extractor for P&ID drawing
PDFs uploaded into the I/O List module.

The user's own API key is passed in per-request and never stored
server-side (Bring-Your-Own-Key) — same contract as
``apps.pid_checker_v2.services.vision_extractor``. This module is an
independent reimplementation, not an import from that app — I/O List
stays self-contained (see this module's own module-level history in
services/legend_comparison.py for why). It reuses the *proven prompt
structure and call pattern* from
``apps.pid_checker_v2.services.instrument_vision_extractor`` (ISA-5.1
symbol shapes, hyphen-preservation rules, exhaustiveness instructions)
rather than reinventing it.

Two scan modes (thorough=False/True — see extract_pid_tags_from_page):
Quick Scan sends the full-page image per page. Thorough Scan additionally
splits the page into 2x2 overlapping tiles and sends each tile as its own
call, for pages dense enough that a single full-page image loses small
balloons — independent reimplementation of the same overlapping-tile
approach apps.pid_checker_v2.services.symbol_shape_extractor already uses
for its own quick/thorough toggle, not shared code. Both modes make
VISION_PASSES independent calls per image (page, or tile) rather than
one — a real, confirmed run-to-run variance report (~600 vs ~550 tags on
the same PDF under Thorough Scan) showed a single Vision call isn't
perfectly deterministic; a second independent look at the same image
catches what the first missed, combined and deduped rather than trusted
alone.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

from .config import IO_LIST_CANONICAL_COLUMNS

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = None


# ─── Soft-coded config ────────────────────────────────────────────────
SUPPORTED_PROVIDERS = ('claude', 'openai')

VISION_MODELS = {
    'claude': 'claude-sonnet-5',
    'openai': 'gpt-4o',
}

VISION_RENDER_DPI = 220
VISION_MAX_DIMENSION_PX = 2200

# Thorough Scan tiling — 2x2 overlapping crops of the same rendered page,
# each sent as its own Vision call, in place of (not in addition to) the
# single full-page call Quick Scan makes. A tag/symbol sitting in the
# overlap band between adjacent tiles can legitimately appear in both —
# handled by _dedupe_rows() after every tile's results are combined, not
# by trying to draw a seam that never double-covers anything.
VISION_TILE_ROWS = 2
VISION_TILE_COLS = 2
# Run the full tile grid this many times per page and combine every
# pass's results (deduped) rather than trusting a single pass. Fix for a
# real, confirmed run-to-run variance report: the SAME PDF under Thorough
# Scan returned ~600 tags on one run and ~550 on another — Vision reads
# are not perfectly deterministic call to call, so a tag a single pass
# happens to miss (faint, rotated, near a fold) is often caught by a
# second independent look at the same image. Applies to BOTH scan modes:
# Quick Scan now makes this many calls on the full page image (was
# exactly 1), Thorough Scan makes this many independent passes over the
# whole tile grid (VISION_TILE_ROWS * VISION_TILE_COLS * this per page).
# Trades cost/latency for exactly the "maximum tags found, more
# consistent" outcome this was asked for.
VISION_PASSES = 2
# Widened from 0.12 to 0.20 — a tag/symbol balloon sitting near a tile
# boundary could still land close enough to an edge to be clipped/
# illegible in both adjacent tiles at the old overlap; a wider shared
# band makes it far more likely at least one tile shows it whole and
# legible. Still fine to double-count near the boundary — _dedupe_rows()
# already collapses the same tag reported by more than one tile.
VISION_TILE_OVERLAP_FRAC = 0.20
# Tiles are already a crop of the full page, so each one needs less
# downscaling than the whole-page call to stay legible — same cap as the
# full-page path is fine since a tile's native resolution before downscale
# is already smaller than the full page's.
VISION_TILE_MAX_DIMENSION_PX = 2200
# 4096 was too tight even with thinking disabled; 8192 still truncated
# mid-array on a dense drawing (confirmed live — Claude's JSON was cut off
# mid-object, closing brackets missing). 16384 was sized for tags alone;
# now that the prompt also asks for every untagged symbol (valves,
# instrument/equipment/piping symbols — often far more numerous than
# tagged items on a dense P&ID), the JSON array can be considerably
# longer again. Raised to 32768 for headroom against that.
# _recover_truncated_json_array() below remains the backstop for whatever
# this still isn't enough for — a truncated response no longer means
# losing every tag/symbol Claude did read before the cutoff.
VISION_MAX_TOKENS = 32768
# 45s (this module's original value, copied from pid_checker_v2's own
# vision_extractor — but that module caps max_tokens at 4096, far below
# this one's 32768) proved too tight in real use — confirmed live via a
# genuine httpx.ReadTimeout on a dense page, not a hypothetical: output
# token generation time scales with how much the model is ALLOWED to
# generate, and a full 32768-token response can legitimately take
# well over a minute. Raised for real headroom. tasks.py's
# process_pid_vision_page Celery task's own soft_time_limit/time_limit
# were raised to match — keeping them below this value would let Celery
# kill the task before this timeout would even fire on its own.
VISION_REQUEST_TIMEOUT_S = 150.0
TEST_CONNECTION_MAX_TOKENS = 5

# Accept both hyphenated (LT-8019, PCV-8004B) and un-hyphenated tags
# (SDV8005, FIC8002, PI8003A) — many drawings drop the hyphen.
INSTRUMENT_TAG_PATTERN = re.compile(
    r'^[A-Z]{1,4}-?\d{2,4}[A-Z]?(?:[A-Z]{2})?$'
)

# Equipment tags — two real shapes seen on actual P&ID drawings, both
# accepted:
#   plain:        V-101, P-203A, TK-201, E-104B
#   unit-prefixed: 1520-D-103, 1520-EA-102, 1520-K-101 (confirmed present
#                  on a real drawing tested this session — the earlier,
#                  stricter pattern had no unit-prefix option at all and
#                  was silently rejecting every one of these as
#                  "hallucinated/malformed", even though they're genuine
#                  equipment tags Vision read correctly off the page).
# Kept as a loose sanity filter, same philosophy as the other two
# patterns: reject only what's CLEARLY not a tag shape at all (stray
# prose, empty string, wildly wrong character classes) — the authoritative
# format check is the active equipment_register legend downstream, not
# this pattern.
EQUIPMENT_TAG_PATTERN = re.compile(
    r'^(?:\d{2,4}-)?[A-Z]{1,4}-?\d{1,5}(?:-?[A-Z]{1,2})?$'
)

# Line tags (6"-FL-AC6N-8112, 4"-HY-1041-1513HMR-PP) — size (inch mark
# optional) + hyphen + a run of service/serial/spec/insulation segments.
# Intentionally loose, same philosophy as the two patterns above: this is
# a sanity filter against hallucinated garbage, not the authoritative
# format check — that's the active line_numbering legend's job downstream.
LINE_TAG_PATTERN = re.compile(
    r'^\d{1,3}(?:/\d)?"?-[A-Z0-9][A-Z0-9-]{3,38}$'
)

# Tagged kinds (instrument/equipment/line) are validated against their own
# sanity pattern as before. 'symbol' has no text shape to validate at all
# — an untagged valve/piping symbol legitimately has tag == "" — so it's
# handled separately in _parse_tags (requires a non-empty symbol_type
# instead of a tag match).
_TAG_PATTERNS = {
    'instrument': INSTRUMENT_TAG_PATTERN,
    'equipment':  EQUIPMENT_TAG_PATTERN,
    'line':       LINE_TAG_PATTERN,
}

VISION_SYSTEM_PROMPT = (
    "You are an expert piping and instrumentation engineer specialised in "
    "reading P&ID drawings. Your task is to enumerate every unique "
    "instrument tag, equipment tag, line number, and visual symbol "
    "visible on the drawing."
)

VISION_USER_PROMPT = """Identify EVERY unique instrument tag, equipment tag, line number, AND visual symbol on this P&ID image.

Four kinds of item appear on a P&ID — classify each one by its symbol or
context, not just its text shape:

1. INSTRUMENT TAGS — kind: "instrument"
   Read the tag inside one of these balloon shapes (ISA-5.1):
     - Plain CIRCLE                          -> field-mounted instrument
     - CIRCLE cut by a horizontal line       -> main control panel
     - CIRCLE with a DOUBLE horizontal line  -> auxiliary / local panel
     - Circle inside a SQUARE / hexagon      -> DCS / PLC / computer function
   Shape (both valid, drawings often mix them): FUNC-LOOP or FUNCLOOP,
   e.g. PT-1600, XV1004, PSV-1521A.
   FUNC = 1-4 uppercase ISA letters (LT, PT, FT, FIC, LIC, PCV, FCV, LCV,
          PSV, SDV, TIT, FIT, LG, FE, TW, PY, LY, PI, TI, LI, XV, PDT...).
   LOOP = 2-4 digits, optional trailing single letter for parallel duty.
   The hyphen between FUNC and LOOP is OPTIONAL — preserve it exactly as
   drawn, do not insert or delete one.
   ALSO set "symbol_type" to the instrument's full descriptive type (e.g.
   "PRESSURE TRANSMITTER", "FLOW ELEMENT", "LEVEL GAUGE", "TEMPERATURE
   ELEMENT") — leave it "" if you can't tell what kind of instrument it is.
   VALVE-TYPE INSTRUMENTS (FUNC = PSV, PV, FCV, LCV, PCV, SDV, XV, MOV and
   similar) are usually NOT drawn inside a round balloon at all — the tag
   text sits directly beside/above/below the actual valve BODY symbol
   (e.g. a safety-relief-valve shape, a control-valve shape). The tag text
   and that valve body are the SAME physical item, reported ONCE as kind
   "instrument" (tag = the FUNC-LOOP text, symbol_type = what the valve
   body looks like, e.g. "PRESSURE SAFETY VALVE", "CONTROL VALVE"). Do
   NOT also report that same valve body a second time as kind "symbol" —
   e.g. having found tag "PSV1521A" with symbol_type "PRESSURE SAFETY
   VALVE", never ALSO emit a separate untagged {"kind": "symbol",
   "symbol_type": "PRESSURE SAFETY VALVE", "location": "near PSV1521A"}
   for the same valve — that is reporting one physical valve twice, which
   is exactly what section 4 below forbids.
   IMPORTANT — if a unit-number-prefix rule applies to this page (see
   UNIT NUMBER PREFIX below), "tag" MUST be the COMPLETE tag INCLUDING
   that prefix — e.g. "1520-PY-1599B", never just "PY-1599B". This is
   true even when the balloon itself only shows "PY-1599B" and the unit
   number comes from a separate blanket note elsewhere on the page — the
   final answer still has to be the full tag as it would actually be
   written on a real I/O list, prefix included. ALSO separately set
   "unit_prefix" to just that unit number on the same item, for
   cross-checking — both fields must agree (e.g. tag="1520-PY-1599B",
   unit_prefix="1520"). If NO unit-prefix rule applies to this page at
   all, "tag" is just the bare FUNC-LOOP text and "unit_prefix" is "".

UNIT NUMBER PREFIX (applies to every "instrument", "equipment", AND
"line" item above — the real phrasing below explicitly names "piping
specialty items" alongside instruments, and a unit-prefixed line tag is
common too, e.g. a real drawing's own line callout '6"-1520-HY-1025-
61056R-PP' — size, THEN the unit number, then service/serial/spec)
   P&IDs commonly state a blanket rule instead of writing the unit number
   on every single balloon/tag — read the WHOLE page, including the
   numbered NOTES list and the title block, for wording like:
     "ALL INSTRUMENTS PREFIXED BY UNIT NO. XXXX"
     "ALL INSTRUMENTS AND PIPING SPECIALTY ITEMS ON THIS DRAWING WILL BE
      PREFIXED BY UNIT NO. XXXX"  (a real, commonly-seen exact phrasing —
      it is usually ONE numbered item, often #1, in a NOTES list, easy to
      mistake for just another note to ignore — read it anyway)
     "UNIT NO. XXXX"
     "PREFIX: XXXX"
   or similar, where XXXX is a short unit/area number (typically 2-4
   digits, sometimes with letters, e.g. "1520", "16A"). The NOTES list
   also contains many OTHER notes about unrelated things (valve settings,
   design pressures, drawing cross-references, revision history...) — do
   not report those as items, but do scan past them for the one note
   about a unit-number prefix if it's present.
   - If you find such a note: set "unit_prefix" to XXXX (just the number/
     code, nothing else) on EVERY "instrument"/"equipment"/"line" item on
     this page.
   - If a balloon/tag already has its own unit number printed inside or
     beside it (distinct from any blanket note), read whatever it shows
     into "unit_prefix" for THAT item instead.
   - If neither applies to an item, set "unit_prefix" to "" — do not
     invent one.
   This field is informational only; for "instrument" items specifically,
   do not ALSO fold it into "tag" (see above) — something else adds it to
   the final tag from "unit_prefix", so putting it in both places would
   duplicate it. "equipment"/"line" tags are different: read those
   exactly as drawn INCLUDING any inline unit number already part of the
   printed tag (see their own sections below) — "unit_prefix" there is
   only for filling in a MISSING prefix from the blanket note, not for
   ones you can already see.

2. EQUIPMENT TAGS — kind: "equipment"
   Labels beside a major process-unit symbol (vessel, pump, tank, heat
   exchanger, compressor, column) — NOT a small instrument balloon.
   Two shapes, both valid (drawings vary by project):
     plain:         EQUIPCODE-NUMBER[SUFFIX], e.g. V-101, P-203A, TK-201, E-104B.
     unit-prefixed: UNIT-EQUIPCODE-NUMBER, e.g. 1520-D-103, 1520-EA-102, 1520-K-101.
   EQUIPCODE = 1-4 uppercase letters (V=vessel, P=pump, TK=tank,
          E=exchanger, C=compressor, D=drum, T=tower/column, K=compressor/turbine...).
   UNIT = 2-4 digit unit/area number when the drawing prefixes tags with it.
   ALSO set "symbol_type" to the equipment's full descriptive type (e.g.
   "CENTRIFUGAL PUMP", "SUCTION DRUM", "HEAT EXCHANGER") — leave it "" if
   you can't tell.

3. LINE TAGS — kind: "line"
   Callouts printed directly ON a pipe/line (not inside any balloon),
   giving the line's size, service, and spec.
   Shape: SIZE["]-SERVICECODE-SERIAL[-SPEC][-INSULATION], with an
   optional unit number printed right AFTER the size, e.g.
   6"-FL-AC6N-8112, 4"-HY-1041-1513HMR-PP, or (unit-prefixed, a real
   phrasing confirmed on an actual drawing) 6"-1520-HY-1025-61056R-PP.
   The inch mark (") after the size is often present but sometimes
   omitted on the drawing — read the digits either way, preserve the
   mark exactly as drawn (don't add or remove it).

4. UNTAGGED SYMBOLS — kind: "symbol"
   Any visual P&ID symbol below that has NO legible alphanumeric tag next
   to it anywhere nearby — most valves, actuators, signal lines, and
   generic piping fittings fall in this category. Identify it by its
   distinctive shape, not by reading text (there may be none to read). If
   a symbol DOES have a legible tag next to/above/below it (even if not
   literally inside a circle — see VALVE-TYPE INSTRUMENTS in section 1),
   report it as kind "instrument" or "equipment" instead (with
   "symbol_type" set per #1/#2 above) — do NOT ALSO report it here as an
   untagged "symbol". Concretely: before adding a "symbol" item, check
   whether you already reported an "instrument"/"equipment" item for a
   tag located at roughly the same spot — if so, skip it, don't add a
   second entry for it.
     Valve symbols:       Gate, Globe, Ball, Check, Butterfly, Plug,
                           Needle, Diaphragm, Control, Safety/Relief,
                           Shutdown, Motor-operated, Breather,
                           3-way/Mixing, or any other distinct valve body
                           shape you can see — name what it visually is,
                           don't force it into this list if it's clearly
                           something else.
     Actuator symbols:    the operator mechanism drawn ON TOP of a valve
                           body (a separate shape from the valve itself) —
                           Solenoid, Diaphragm, Piston/Cylinder, Rotary
                           motor, Handwheel/manual.
     Instrument symbols:  Pressure/Level/Flow/Temperature gauge or
                           element, orifice plate, sight glass, pilot
                           light, or any other small field-device shape
                           that isn't a numbered instrument balloon.
     Equipment symbols:   Vessel, drum, tank, column, pump, compressor,
                           blower, mixer, heat exchanger, filter, wellhead,
                           flare stack, or any other major process-unit
                           outline with no legible tag beside it.
     Piping/inline symbols: Reducer, flange, blind flange, spade,
                           strainer, sight glass, spectacle blind, tee,
                           barred tee, monitor, or any other small fitting
                           drawn directly on a pipe run.
     Signal line symbols: the LINE STYLE itself (not a fitting) where it
                           visibly changes or is labelled — e.g. a dashed
                           line (pneumatic signal), a line with slash
                           marks (electrical signal), a line with circles
                           (data/software link), or similar — report the
                           signal type it represents (e.g. "PNEUMATIC
                           SIGNAL", "ELECTRICAL SIGNAL") as symbol_type,
                           only when the drawing's own legend/key or a
                           clear convention makes the meaning unambiguous.
   Use the plain, standard engineering name for whatever you actually see
   — the categories above are guidance for what to look for, not an
   exhaustive list to match against; downstream validation (not you)
   reconciles the exact wording against the project's own legend.
   Set "symbol_type" to that name (e.g. "GATE VALVE", "PRESSURE GAUGE",
   "REDUCER", "SOLENOID ACTUATOR", "PNEUMATIC SIGNAL"), "tag" to ""
   (nothing to read), and "location" to a short approximate position on
   the drawing (e.g. "top-left", "near V-101", "on the line from D-103 to
   K-101").

SELF-CONSISTENCY CHECK — a real, observed gap: naming a tag inside
"location" (e.g. "near PY-1599A", "connecting PY-1599A/B/C/D loops")
proves you already read and recognised that tag — but it is easy to stop
there and never ALSO report it as its own separate "instrument"/
"equipment" item, especially when several related tags are grouped
together in one location string like "PY-1599A/B/C/D" (that shorthand
means FOUR separate tags — PY-1599A, PY-1599B, PY-1599C, and PY-1599D —
each of which needs its OWN item in the array, not just a mention inside
someone else's "location"). Before finishing, re-check every tag name
that appears anywhere inside a "location" value: if that exact tag does
not ALSO appear as the "tag" of its own instrument/equipment item
elsewhere in your answer, add one now — don't let a tag exist only as
someone else's location reference.

ACCURACY — read every character exactly as printed. Do NOT guess,
abbreviate, truncate, or autocomplete from a similar-looking tag
elsewhere on the drawing — a tag that's genuinely hard to read is still
better reported as your best actual reading than silently skipped or
padded out with a guess. Before including an item in the final array,
look at it once more and confirm every character of "tag" and every word
of "service"/"symbol_type" against what's actually drawn — a rushed
first-pass read is the most common source of a wrong digit, a dropped
suffix letter, or a cut-off service description.
  - "service": copy the FULL text printed near the tag, not a shortened
    version of it — a real service description can run to a full
    sentence or phrase; do not cut it off partway through or paraphrase
    it into something shorter.
  - "symbol_type": name what the symbol/balloon ACTUALLY looks like on
    THIS drawing, not the most common/likely type for that FUNC code —
    e.g. don't default every "LT" to "LEVEL TRANSMITTER" without looking;
    confirm the balloon/body shape matches that description.
  - "location" (kind "symbol" only): keep this format short and
    consistent — a rough position ("top-left", "middle-right") or a
    nearby-tag reference ("near V-101"), not a long sentence.

SCAN THE ENTIRE IMAGE METHODICALLY, top-left to bottom-right, including
tags near the borders, in corners, and along the edges of the page —
these are the tags most often missed because they sit right at the crop
boundary. A typical P&ID has 20-80 instrument tags, a smaller number of
equipment/line tags, and often many more untagged valve/piping symbols —
do not stop after finding a few of any kind; a partial pass through the
drawing produces a partial, misleading result. Small/faint tags (a
single-letter FUNC, a light pencil-weight balloon, text packed tightly
next to other tags) are still real tags — look for them specifically,
don't only report the large/bold ones that are easy to spot at a glance.

EXCLUDE from the tag/symbol list: drawing/reference numbers, revision
blocks, NOTE/TYPE callouts, title-block text — none of these are
themselves tags or symbols to report as an item. The ONE exception: still
READ any unit-number-prefix note per UNIT NUMBER PREFIX above and use it
to fill "unit_prefix" (and, per that section, "tag" itself) — reading
that note is not the same as reporting it as its own item.

Return ONLY a JSON array of objects — no prose, no markdown fences. Each
object:
  {"kind": "instrument"|"equipment"|"line"|"symbol",
   "tag": "<tag exactly as read, INCLUDING the unit-number prefix when one applies (see UNIT NUMBER PREFIX above) — or \"\" for an untagged symbol>",
   "function_code": "<instrument FUNC or equipment EQUIPCODE, else empty>",
   "symbol_type": "<full descriptive type, e.g. PRESSURE TRANSMITTER, GATE VALVE — empty if unknown>",
   "service": "<the FULL service/description text printed near the tag, not shortened — or empty string>",
   "location": "<short, consistent approximate position on the drawing — only meaningful for kind \"symbol\", else empty>",
   "unit_prefix": "<unit number per UNIT NUMBER PREFIX above, or \"\" — only meaningful for kind \"instrument\"/\"equipment\"/\"line\", else empty>"}

Be exhaustive. Miss nothing — a small tag in a corner is exactly as
important to report as a large one in the middle of the page. Find EVERY
instrument tag, equipment tag, line number, and symbol on this image —
check every corner and edge of the image, including partially-visible
bubbles cut off by the image boundary. Do not skip any bubble because it
looks faint, small, rotated, or overlaps a line — report it anyway with
your best reading. Scan the image systematically (e.g. left to right,
top to bottom) rather than only the areas that visually stand out first,
so nothing in a quiet/sparse region gets overlooked. Be thorough and
complete: a shorter list is only correct if the page genuinely has fewer
tags, never because the scan stopped early.
"""


def _render_page(pdf_bytes: bytes, page_index: int) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        mat = fitz.Matrix(VISION_RENDER_DPI / 72, VISION_RENDER_DPI / 72)
        pix = doc[page_index].get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes('png')))
        logger.info(
            '[IOWF] Rendered P&ID page %d at %d DPI -> %dx%d px. '
            'No preprocessing (contrast/threshold) is applied to this image before Vision.',
            page_index + 1, VISION_RENDER_DPI, img.width, img.height,
        )
        return img
    finally:
        doc.close()


def _downscale(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    resized = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    logger.info(
        '[IOWF] Downscaled image %dx%d -> %dx%d (max_dim=%d)',
        w, h, resized.width, resized.height, max_dim,
    )
    return resized


def _image_to_b64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def _tile_image(img: Image.Image, rows: int, cols: int, overlap_frac: float) -> list[Image.Image]:
    """Splits `img` into rows x cols overlapping crops — Thorough Scan's
    2x2 tiling. Independent reimplementation of the same approach
    apps.pid_checker_v2.services.vision_extractor._tile_image already
    uses; not shared code (see this module's own history in
    legend_comparison.py for why I/O List stays self-contained).

    The overlap band means a tag/symbol sitting near a tile boundary
    lands fully inside at least one tile instead of being cut in half —
    the resulting cross-tile duplicates are handled afterward by
    _dedupe_rows(), not avoided here.
    """
    w, h = img.size
    tile_w = w / cols
    tile_h = h / rows
    ov_w = tile_w * overlap_frac
    ov_h = tile_h * overlap_frac
    tiles: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            left   = max(0, int(c * tile_w - ov_w))
            top    = max(0, int(r * tile_h - ov_h))
            right  = min(w, int((c + 1) * tile_w + ov_w))
            bottom = min(h, int((r + 1) * tile_h + ov_h))
            tiles.append(img.crop((left, top, right, bottom)))
    return tiles


def _call_claude(api_key: str, image_b64: str) -> tuple[str, int]:
    """Returns (raw response text, real token count) — see tokens_used's
    own comment below for where that number comes from."""
    import anthropic
    logger.info(
        '[IOWF] Calling Claude vision: model=%s image_b64_bytes=%d prompt_chars=%d key_prefix=%s...',
        VISION_MODELS['claude'], len(image_b64), len(VISION_USER_PROMPT),
        (api_key or '')[:10],
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    resp = client.messages.create(
        model=VISION_MODELS['claude'],
        max_tokens=VISION_MAX_TOKENS,
        # claude-sonnet-5 is a hybrid-reasoning model that emits extended
        # 'thinking' content blocks by default even though nothing here
        # ever requested them — confirmed directly via the diagnostic
        # logging just added: a real call came back with
        # stop_reason='max_tokens' and content_block_types=['thinking'],
        # meaning the entire max_tokens budget was consumed by thinking
        # tokens and the model was cut off before emitting any of the
        # actual answer (0 chars of text). This task needs a plain
        # read-the-image-and-list-tags answer, not multi-step reasoning,
        # so thinking is explicitly turned off — the SDK's
        # ThinkingConfigDisabledParam shape is exactly {"type": "disabled"},
        # confirmed against the installed anthropic package's own types.
        thinking={'type': 'disabled'},
        system=VISION_SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {
                    'type': 'base64', 'media_type': 'image/png', 'data': image_b64,
                }},
                {'type': 'text', 'text': VISION_USER_PROMPT},
            ],
        }],
    )
    logger.info(
        '[IOWF] Claude vision response: stop_reason=%s usage=%s content_block_types=%s',
        getattr(resp, 'stop_reason', None), getattr(resp, 'usage', None),
        [getattr(b, 'type', None) for b in resp.content],
    )
    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    text = ''.join(parts)
    # Real token count straight off the API response's own usage object —
    # never estimated. input_tokens + output_tokens is Anthropic's own
    # split; missing/malformed usage (shouldn't happen on a successful
    # call, but defensive regardless) degrades to 0 rather than raising —
    # a progress-counter detail must never be able to fail real
    # extraction work.
    usage = getattr(resp, 'usage', None)
    tokens_used = (getattr(usage, 'input_tokens', 0) or 0) + (getattr(usage, 'output_tokens', 0) or 0) if usage else 0
    # Log the FULL response, not a truncated preview — a truncated log was
    # useless for diagnosing exactly the failure mode below (empty text),
    # and even a full 32768-token response is only ~130KB of log text.
    logger.info('[IOWF] Claude vision raw text (%d chars): %s', len(text), text)

    if not text.strip():
        # A successful API call (no exception) that nonetheless produced
        # ZERO text content is not a "no tags on this page" result — it's
        # an abnormal response shape, and silently returning '' here let it
        # flow straight into _parse_tags(''), which logs a generic "could
        # not recover any JSON array" warning with no clue WHY the response
        # was empty, and the page quietly contributes 0 rows with no error
        # anywhere. Previously this exact shape (stop_reason='max_tokens',
        # content_block_types=['thinking'], 0 text chars) was caused by
        # unrequested extended thinking consuming the whole budget — now
        # that thinking is explicitly disabled it should not recur, but if
        # it (or anything else) produces an empty response again, fail
        # loudly here with everything needed to diagnose it in one place,
        # instead of a step downstream with the cause already lost. This
        # raises, which the per-page caller already logs at ERROR with
        # exc_info=True and any HTTP status code.
        block_dump = [
            {'type': getattr(b, 'type', None), 'repr': repr(b)[:500]}
            for b in resp.content
        ]
        logger.error(
            '[IOWF] Claude vision returned EMPTY text content. stop_reason=%s usage=%s '
            'content_blocks=%s',
            getattr(resp, 'stop_reason', None), getattr(resp, 'usage', None), block_dump,
        )
        raise RuntimeError(
            f"Claude vision call returned no text content (stop_reason="
            f"{getattr(resp, 'stop_reason', None)!r}, "
            f"content_block_types={[getattr(b, 'type', None) for b in resp.content]!r}) "
            f"— see the preceding [IOWF] Claude vision returned EMPTY text content log line "
            f"for the full response dump."
        )
    return text, tokens_used


def _call_openai(api_key: str, image_b64: str) -> tuple[str, int]:
    """Returns (raw response text, real token count) — see tokens_used's
    own comment below for where that number comes from."""
    import openai
    logger.info(
        '[IOWF] Calling OpenAI vision: model=%s image_b64_bytes=%d prompt_chars=%d key_prefix=%s...',
        VISION_MODELS['openai'], len(image_b64), len(VISION_USER_PROMPT),
        (api_key or '')[:10],
    )
    client = openai.OpenAI(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
    resp = client.chat.completions.create(
        model=VISION_MODELS['openai'],
        max_tokens=VISION_MAX_TOKENS,
        temperature=0.0,
        messages=[
            {'role': 'system', 'content': VISION_SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': VISION_USER_PROMPT},
                {'type': 'image_url', 'image_url': {
                    'url': f'data:image/png;base64,{image_b64}', 'detail': 'high',
                }},
            ]},
        ],
    )
    text = resp.choices[0].message.content or ''
    logger.info(
        '[IOWF] OpenAI vision response: finish_reason=%s usage=%s raw text (%d chars): %s',
        resp.choices[0].finish_reason, resp.usage, len(text), text,
    )
    # Real token count straight off the API response's own usage object —
    # OpenAI's own combined total_tokens field, never estimated.
    usage = getattr(resp, 'usage', None)
    tokens_used = getattr(usage, 'total_tokens', 0) or 0 if usage else 0
    if not text.strip():
        # Same failure mode as _call_claude above — a successful call with
        # zero text content is abnormal, not "no tags found". Fail loudly
        # here (with finish_reason/usage already in the line above) rather
        # than letting it flow into _parse_tags('') as a silent 0-row page.
        logger.error(
            '[IOWF] OpenAI vision returned EMPTY text content. finish_reason=%s usage=%s',
            resp.choices[0].finish_reason, resp.usage,
        )
        raise RuntimeError(
            f"OpenAI vision call returned no text content "
            f"(finish_reason={resp.choices[0].finish_reason!r}) — see the preceding "
            f"[IOWF] OpenAI vision returned EMPTY text content log line."
        )
    return text


def _call_vision(provider: str, api_key: str, image_b64: str) -> tuple[str, int]:
    if provider == 'claude':
        return _call_claude(api_key, image_b64)
    if provider == 'openai':
        return _call_openai(api_key, image_b64)
    raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")


# Retry policy for transient network/API errors during a Vision call —
# same structural approach as apps.pid_checker_v2.services.vision_extractor's
# own _with_retries (attempt loop, log a warning, sleep, retry; re-raise
# once attempts are exhausted or the error isn't retriable) — independent
# reimplementation per this module's isolation convention, not shared
# code. That module's own policy is scoped to HTTP status codes (rate
# limit/overloaded); this one is specifically for what real usage
# actually hit — a connection reset mid-request (ECONNRESET / Windows'
# WinError 10054 "forcibly closed") — plus a request timeout and the same
# status codes, since those are the same class of "nothing wrong with the
# request itself, the network hiccuped" failure.
VISION_CONNECTION_RETRY_MAX_ATTEMPTS = 3
VISION_CONNECTION_RETRY_DELAY_S = 2.0
VISION_RETRY_STATUS_CODES = (429, 500, 502, 503, 504, 529)


def _extract_status_code(exc: Exception) -> Optional[int]:
    for attr in ('status_code', 'http_status', 'code'):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, 'response', None)
    if resp is not None:
        v = getattr(resp, 'status_code', None)
        if isinstance(v, int):
            return v
    # Fall back to parsing the message (e.g. "Error code: 529 - {...}")
    m = re.search(r'\b(4\d{2}|5\d{2})\b', str(exc))
    return int(m.group(1)) if m else None


def _is_retriable_network_error(exc: Exception) -> bool:
    """True for a connection reset, a request timeout, or a transient
    rate-limit/overloaded HTTP status — none of these mean anything was
    wrong with the request itself, so retrying the identical request is
    worth it. False for anything else (a genuine 400 bad request, an auth
    failure, the malformed/empty-response case _call_claude/_call_openai
    already raise their own specific error for — retrying THOSE
    identically would likely just fail the same way again and waste an
    API call, so they're deliberately left alone here).

    isinstance(exc, ConnectionError) catches ConnectionResetError and
    friends directly if the underlying transport raised a plain Python
    one; the message-substring check is the backstop for however the
    anthropic/openai SDK's own wrapped exception phrases the same
    underlying failure (varies by SDK/httpx version — 'ECONNRESET' is the
    POSIX errno name, Windows phrases it as "forcibly closed").
    """
    if isinstance(exc, ConnectionError):
        return True
    if _extract_status_code(exc) in VISION_RETRY_STATUS_CODES:
        return True
    msg = str(exc).lower()
    return any(needle in msg for needle in (
        'econnreset', 'connection reset', 'connection aborted',
        'forcibly closed', 'broken pipe', 'remote end closed',
        'timed out', 'timeout', 'overloaded',
    ))


def _call_vision_with_retries(provider: str, api_key: str, image_b64: str) -> tuple[str, int]:
    """Wraps _call_vision with the fixed retry policy above — up to
    VISION_CONNECTION_RETRY_MAX_ATTEMPTS attempts total,
    VISION_CONNECTION_RETRY_DELAY_S between them. A fixed short delay
    rather than exponential backoff: an ECONNRESET is a dropped
    connection, not a rate limit that benefits from waiting progressively
    longer each time.

    This is the ONLY place any Vision call is actually made from
    (_extract_tags_from_image, shared by both Quick Scan's single
    full-page call and every one of Thorough Scan's tile calls — see its
    own docstring), so one dropped connection on one tile gets its own
    fixed retries before that tile is given up on, with no separate
    tile-specific retry code needed — Thorough Scan's existing per-tile
    try/except in extract_pid_tags_from_page already isolates one tile's
    ultimate (post-retry) failure from the others, so a bad connection on
    one tile still can't fail the whole page.
    """
    import time
    last_exc: Exception | None = None
    for attempt in range(1, VISION_CONNECTION_RETRY_MAX_ATTEMPTS + 1):
        try:
            return _call_vision(provider, api_key, image_b64)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retriable_network_error(exc) or attempt == VISION_CONNECTION_RETRY_MAX_ATTEMPTS:
                raise
            logger.warning(
                '[IOWF] Vision call transient error (attempt %d/%d): %s: %s — retrying in %.0fs',
                attempt, VISION_CONNECTION_RETRY_MAX_ATTEMPTS, type(exc).__name__, exc,
                VISION_CONNECTION_RETRY_DELAY_S,
            )
            time.sleep(VISION_CONNECTION_RETRY_DELAY_S)
    raise last_exc  # pragma: no cover — loop always either returns or raises


def _recover_truncated_json_array(text: str) -> Optional[str]:
    """Best-effort recovery for a Vision response whose JSON array was cut
    off mid-object — the response hit max_tokens partway through, so the
    array never got its closing ']' (and often not even the last object's
    closing '}'). Discarding the whole response would throw away every
    tag object Claude DID finish reading before the cutoff, which for a
    dense P&ID could be dozens of genuine tags.

    Finds the opening '[', walks forward tracking object/array nesting
    depth (string-aware — a brace/bracket inside a quoted value, e.g. a
    service description, doesn't affect the count), and remembers the end
    of the LAST fully-closed top-level object directly inside the array.
    Returns a repaired JSON string (everything up to that point, with the
    array closed) or None if not even one complete object was found.
    """
    start = text.find('[')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    last_complete_object_end = None

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
            if ch == '}' and depth == 1:
                last_complete_object_end = i + 1
            if depth == 0:
                # Array closed normally — normal parsing would already
                # have succeeded, so there's nothing to recover.
                return None

    if last_complete_object_end is None:
        return None
    return text[start:last_complete_object_end] + ']'


# Sanity filter for "unit_prefix" — the model reads this from a page note
# (e.g. "ALL INSTRUMENTS PREFIXED BY UNIT NO. 1520") or an inline balloon
# number, not from a fixed vocabulary, so a loose alphanumeric shape is all
# that can be checked. Rejects obviously-wrong values (empty, punctuation,
# something clearly not a short unit code) without hardcoding to any
# specific unit number — works for any real unit code the model reports.
UNIT_PREFIX_PATTERN = re.compile(r'^[A-Z0-9]{1,6}$')


# A line tag's real-document convention puts the unit prefix AFTER the
# size segment, not at the very front — confirmed live against a real
# extracted document: '6"-1520-HY-1025-61056R-PP', '10"-1520-HY-1018-
# 61056R-V' (size, THEN unit, then service/serial/spec). Prefixing the
# front the same way instrument/equipment tags are prefixed would produce
# '1520-6"-HY-1025-...' — a shape that doesn't match any real line tag.
_LINE_SIZE_RE = re.compile(r'^(\d{1,3}(?:/\d)?"?)-(.+)$')

# Generic "some digits, then the rest" shapes — used as a fallback in
# _strip_reported_unit_prefix_for_validation when this item's
# 'unit_prefix' field wasn't (also) populated even though 'tag' itself
# was written with the prefix baked in. Two variants: hyphenated
# ('1520-PY1599C') and fully concatenated with no separator at all
# ('1520PY1599C'). INSTRUMENT_TAG_PATTERN has no legitimate bare shape
# starting with a digit at all, so detecting either shape directly on
# 'tag' is safe regardless of whether 'unit_prefix' was also populated.
_LEADING_DIGIT_PREFIX_RE = re.compile(r'^(\d{2,6})-(.+)$')
_LEADING_DIGIT_PREFIX_NO_SEP_RE = re.compile(r'^(\d{2,6})([A-Z].*)$')


def _strip_reported_unit_prefix_for_validation(tag: str, raw_unit_prefix: str) -> tuple[str, str]:
    """Instrument-kind only. Returns (tag_for_validation,
    effective_unit_prefix) — strips a leading unit-number segment off
    'tag' so INSTRUMENT_TAG_PATTERN's strict letters-first shape can
    still validate the underlying bare FUNC-LOOP tag, and returns
    whatever prefix was actually found so the CALLER can feed it back
    into _apply_unit_prefix afterward (see call site) — the prompt tells
    the model to write the full prefixed tag directly INTO 'tag' now, so
    simply discarding what was stripped here would silently throw the
    prefix away, undoing the whole point of stripping it in the first
    place. effective_unit_prefix is '' when nothing was stripped (tag
    genuinely has no prefix on it, or this item has none at all).

    Tries three things, in order of how much evidence backs them:
      1. This item's own reported 'unit_prefix' field, if 'tag' actually
         starts with it (hyphenated, or bare — for the no-hyphen case,
         only strips when what's left STARTS WITH A LETTER, confirming
         the split point was genuinely the prefix boundary and not a
         coincidental leading-substring match — a real unit prefix is
         virtually always numeric while FUNC is always letters).
      2. A generic leading digit-group + HYPHEN directly on 'tag' itself
         — covers the real, observed failure mode where the model wrote
         the full prefixed tag but did NOT also duplicate the same value
         into 'unit_prefix' on that item.
      3. Same, but with NO separator at all between the digit-group and
         the letters (e.g. '1520PY1599C') — the fully-concatenated form
         _ensure_func_loop_hyphen alone can't recover, since that
         function only handles a bare tag starting with LETTERS.
    Falls through to (tag, '') unchanged if none of these apply.
    """
    unit_prefix = (raw_unit_prefix or '').strip().upper()
    if unit_prefix and UNIT_PREFIX_PATTERN.match(unit_prefix):
        if tag.startswith(f'{unit_prefix}-'):
            return tag[len(unit_prefix) + 1:], unit_prefix
        if tag.startswith(unit_prefix):
            remainder = tag[len(unit_prefix):]
            if remainder[:1].isalpha():
                return remainder, unit_prefix
    m = _LEADING_DIGIT_PREFIX_RE.match(tag)
    if m:
        candidate_prefix, remainder = m.groups()
        if UNIT_PREFIX_PATTERN.match(candidate_prefix):
            return remainder, candidate_prefix
    m2 = _LEADING_DIGIT_PREFIX_NO_SEP_RE.match(tag)
    if m2:
        candidate_prefix, remainder = m2.groups()
        if UNIT_PREFIX_PATTERN.match(candidate_prefix):
            return remainder, candidate_prefix
    return tag, ''


def _apply_unit_prefix(tag: str, raw_unit_prefix: str, kind: str = 'instrument') -> tuple[str, str]:
    """Applies a unit-number prefix to an instrument/equipment/line tag,
    e.g. ('PY-1599B', '1520', 'instrument') -> '1520-PY-1599B' — the fix
    for P&IDs that state "ALL INSTRUMENTS PREFIXED BY UNIT NO. XXXX" (or,
    per real evidence, the same rule extending to equipment and piping-
    specialty/line items too) as a page-wide note instead of printing the
    unit number on every balloon/tag (Vision reads the note separately
    into 'unit_prefix' rather than the bare 'tag' — see
    VISION_USER_PROMPT's UNIT NUMBER PREFIX section).

    kind='line' inserts AFTER the size segment instead of at the front —
    see _LINE_SIZE_RE above. If a line tag's size segment can't be
    identified, nothing is applied rather than guessing wrong (returns
    the tag unchanged, accepted_unit_prefix ''). kind='symbol' is never
    passed here — an untagged symbol has no tag to prefix.

    Returns (final_tag, accepted_unit_prefix) — accepted_unit_prefix is ''
    if there was nothing to apply (no prefix reported, it failed the sanity
    check, it was already present, or — line only — the size segment
    couldn't be identified). Checking startswith covers both a model that
    already (against instructions) embedded the prefix in 'tag', and one
    tag re-reported with the same prefix by two different calls — either
    way the prefix is never applied twice.
    """
    unit_prefix = (raw_unit_prefix or '').strip().upper()
    if not unit_prefix:
        return tag, ''
    if not UNIT_PREFIX_PATTERN.match(unit_prefix):
        logger.info(
            '[IOWF] _parse_tags: ignoring unit_prefix %r for tag %r — does not look like a '
            'real unit code (hallucinated/misread note text)',
            raw_unit_prefix, tag,
        )
        return tag, ''

    if kind == 'line':
        m = _LINE_SIZE_RE.match(tag)
        if not m:
            return tag, ''
        size, rest = m.group(1), m.group(2)
        if rest.startswith(f'{unit_prefix}-'):
            return tag, unit_prefix  # already present right after the size segment
        return f'{size}-{unit_prefix}-{rest}', unit_prefix

    if tag.startswith(f'{unit_prefix}-'):
        # Already carries this prefix (model included it in 'tag' despite
        # instructions not to, or it's genuinely part of the tag as drawn)
        # — don't double it up. Hyphen-anchored specifically so a
        # unit_prefix that coincidentally matches the START of the FUNC/
        # EQUIPCODE itself (e.g. unit_prefix "PY" vs tag "PY-1599B") never
        # false-positives as "already prefixed".
        return tag, unit_prefix
    return f'{unit_prefix}-{tag}', unit_prefix


# Splits a bare FUNC+LOOP instrument tag with NO hyphen at all — e.g.
# 'PY1599C', 'MOV1001', 'HIC1003A' — into (FUNC, LOOP+suffix). Only ever
# applied to a tag that has ALREADY passed INSTRUMENT_TAG_PATTERN (that
# pattern's own '[A-Z]{1,4}-?\d{2,4}[A-Z]?(?:[A-Z]{2})?' shape guarantees
# the letters-then-digits split point is unambiguous), so this doesn't
# need to re-derive FUNC's exact length rule — just find where the
# leading letter run ends.
_BARE_FUNC_LOOP_RE = re.compile(r'^([A-Z]{1,4})(\d[\dA-Z]*)$')


def _ensure_func_loop_hyphen(tag: str) -> str:
    """Confirmed live on real extracted data (document with tags like
    '1520-PY-1599B' correctly hyphenated right next to '1520-PY1599C' and
    '1520-MOV1001' NOT hyphenated, from the very same Vision response) —
    the model reads a hyphen when the balloon draws FUNC and LOOP on one
    line with a visible dash, but drops it when they're split across two
    lines inside the balloon with no dash glyph at all (a very common
    ISA-5.1 drawing style). The prompt's own 'preserve exactly as drawn'
    instruction is correct engineering advice for THAT case but produces
    an inconsistent, wrong-looking result for this app's canonical tag
    format — every other tag-normalizing function in this app
    (legend_comparison.normalize_tag) already treats the hyphenated form
    as canonical for exactly this reason. Deterministically inserts the
    hyphen here rather than depending on the model to read/insert it
    consistently; already-hyphenated tags pass through untouched.
    """
    if '-' in tag:
        return tag
    m = _BARE_FUNC_LOOP_RE.match(tag)
    if not m:
        return tag
    return f'{m.group(1)}-{m.group(2)}'


def _parse_tags(raw: str) -> list[dict]:
    text = (raw or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\n?', '', text)
        text = re.sub(r'```\s*$', '', text)

    parsed = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    if parsed is None:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                logger.info('[IOWF] _parse_tags: salvaged a complete JSON array embedded in a non-JSON response')
            except (json.JSONDecodeError, ValueError):
                parsed = None

    if parsed is None:
        # No complete '[...]' array anywhere in the text — most likely the
        # response was truncated mid-object by hitting max_tokens. Recover
        # whatever complete tag objects exist before the cutoff instead of
        # discarding the entire response.
        recovered = _recover_truncated_json_array(text)
        if recovered is not None:
            try:
                parsed = json.loads(recovered)
                logger.warning(
                    '[IOWF] _parse_tags: response JSON was truncated (likely hit max_tokens) — '
                    'recovered %d complete tag object(s) from before the cutoff instead of '
                    'discarding the whole response',
                    len(parsed) if isinstance(parsed, list) else 0,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    '[IOWF] _parse_tags: truncation recovery still failed to parse (%s). '
                    'Recovered text was: %r', exc, recovered[:500],
                )
                parsed = None

    if not isinstance(parsed, list):
        logger.warning(
            '[IOWF] _parse_tags: could not recover any JSON array from the response '
            '(not JSON, no embedded array, truncation recovery found no complete object). '
            'Raw text was: %r', text[:500],
        )
        return []

    out = []
    symbol_candidates = []  # held back until every tagged item is known — see dedup pass below
    known_items: dict[str, str] = {}  # bare/prefixed tag (upper) -> its symbol_type (upper)
    rejected_bad_kind = 0
    rejected_bad_shape = 0
    for item in parsed:
        if not isinstance(item, dict):
            rejected_bad_shape += 1
            continue
        kind = (item.get('kind') or '').strip().lower()
        symbol_type = (item.get('symbol_type') or '').strip().upper()

        if kind == 'symbol':
            # No tag to validate — an untagged symbol legitimately has
            # tag == "" (that's the whole point of this kind). Accept it
            # based on symbol_type being present instead; that's the only
            # thing actually identifying what was found. Held in
            # symbol_candidates rather than appended straight to `out` —
            # whether this is a genuine untagged symbol or a duplicate of
            # an already-tagged instrument/equipment item (see dedup pass
            # below) can only be decided once every tagged item in this
            # response is known, and a 'symbol' item can appear earlier in
            # the array than the tagged item it duplicates.
            if not symbol_type:
                rejected_bad_shape += 1
                logger.info('[IOWF] _parse_tags: rejected a "symbol" item with no symbol_type')
                continue
            symbol_candidates.append({
                'kind': kind,
                'tag': '',
                'function_code': '',
                'symbol_type': symbol_type,
                'service': (item.get('service') or '').strip(),
                'location': (item.get('location') or '').strip(),
            })
            continue

        pattern = _TAG_PATTERNS.get(kind)
        if not pattern:
            rejected_bad_kind += 1
            continue
        tag = (item.get('tag') or '').strip().upper()
        # The prompt now tells the model to write the unit prefix directly
        # INTO "tag" whenever a page-wide rule applies (e.g.
        # "1520-PY-1599B", or even "1520PY1599C" with no hyphens at all)
        # — but INSTRUMENT_TAG_PATTERN below still only accepts the bare
        # FUNC-LOOP shape (it deliberately isn't widened to accept a
        # leading unit number generally, to avoid loosening sanity-
        # checking for every instrument tag). If this item's OWN reported
        # 'unit_prefix' is a plausible code and 'tag' actually starts with
        # it, strip just that leading segment before validating —
        # _apply_unit_prefix cleanly re-adds it afterward via its own
        # already-tested logic, so the stored result is identical no
        # matter which way the model complied (prefix in 'tag' only, in
        # 'unit_prefix' only, or in both).
        stripped_unit_prefix = ''
        if kind == 'instrument':
            tag, stripped_unit_prefix = _strip_reported_unit_prefix_for_validation(tag, item.get('unit_prefix'))
        # Validate the sanity pattern against the BARE tag (as printed
        # inside the balloon, prefix stripped per above) before any unit
        # prefix is (re-)applied below — widening INSTRUMENT_TAG_PATTERN
        # itself to also accept a leading unit number would loosen it more
        # than necessary and risk accepting genuine garbage; instead the
        # prefix is validated separately (UNIT_PREFIX_PATTERN in
        # _apply_unit_prefix) and (re-)prepended only after this shape
        # check passes.
        if not tag or not pattern.match(tag):
            rejected_bad_shape += 1
            logger.info(
                '[IOWF] _parse_tags: rejected tag %r (kind=%r) — does not match the %s '
                'sanity pattern (hallucinated / malformed tag, or a real tag Vision misread)',
                tag, kind, kind or 'unrecognised-kind',
            )
            continue
        unit_prefix = ''
        if kind == 'instrument':
            tag = _ensure_func_loop_hyphen(tag)
        bare_tag = tag
        if kind in ('instrument', 'equipment', 'line'):
            # Prefer whatever _strip_reported_unit_prefix_for_validation
            # already found on 'tag' itself (stripped_unit_prefix) over
            # the item's own 'unit_prefix' field — if stripping found
            # something, that's the confirmed prefix to re-add; if it
            # found nothing, stripped_unit_prefix is '' and this falls
            # through to item.get('unit_prefix') exactly as before.
            tag, unit_prefix = _apply_unit_prefix(
                tag, stripped_unit_prefix or item.get('unit_prefix'), kind=kind,
            )
        if symbol_type:
            known_items[bare_tag] = symbol_type
            known_items[tag] = symbol_type  # also index the prefixed form, in case location uses it
        out.append({
            'kind': kind,
            'tag': tag,
            'function_code': (item.get('function_code') or '').strip().upper(),
            'symbol_type': symbol_type,
            'service': (item.get('service') or '').strip(),
            'location': '',
            'unit_prefix': unit_prefix,
        })

    # Dedup pass: a genuine model-compliance failure seen live — despite
    # the prompt saying not to, Vision sometimes reports a valve-type
    # instrument (e.g. tag "PSV1521A", symbol_type "PRESSURE SAFETY
    # VALVE") AND separately an untagged "symbol" item for the SAME
    # physical valve (symbol_type "PRESSURE SAFETY VALVE" again, location
    # "near PSV1521A") — one real valve counted twice. Both signals must
    # agree (the location names a tag already reported above, AND that
    # tag's own symbol_type matches this item's) before dropping anything
    # — a generic symbol legitimately positioned "near" some unrelated
    # tagged equipment (a different symbol_type) is left alone.
    # Hyphen-insensitive on both sides: known_items' keys are the
    # canonical (hyphenated) tag forms after _ensure_func_loop_hyphen, but
    # 'location' text is the model's own free-form wording, which reuses
    # whatever bare tag string it originally read (often unhyphenated,
    # e.g. 'near PSV1521A') — comparing the hyphenated form directly
    # against that text would never find a match, silently defeating this
    # whole dedup pass for exactly the tags _ensure_func_loop_hyphen just
    # fixed. Stripping hyphens from both sides for this comparison ONLY
    # (never for what's stored/displayed) keeps both fixes compatible.
    rejected_duplicate_symbol = 0
    for candidate in symbol_candidates:
        loc = candidate['location'].upper().replace('-', '')
        is_duplicate = any(
            known_tag and known_tag.replace('-', '') in loc and known_type == candidate['symbol_type']
            for known_tag, known_type in known_items.items()
        )
        if is_duplicate:
            rejected_duplicate_symbol += 1
            logger.info(
                '[IOWF] _parse_tags: dropped duplicate "symbol" item (symbol_type=%r, location=%r) — '
                'already reported as a tagged instrument/equipment item at this location',
                candidate['symbol_type'], candidate['location'],
            )
            continue
        out.append(candidate)

    backfilled_count = _backfill_missing_unit_prefix(out)
    prefixed_count = sum(1 for i in out if i.get('unit_prefix'))
    logger.info(
        '[IOWF] _parse_tags: %d items in response -> %d accepted, %d rejected (bad/unrecognised kind), '
        '%d rejected (missing tag/symbol_type or failed sanity pattern), %d rejected (duplicate of a '
        'tagged item), %d instrument/equipment/line tag(s) got a unit-number prefix applied (%d of '
        'those via the same-page majority backfill)',
        len(parsed), len(out), rejected_bad_kind, rejected_bad_shape, rejected_duplicate_symbol,
        prefixed_count, backfilled_count,
    )
    return out


# Kinds a unit prefix can ever apply to — 'symbol' is deliberately
# excluded (an untagged symbol has tag == '' by design, nothing to
# prefix; see _row_from_tag_info's own docstring for why each row only
# ever carries one of tag_number/equipment_tag/line_tag).
_UNIT_PREFIXABLE_KINDS = ('instrument', 'equipment', 'line')


def _backfill_missing_unit_prefix(out: list[dict]) -> int:
    """Real, observed model-compliance gap: given a page-wide "ALL
    INSTRUMENTS AND PIPING SPECIALTY ITEMS PREFIXED BY UNIT NO. XXXX" note
    (confirmed real phrasing — it names more than just instruments),
    Vision sometimes reports 'unit_prefix' correctly on MOST tagged items
    on the page but forgets it on a few — the note applies uniformly,
    there's no such thing as a real per-item exemption, so a missing
    value on an otherwise-identical page is a reporting gap, not a
    genuine "this one has no prefix" case.

    Pools instrument/equipment/line items TOGETHER for one page-wide vote
    (rather than three independent per-kind votes) — the note is a single
    page-wide rule, so evidence from any one of these kinds is evidence
    for all of them, and pooling avoids a kind with only 1-2 instances on
    a page failing to accumulate enough evidence on its own despite
    overwhelming evidence from the other kinds. Backfills the page's
    dominant unit_prefix onto EVERY item that came back with none — ANY
    confirmed occurrence is enough evidence (a real page-wide rule has no
    per-item exemptions, so even one correctly-reported instance proves
    the rule applies to the whole page — waiting for a "majority" missed
    real cases where only a minority of items happened to get it right).
    The only thing that still blocks backfill is genuine disagreement —
    more than one DISTINCT unit_prefix value seen on the same page, which
    means either a real mixed-unit page or an unreliable read, and
    guessing which one is correct would be worse than leaving it. See
    also _backfill_missing_unit_prefix_across_rows below for the same
    check pooling evidence across the WHOLE document, for pages whose own
    response never mentions a unit_prefix at all (e.g. the note wasn't
    legible/present on that specific page's image). Insertion point is
    kind-aware (see _apply_unit_prefix — a line tag's prefix goes after
    its size segment, not at the front). Mutates `out` in place; returns
    how many items were backfilled.
    """
    candidates = [i for i in out if i.get('kind') in _UNIT_PREFIXABLE_KINDS]
    prefixed = [i for i in candidates if i.get('unit_prefix')]
    missing = [i for i in candidates if not i.get('unit_prefix')]
    if not prefixed or not missing:
        return 0
    prefixes_used = {i['unit_prefix'] for i in prefixed}
    if len(prefixes_used) != 1:
        return 0  # more than one distinct value seen — a real mixed-unit page, don't guess
    dominant = next(iter(prefixes_used))
    count = 0
    for item in missing:
        original_tag = item['tag']
        new_tag, applied = _apply_unit_prefix(original_tag, dominant, kind=item['kind'])
        if not applied or new_tag == original_tag:
            continue  # e.g. a line tag whose size segment couldn't be identified
        item['tag'] = new_tag
        item['unit_prefix'] = applied
        count += 1
        logger.info(
            '[IOWF] _backfill_missing_unit_prefix: %r -> %r (kind=%r, page-wide unit_prefix %r applied — '
            '%d/%d other tagged items on this page already reported it)',
            original_tag, item['tag'], item['kind'], dominant, len(prefixed), len(candidates),
        )
    return count


def _row_from_tag_info(tag_info: dict, page_index: int) -> dict:
    """One parsed tag/symbol -> one canonical-shaped I/O List row.

    Instrument tags populate 'tag_number' (validated against
    instrument_index); equipment tags populate 'equipment_tag' (validated
    against equipment_register); line tags populate 'line_tag' (validated
    against line_numbering) — see
    models.IO_LEGEND_SUPPLEMENTARY_FORMAT_SECTIONS. Untagged symbols
    populate neither — nothing validates a free-text symbol_type, the row
    exists purely to surface what Vision found. Each row only ever has ONE
    of tag_number/equipment_tag/line_tag populated — a P&ID drawing
    doesn't reliably pair a given instrument with "its" equipment/line the
    way a table row would, so each kind is kept independent rather than
    merged into one.
    """
    kind = tag_info['kind']
    tag = tag_info['tag']
    symbol_type = tag_info.get('symbol_type', '')
    location = tag_info.get('location', '')

    record = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
    record.update({
        'page_number': page_index + 1,
        'symbol_type': symbol_type,
        'kind': kind,
        'remarks': 'Extracted from P&ID drawing via AI Vision — verify manually.',
    })
    if kind == 'instrument':
        record.update({
            'tag_number': tag,
            'instrument_type': tag_info['function_code'],
            'service_description': tag_info['service'],
            # Not shown on a P&ID — a DCS I/O signal type (AI/AO/DI/DO)
            # is a control-system attribute, not something drawn on the
            # sheet. Leaving this blank rather than guessing from the
            # function code avoids fabricating a value that looks correct
            # but isn't backed by anything on the drawing.
            'signal_type': '',
        })
    elif kind == 'equipment':
        record.update({
            'equipment_tag': tag,
            'instrument_type': tag_info['function_code'],
            'service_description': tag_info['service'],
        })
    elif kind == 'line':
        record.update({
            'line_tag': tag,
            'service_description': tag_info['service'],
        })
    elif kind == 'symbol':
        record.update({
            'service_description': tag_info['service'],
            'location': location,
        })

    unit_prefix = tag_info.get('unit_prefix', '')
    if unit_prefix and kind in _UNIT_PREFIXABLE_KINDS:
        # Structured column (not just prose in 'remarks') so a
        # document-wide backfill pass can tell which rows already have
        # confirmed evidence without re-parsing text — see
        # _backfill_missing_unit_prefix_across_rows.
        record['unit_prefix'] = unit_prefix
        # Also surfaces it in the table itself so a reviewer can see WHY
        # the tag has a unit number without digging through logs.
        record['remarks'] += f" Unit prefix '{unit_prefix}' applied from drawing note/label."
    return record


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    """Drop exact-duplicate rows, keyed on whichever field(s) actually
    identify the row: tag_number/equipment_tag/line_tag for a tagged row
    (exactly one of the three is non-empty), symbol_type+location for an
    untagged symbol. A single-process run already deduped as it went (one
    'seen' set covering every page in order); this is the same check
    applied once at the end instead, so it works identically whether rows
    came from one sequential loop or were combined from N independent
    parallel Celery page-tasks that can't see each other's output.
    """
    seen: set[tuple[str, str, str, str, str]] = set()
    out = []
    for r in rows:
        key = (
            r.get('tag_number', ''), r.get('equipment_tag', ''), r.get('line_tag', ''),
            r.get('symbol_type', ''), r.get('location', ''),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# tag_number/equipment_tag/line_tag are populated on mutually-exclusive
# rows (see _row_from_tag_info's own docstring) — this maps a prefixable
# kind to which canonical column actually holds its tag text.
_TAG_FIELD_BY_KIND = {'instrument': 'tag_number', 'equipment': 'equipment_tag', 'line': 'line_tag'}


def _backfill_missing_unit_prefix_across_rows(rows: list[dict]) -> int:
    """Document-wide sibling of _backfill_missing_unit_prefix — same "any
    confirmed evidence, one consistent value" rule, but pooling evidence
    across EVERY page of the document instead of just one page's own
    Vision response. Closes two real gaps the per-page pass alone can't:

      - The unit-prefix note may only be legible/present on SOME pages of
        the drawing set (e.g. a general-notes sheet) — a page whose own
        image never shows it has zero evidence to work with on its own,
        even though the same document-wide rule still applies to it.
      - The async/Celery fan-out path (tasks.py's process_pid_vision_page)
        runs each page as a fully independent task with no visibility
        into any other page's results. The per-page pass already ran once
        inside each task; rows from DIFFERENT pages are only ever
        combined afterward, in finalize_io_document — exactly where this
        runs (see also extract_pid_tags_via_vision for the sync-path
        equivalent call site).

    Operates on the FINAL canonical row shape (post _row_from_tag_info) —
    'unit_prefix' is read from the row's own column (the per-page tag_info
    dicts no longer exist once pages are combined), and the tag field to
    update is whichever of tag_number/equipment_tag/line_tag the row's
    kind actually populates (_TAG_FIELD_BY_KIND). Mutates `rows` in
    place; returns how many rows were backfilled.
    """
    candidates = [r for r in rows if r.get('kind') in _UNIT_PREFIXABLE_KINDS]
    prefixed = [r for r in candidates if r.get('unit_prefix')]
    missing = [r for r in candidates if not r.get('unit_prefix')]
    if not prefixed or not missing:
        return 0
    prefixes_used = {r['unit_prefix'] for r in prefixed}
    if len(prefixes_used) != 1:
        return 0  # more than one distinct value across the whole document — a real mixed-unit set, don't guess
    dominant = next(iter(prefixes_used))
    count = 0
    for row in missing:
        field = _TAG_FIELD_BY_KIND[row['kind']]
        original_tag = row.get(field, '')
        new_tag, applied = _apply_unit_prefix(original_tag, dominant, kind=row['kind'])
        if not applied or new_tag == original_tag:
            continue  # e.g. a line tag whose size segment couldn't be identified
        row[field] = new_tag
        row['unit_prefix'] = applied
        row['remarks'] = (row.get('remarks') or '') + (
            f" Unit prefix '{applied}' applied document-wide "
            f"(not present in this page's own Vision response)."
        )
        count += 1
        logger.info(
            "[IOWF] _backfill_missing_unit_prefix_across_rows: %r -> %r (kind=%r, page=%s, "
            'document-wide unit_prefix %r applied)',
            original_tag, new_tag, row['kind'], row.get('page_number'), dominant,
        )
    return count


def _warn_about_remaining_unprefixed_tags(rows: list[dict]) -> None:
    """Final visibility check, run after both the per-page and
    document-wide backfill passes: logs a clear WARNING naming any
    instrument/equipment/line tag that STILL has no unit_prefix, rather
    than letting a genuinely un-backfillable case (no confirmed evidence
    anywhere in the whole document — the drawing may simply have no
    unit-prefix convention at all) ship silently.
    """
    still_missing = [r for r in rows if r.get('kind') in _UNIT_PREFIXABLE_KINDS and not r.get('unit_prefix')]
    if not still_missing:
        return
    examples = [r.get(_TAG_FIELD_BY_KIND[r['kind']], '') for r in still_missing][:20]
    logger.warning(
        '[IOWF] %d tag(s) have NO unit_prefix after all backfill passes — no confirmed evidence '
        'anywhere in this document (either it genuinely has no unit-prefix note, or Vision never '
        'read one on any page): %s%s',
        len(still_missing), examples, ' ...' if len(still_missing) > 20 else '',
    )


# Matches a tag-shaped mention inside free text (e.g. a 'location'
# field's "near PSV1521A" or "connecting PY-1599A/B/C/D loops"). The
# trailing single letter + optional "/X/Y/Z" run captures the real,
# observed shorthand a page uses for several related tags sharing one
# FUNC-LOOP (PY-1599A/B/C/D = 4 separate tags, not one).
_LOCATION_TAG_MENTION_RE = re.compile(r'\b([A-Z]{1,4}-?\d{2,4})([A-Z])?((?:/[A-Z])*)\b')


def _expand_grouped_tag_mentions(text: str) -> set[str]:
    """Finds every tag-shaped mention inside free text, expanding
    grouped-suffix shorthand like 'PY-1599A/B/C/D' into the individual
    tags it actually names (PY-1599A, PY-1599B, PY-1599C, PY-1599D)
    rather than treating the whole string as one opaque token. Best-
    effort text scanning only — used to flag POSSIBLE self-consistency
    gaps for a human to review, never to reject or silently invent
    anything.
    """
    found: set[str] = set()
    for m in _LOCATION_TAG_MENTION_RE.finditer(text.upper()):
        base, first_suffix, extra = m.groups()
        if not first_suffix:
            found.add(base)
            continue
        found.add(f'{base}{first_suffix}')
        for part in extra.split('/'):
            if part:
                found.add(f'{base}{part}')
    return found


def _normalize_for_mention_check(s: str) -> str:
    return (s or '').replace('-', '').upper()


def _warn_about_tags_mentioned_only_in_location(rows: list[dict]) -> None:
    """Real, observed gap: a tag named inside a "symbol" row's
    "location" text (e.g. "connecting PY-1599A/B/C/D loops") proves
    Vision already read and recognised that tag, but it's easy for the
    model to stop there and never ALSO report it as its own separate
    instrument/equipment/line item — confirmed live: a location said
    "connecting PY1599A/B/C/D loops" and NONE of those four tags had
    their own item anywhere in the document. See the prompt's own
    SELF-CONSISTENCY CHECK section, which asks the model not to do this;
    this is the code-level backstop for whenever it does anyway — a
    prompt instruction changes model behaviour probabilistically, it
    can't guarantee it, so this makes the gap VISIBLE in logs regardless
    of whether the model complied. Best-effort (a location mention could
    legitimately be generic context with nothing to expand, e.g.
    "top-left") — logs a WARNING only, never blocks or mutates
    extraction.
    """
    known_normalized = set()
    for r in rows:
        for field in ('tag_number', 'equipment_tag', 'line_tag'):
            val = r.get(field) or ''
            if val:
                known_normalized.add(_normalize_for_mention_check(val))
    mentioned_but_missing: set[str] = set()
    for r in rows:
        if r.get('kind') != 'symbol':
            continue
        for mention in _expand_grouped_tag_mentions(r.get('location') or ''):
            norm = _normalize_for_mention_check(mention)
            # Substring match (not exact) so a known tag carrying a unit
            # prefix (e.g. '1520PY1599A') still matches a bare mention
            # ('PY1599A') without needing to know which prefix applies.
            if not any(norm in k or k in norm for k in known_normalized):
                mentioned_but_missing.add(mention)
    if mentioned_but_missing:
        logger.warning(
            '[IOWF] %d tag(s) mentioned inside a "symbol" item\'s location text were never '
            'separately reported as their own instrument/equipment/line item — likely a real '
            'missed tag, not just a location reference: %s',
            len(mentioned_but_missing), sorted(mentioned_but_missing),
        )


def _extract_tags_from_image(
    img: Image.Image, provider: str, api_key: str, page_index: int, max_dim: int,
) -> tuple[list[dict], int]:
    """Downscale + encode + call Vision + parse + convert to canonical
    rows, for exactly ONE image — a full page (Quick Scan) or a single
    tile of one (Thorough Scan). Both callers below share this so a tile
    call and a full-page call are identical in every way except which
    image they're given. Returns (rows, real token count for this one
    call) — the token count rides along so on_call_complete (see
    extract_pid_tags_from_page) can report genuine cumulative usage.
    """
    resized = _downscale(img, max_dim)
    image_b64 = _image_to_b64_png(resized)
    raw, tokens_used = _call_vision_with_retries(provider, api_key, image_b64)
    rows = [_row_from_tag_info(info, page_index) for info in _parse_tags(raw)]
    return rows, tokens_used


def extract_pid_tags_from_page(
    pdf_bytes: bytes, page_index: int, provider: str, api_key: str,
    thorough: bool = False, on_call_complete=None,
) -> list[dict]:
    """Vision-extract tags/symbols from exactly ONE page — the unit of
    work shared by extract_pid_tags_via_vision's whole-document loop below
    AND tasks.py's per-page Celery fan-out (process_pid_vision_page) for
    large P&ID drawings (50-100+ pages), so both paths run identical
    per-page logic rather than two implementations drifting apart.

    thorough=False (Quick Scan, default): VISION_PASSES independent
    Vision calls on the SAME full page image, combined and deduped (was a
    single call; widened for the run-to-run variance fix — see
    VISION_PASSES's own comment).
    thorough=True (Thorough Scan): the page is split into a
    VISION_TILE_ROWS x VISION_TILE_COLS grid of overlapping crops
    (_tile_image), and EACH tile gets VISION_PASSES independent Vision
    calls INSTEAD of the single full-page call — a small balloon that a
    dense page's full-size call might miss is far more legible cropped
    into its own tile, and a second independent look at that same tile
    catches what the first pass missed. Tile/pass calls run sequentially
    (matching this module's existing one-call-at-a-time style; not the
    concurrency pid_checker_v2's own thorough mode uses) — trading extra
    latency for a simpler, more predictable version. Results from every
    tile AND every pass are combined and passed through _dedupe_rows(),
    since a tag/symbol sitting in the overlap band between two tiles (or
    reported again by a second pass over the same image) can legitimately
    be reported more than once.

    Raises on a missing/unsupported provider/key (checked once, cheaply,
    before any rendering work), or if EVERY pass for this page fails —
    callers decide how to handle that (skip-and-continue for the
    whole-document loop; the local-OCR fallback for that single page in
    the Celery task). If at least one pass succeeds, its rows are
    returned even if another pass failed.

    on_call_complete, if given, is invoked exactly once after EVERY
    individual Vision API call finishes — success or failure alike (a
    call that failed still genuinely happened and took real time; the
    point is showing real work completing, not a success count). Lets
    tasks.py's process_pid_vision_page report real, fine-grained progress
    (IOListDocument.vision_calls_done) mid-page instead of only once the
    WHOLE page — all VISION_PASSES calls, or every tile x pass in
    Thorough Scan — finishes, which is what made the frontend's progress
    bar sit frozen for minutes at a time on a small page count. Called as
    on_call_complete(tokens_used) — tokens_used is the real count from
    that one call's own API response (0 for a call that raised before a
    response came back), letting the caller accumulate a genuine running
    total. Never raises from this callback's own failure — a
    progress-counter update must not be able to take down real
    extraction work.
    """
    def _tick(tokens_used=0):
        if on_call_complete is None:
            return
        try:
            on_call_complete(tokens_used)
        except Exception:  # noqa: BLE001
            logger.exception('[IOWF] on_call_complete progress callback failed — ignoring')
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Choose one of {SUPPORTED_PROVIDERS}.")
    if not api_key or not api_key.strip():
        raise ValueError('api_key is required for Vision extraction')

    page_img = _render_page(pdf_bytes, page_index)

    # Diagnostic logging (per user report: "Quick Scan and Thorough Scan
    # giving same results") — confirms which mode this specific page
    # invocation actually received and ran, at the one place both modes
    # branch apart. Do NOT remove without checking the caching-layer
    # findings this was added to help confirm first.
    logger.info(
        '[IOWF] extract_pid_tags_from_page: page=%d thorough=%r -> %s',
        page_index + 1, thorough,
        f'QUICK SCAN ({VISION_PASSES} passes, {VISION_PASSES} calls)' if not thorough else
        f'THOROUGH SCAN ({VISION_TILE_ROWS * VISION_TILE_COLS * VISION_PASSES} calls, '
        f'{VISION_PASSES} passes)',
    )

    if not thorough:
        # Same run-to-run-variance fix as Thorough Scan below, applied to
        # the single full-page image: call Vision VISION_PASSES times and
        # combine+dedupe, instead of trusting one call. Raises only if
        # EVERY pass fails (preserving the original single-call contract
        # callers rely on — extract_pid_tags_via_vision's per-page
        # except/warning and process_pid_vision_page's local-OCR
        # fallback both depend on a total failure actually raising); if
        # at least one pass succeeds, its rows are returned even if
        # another pass failed.
        rows: list[dict] = []
        last_exc: Exception | None = None
        any_success = False
        for pass_num in range(VISION_PASSES):
            tokens_used = 0
            try:
                pass_rows, tokens_used = _extract_tags_from_image(
                    page_img, provider, api_key, page_index, VISION_MAX_DIMENSION_PX,
                )
                rows.extend(pass_rows)
                any_success = True
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception(
                    '[IOWF] Quick Scan: pass %d/%d FAILED on page %d — '
                    'continuing with remaining pass(es)',
                    pass_num + 1, VISION_PASSES, page_index + 1,
                )
            finally:
                _tick(tokens_used)
        if not any_success and last_exc is not None:
            raise last_exc
        return _dedupe_rows(rows)

    tiles = _tile_image(page_img, VISION_TILE_ROWS, VISION_TILE_COLS, VISION_TILE_OVERLAP_FRAC)
    logger.info(
        '[IOWF] Thorough Scan: page %d split into %d tiles (%dx%d grid), '
        'running %d independent pass(es) over the full grid for consistency',
        page_index + 1, len(tiles), VISION_TILE_ROWS, VISION_TILE_COLS, VISION_PASSES,
    )
    rows: list[dict] = []
    for pass_num in range(VISION_PASSES):
        for tile_idx, tile in enumerate(tiles):
            tokens_used = 0
            try:
                tile_rows, tokens_used = _extract_tags_from_image(
                    tile, provider, api_key, page_index, VISION_TILE_MAX_DIMENSION_PX,
                )
                rows.extend(tile_rows)
            except Exception:
                # One bad tile/pass (rate limit, transient error) shouldn't
                # lose every other tile's results for this page — logged
                # and skipped, same degrade-gracefully rule the
                # whole-document loop below already applies at the page
                # level. A tag missed here has another full pass (and its
                # own tile) still able to catch it.
                logger.exception(
                    '[IOWF] Thorough Scan: pass %d/%d tile %d/%d FAILED on page %d — '
                    'continuing with remaining tiles/passes',
                    pass_num + 1, VISION_PASSES, tile_idx + 1, len(tiles), page_index + 1,
                )
            finally:
                _tick(tokens_used)
    # Multiple passes over the SAME tile/image deliberately re-report the
    # same real tags — that's the whole point (a second independent look
    # catches what the first missed) — _dedupe_rows() collapses those
    # duplicates back down; only tags found in just one pass survive as
    # "extra".
    return _dedupe_rows(rows)


def extract_pid_tags_via_vision(
    pdf_bytes: bytes, provider: str, api_key: str, thorough: bool = False,
) -> tuple[list[dict], list[str]]:
    """One page at a time (each page itself either VISION_PASSES calls, or
    VISION_TILE_ROWS x VISION_TILE_COLS x VISION_PASSES calls if
    thorough=True — see extract_pid_tags_from_page), run sequentially in
    this one process —
    returns (rows, warnings): deduped, canonical-shaped I/O List rows for
    the WHOLE document, plus a human-readable warning for every page that
    genuinely failed. Used for documents at or under
    config.PAGE_FANOUT_THRESHOLD; see tasks.py's process_pid_vision_page
    for the parallel Celery version used above that page count.

    BUG FIX: a per-page Vision failure (invalid key, rate limit, network
    error...) used to be logged at ERROR here and then silently
    swallowed — this function never raised, so orchestrator.py's own
    "Vision extraction failed" fallback/warning could never fire, and the
    caller ended up with 0 (or fewer) rows and warnings=[] — an invalid
    API key looked identical to "nothing to extract on this drawing",
    with no way to tell the difference. Now every failed page is
    collected and turned into an explicit warning string in the returned
    warnings list, so a bad key is never silent again.
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    page_count = doc.page_count
    doc.close()

    rows: list[dict] = []
    failed_pages: list[int] = []
    last_status: int | None = None
    for page_index in range(page_count):
        try:
            rows.extend(extract_pid_tags_from_page(pdf_bytes, page_index, provider, api_key, thorough=thorough))
        except Exception as exc:  # noqa: BLE001
            # Deliberately logged at ERROR with exc_info — this is the
            # single most important diagnostic line for "some pages'
            # results missing with a valid key": rendering OR the vision
            # call itself failed for THIS page specifically (corrupt/odd
            # page geometry, auth, rate limit, model-not-available, bad
            # request, timeout...). Look here first when diagnosing a
            # specific page's results being dropped while other pages
            # succeeded — the returned `warnings` (see below) only ever
            # carries a summary, not the per-page detail this line has.
            status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
            logger.error(
                '[IOWF] Vision extraction FAILED on P&ID page %d: %s: %s (status=%s)',
                page_index + 1, type(exc).__name__, exc, status, exc_info=True,
            )
            failed_pages.append(page_index + 1)
            last_status = status
            continue

    deduped = _dedupe_rows(rows)
    _backfill_missing_unit_prefix_across_rows(deduped)
    _warn_about_remaining_unprefixed_tags(deduped)
    _warn_about_tags_mentioned_only_in_location(deduped)

    warnings: list[str] = []
    if failed_pages:
        # 401/403 (or every single page failing, which is the same
        # symptom a bad key produces even when the SDK doesn't expose a
        # status code) point squarely at the API key; anything else
        # (rate limit, timeout, transient network error, one bad page
        # among many good ones) still surfaces — same message, since
        # "check your API key" is the single most actionable thing to
        # tell a user regardless of the exact underlying cause, and this
        # module has no more specific per-status copy to offer.
        if last_status in (401, 403) or len(failed_pages) == page_count:
            warnings.append('Vision extraction failed - please check your API key')
        else:
            warnings.append(
                f'Vision extraction failed on {len(failed_pages)} of {page_count} '
                f'page(s) - please check your API key'
            )
    return deduped, warnings


def test_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """One minimal text-only call to confirm a BYOK key actually works."""
    if not api_key or not api_key.strip():
        return False, 'API key is required.'
    try:
        if provider == 'claude':
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
            client.messages.create(
                model=VISION_MODELS['claude'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                # Same fix as _call_claude — without this, claude-sonnet-5's
                # default thinking would consume the entire 5-token test
                # budget on thinking tokens before this call even proves
                # anything beyond "the key can start a request", and would
                # burn a few thinking tokens on every connectivity check
                # for no reason (this is a plain auth ping, not a task that
                # benefits from reasoning).
                thinking={'type': 'disabled'},
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        elif provider == 'openai':
            import openai
            client = openai.OpenAI(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
            client.chat.completions.create(
                model=VISION_MODELS['openai'],
                max_tokens=TEST_CONNECTION_MAX_TOKENS,
                messages=[{'role': 'user', 'content': 'Hi'}],
            )
        else:
            return False, f"Unsupported provider '{provider}'."
        return True, 'API key is valid and working!'
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
        if status in (401, 403):
            return False, 'Invalid API key. Please check and try again.'
        return False, f'Connection test failed: {exc}'
