"""
Drawing Segmentation Service
=============================
Splits a PDF/image document into independent P&ID drawings.
Each page is treated as one drawing (deterministic, no AI).
"""
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class SegmentedDrawing:
    drawing_id: str
    page_index: int
    title: str = ''
    metadata: dict = field(default_factory=dict)


def segment_document(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    """
    Segment a document into individual drawings.

    Strategy (deterministic):
      • PDF  → one drawing per page
      • Image → single drawing
      • DWG  → single drawing (DWG parsing requires AutoCAD libraries; treat as one)

    Returns a sorted list of SegmentedDrawing objects.
    """
    ext = file_path.rsplit('.', 1)[-1].lower()
    drawings: List[SegmentedDrawing] = []

    if ext == 'pdf':
        drawings = _segment_pdf(document_id, file_path)
        if not drawings:
            logger.warning(
                '[PIDVerification] PDF segmentation produced 0 pages for %s; applying single-page fallback',
                document_id,
            )
            drawings = [
                SegmentedDrawing(
                    drawing_id=f'{document_id}-DRAWING-1',
                    page_index=0,
                    title='',
                    metadata={'source_format': 'pdf', 'fallback': 'empty_pdf_page_list'},
                )
            ]
    else:
        # Single-drawing documents
        drawings = [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'source_format': ext},
            )
        ]

    logger.info(
        '[PIDVerification] Segmented document %s → %d drawing(s)',
        document_id, len(drawings)
    )
    return drawings


def _segment_pdf(document_id: str, file_path: str) -> List[SegmentedDrawing]:
    """One SegmentedDrawing per PDF page using PyMuPDF (fitz) or fallback."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        results = []
        for i, page in enumerate(doc):
            text_preview = page.get_text('text')[:200].strip()
            results.append(
                SegmentedDrawing(
                    drawing_id=f'{document_id}-DRAWING-{i + 1}',
                    page_index=i,
                    title=_extract_title_from_text(text_preview),
                    metadata={'page_count': len(doc), 'page_index': i},
                )
            )
        doc.close()
        return results
    except ImportError:
        logger.warning('[PIDVerification] PyMuPDF not installed – treating PDF as single drawing')
        return [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'source_format': 'pdf', 'fallback': True},
            )
        ]
    except Exception as exc:
        logger.error('[PIDVerification] PDF segmentation error: %s', exc)
        return [
            SegmentedDrawing(
                drawing_id=f'{document_id}-DRAWING-1',
                page_index=0,
                title='',
                metadata={'error': str(exc)},
            )
        ]


def _extract_title_from_text(text: str) -> str:
    """
    Heuristic: first non-empty line ≤ 120 chars is likely the drawing title.
    Fully deterministic.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) <= 120:
            return stripped
    return ''
