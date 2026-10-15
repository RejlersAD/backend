"""
Annotated PDF Download — overlays color-coded boxes on the original
P&ID/SLD drawing, one per ElectricalComparisonResult tag, so a reviewer
can see matched/missing/extra tags directly on the drawing instead of
only in the results table.
"""
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
