"""
Annotated PDF Download — overlays color-coded boxes on the original
P&ID/SLD drawing, one per ElectricalComparisonResult tag, so a reviewer
can see matched/missing/extra tags directly on the drawing instead of
only in the results table.
"""
import base64

import fitz

_STATUS_COLORS = {
    'matched': (0, 1, 0),   # green
    'missing': (1, 0, 0),   # red
    'extra': (1, 1, 0),     # yellow
}
_STATUS_LABELS = {
    'matched': '[MATCHED]',
    'missing': '[MISSING]',
    'extra': '[EXTRA]',
}
_BOX_WIDTH = 1.5
_LABEL_FONT_SIZE = 6


def annotate_sld_pdf(pdf_bytes, results):
    """Draws a colored box around every instance of each result's
    tag_number found on the drawing, plus a small status label above the
    box, color-matched to the box (green/[MATCHED], red/[MISSING],
    yellow/[EXTRA]).

    results: [{'tag_number', 'status', 'equipment_type'}, ...] — status
    values other than matched/missing/extra, and tags with no text match
    anywhere in the PDF (e.g. a 'missing' tag that only ever existed in
    an Excel register, never on the drawing itself), are silently
    skipped. This is a visual aid, not a source of truth, so it never
    raises over a tag it can't place.

    Returns the annotated PDF as bytes.
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for result in results:
            tag_number = str(result.get('tag_number') or '').strip()
            color = _STATUS_COLORS.get(result.get('status'))
            if not tag_number or color is None:
                continue
            label = _STATUS_LABELS[result['status']]
            for page in doc:
                for rect in page.search_for(tag_number):
                    page.draw_rect(rect, color=color, width=_BOX_WIDTH)
                    label_point = fitz.Point(rect.x0, max(rect.y0 - 2, 0))
                    page.insert_text(label_point, label, fontsize=_LABEL_FONT_SIZE, color=color)
        return doc.tobytes()
    finally:
        doc.close()


# Hex colors for the canvas viewer (frontend draws these) — same
# matched/missing/extra semantics as _STATUS_COLORS above.
_STATUS_HEX = {
    'matched': '#16a34a',   # green
    'missing': '#dc2626',   # red
    'extra': '#ca8a04',     # yellow/amber
}

# 'missing' tags the targeted Vision locator pass (tag_extractor.
# locate_tags_on_drawing) managed to find on the scanned drawing anyway.
_LOCATED_HEX = '#7c3aed'   # purple (unused fallback now — kept for any
                           # other caller; get_annotated_pages below uses
                           # _LOCATED_STATUS_HEX/_LOCATED_FALLBACK_HEX)

# Status colors for located_boxes hits specifically (targeted Vision
# locator pass) — tag_statuses (passed alongside located) lets each hit
# be colored by its REAL comparison status instead of always purple.
# Distinct literal hex values from _STATUS_HEX/_LOCATED_HEX above, per
# explicit spec for this feature.
_LOCATED_STATUS_HEX = {
    'matched': '#00C851',
    'missing': '#FF4444',
    'extra': '#FFD700',
}
_LOCATED_FALLBACK_HEX = '#9B59B6'   # unknown tag / no tag_statuses entry

# Cap the rendered page image's longest side so a big A1/A0 SLD drawing
# stays a manageable payload for the in-browser canvas viewer.
_MAX_PAGE_PIXELS = 2400


def get_annotated_pages(pdf_bytes, results, max_pixels=_MAX_PAGE_PIXELS, located=None, tag_statuses=None):
    """Canvas-viewer variant of annotate_sld_pdf — instead of returning a
    modified PDF, renders each page of the original drawing to a PNG and
    returns the box coordinates alongside it, so the frontend can draw
    the same color-coded highlights on a <canvas> (and restyle/toggle
    them without a server round-trip).

    located: optional {tag: [{'page': 1-based, 'x0','y0','x1','y1'}]}
    from tag_extractor.locate_tags_on_drawing — now covers every tag
    (matched/missing/extra), not just 'missing' ones; see tasks.py's own
    locator-pass comment. Each hit keeps 'status': 'located' (so the
    frontend's existing "located" canvas-filter toggle still groups them
    together), but its 'color' now reflects the tag's REAL comparison
    status via tag_statuses, instead of always being purple.

    tag_statuses: optional {tag_number: status} — same job.tag_statuses
    snapshot tasks.py saves alongside located_boxes. Looked up per hit;
    falls back to _LOCATED_FALLBACK_HEX (purple) for a tag with no entry
    (e.g. older jobs saved before tag_statuses existed).

    Returns:
    [{'page': 1-based int,
      'width': int, 'height': int,          # rendered image pixels
      'image': 'data:image/png;base64,...',
      'boxes': [{'tag_number', 'status', 'color',
                 'x0', 'y0', 'x1', 'y1'}]}] # in image-pixel coordinates

    Tags not found on a page simply contribute no boxes there (same
    "visual aid, never raise" policy as annotate_sld_pdf).
    """
    wanted = []
    for result in results:
        tag_number = str(result.get('tag_number') or '').strip()
        color = _STATUS_HEX.get(result.get('status'))
        if tag_number and color:
            wanted.append((tag_number, result['status'], color))

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        located = located or {}
        tag_statuses = tag_statuses or {}
        pages = []
        for page_index, page in enumerate(doc):
            zoom = min(max_pixels / page.rect.width, max_pixels / page.rect.height, 3.0)
            zoom = max(zoom, 0.5)
            matrix = fitz.Matrix(zoom, zoom)

            boxes = []
            for tag_number, status_val, color in wanted:
                for rect in page.search_for(tag_number):
                    boxes.append({
                        'tag_number': tag_number,
                        'status': status_val,
                        'color': color,
                        'x0': rect.x0 * zoom,
                        'y0': rect.y0 * zoom,
                        'x1': rect.x1 * zoom,
                        'y1': rect.y1 * zoom,
                    })

            # Targeted-locator boxes (scanned drawings have no text layer
            # for search_for — these coordinates came from the Vision
            # model itself, in PDF points like search_for's rects).
            # Colored by the tag's real comparison status (via
            # tag_statuses) rather than always purple — see this
            # function's own docstring.
            for tag_number, hits in located.items():
                real_status = tag_statuses.get(tag_number)
                if real_status == 'matched':
                    located_color = _LOCATED_STATUS_HEX['matched']
                elif real_status == 'missing':
                    located_color = _LOCATED_STATUS_HEX['missing']
                elif real_status == 'extra':
                    located_color = _LOCATED_STATUS_HEX['extra']
                else:
                    located_color = _LOCATED_FALLBACK_HEX
                for hit in hits:
                    if hit.get('page') != page_index + 1:
                        continue
                    boxes.append({
                        'tag_number': tag_number,
                        'status': 'located',
                        'color': located_color,
                        'x0': hit['x0'] * zoom,
                        'y0': hit['y0'] * zoom,
                        'x1': hit['x1'] * zoom,
                        'y1': hit['y1'] * zoom,
                    })

            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            png_b64 = base64.b64encode(pixmap.tobytes('png')).decode('ascii')
            pages.append({
                'page': page_index + 1,
                'width': pixmap.width,
                'height': pixmap.height,
                'image': f'data:image/png;base64,{png_b64}',
                'boxes': boxes,
            })
        return pages
    finally:
        doc.close()
