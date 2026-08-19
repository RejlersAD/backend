"""
Document segmentation — splits a PDF into one drawing per page.
Returns a list of SegmentedDrawing dataclasses.
"""
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class SegmentedDrawing:
    drawing_id: str
    title:      str
    page_index: int
    metadata:   dict = field(default_factory=dict)


def segment_document(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    """
    Open the PDF at file_path and return one SegmentedDrawing per page.
    Uses PyMuPDF (fitz) when available, falls back to a single-segment stub.
    """
    try:
        import fitz  # PyMuPDF
        return _segment_with_fitz(document_id, file_path)
    except ImportError:
        logger.warning('[PFDQ Segmentation] PyMuPDF not available — treating file as single drawing')
        return [SegmentedDrawing(
            drawing_id = f'{document_id}_drawing_001',
            title      = 'PFD Drawing 1',
            page_index = 0,
            metadata   = {'page_count': 1, 'source': 'fallback'},
        )]


def _segment_with_fitz(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    import fitz

    pdf = fitz.open(file_path)
    segments: List[SegmentedDrawing] = []

    for i, page in enumerate(pdf):
        page_num  = i + 1
        raw_title = _extract_title_from_page(page)
        title     = raw_title or f'PFD Drawing {page_num}'

        segments.append(SegmentedDrawing(
            drawing_id = f'{document_id}_drawing_{page_num:03d}',
            title      = title,
            page_index = i,
            metadata   = {
                'page_number': page_num,
                'width_pt':    page.rect.width,
                'height_pt':   page.rect.height,
            },
        ))

    pdf.close()
    logger.info('[PFDQ Segmentation] %d segments from %s', len(segments), file_path)
    return segments


def _extract_title_from_page(page) -> str:
    """
    Heuristic: find the largest text block or blocks near the title block
    area (bottom 20% of page) as the drawing title.
    """
    try:
        blocks = page.get_text('dict').get('blocks', [])
        candidates = []
        height = page.rect.height

        for block in blocks:
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = span.get('text', '').strip()
                    size = span.get('size', 0)
                    y    = span.get('origin', (0, 0))[1]
                    # Prefer large text in title-block zone (bottom quarter)
                    if text and size >= 10 and y > height * 0.75:
                        candidates.append((size, text))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1][:120]
    except Exception:
        pass
    return ''
