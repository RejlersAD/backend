"""Fit generated requisition forms to one page without dropping their contents."""

from reportlab.platypus import KeepInFrame


def build_single_page_requisition(document, story):
    # SimpleDocTemplate's frame has 6pt padding inside each document margin.
    # Measure actual table widths as well as text height before proportional
    # scaling; the requisition contains fixed-width tables wider than its frame.
    fitted = KeepInFrame(
        document.width - 12, document.height - 12, story,
        mode='shrink', hAlign='CENTER', vAlign='TOP', fakeWidth=False,
    )
    document.build([fitted])
