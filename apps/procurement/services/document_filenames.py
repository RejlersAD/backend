"""Consistent filenames for saved procurement PDF documents."""

import re
from datetime import date, datetime

from django.utils import timezone


_MODULE_LABELS = {
    "po": "Purchase_Order",
    "purchase_order": "Purchase_Order",
    "pr": "Purchase_Requisition",
    "purchase_requisition": "Purchase_Requisition",
}


def build_procurement_pdf_filename(
    document_number: object,
    module_name: str,
    document_date: date | datetime | None = None,
) -> str:
    """Return ``<number>_<module>_<YYYY-MM-DD>.pdf`` using safe path characters."""
    module_label = _MODULE_LABELS.get(str(module_name or "").strip().lower())
    if not module_label:
        raise ValueError(f"Unsupported procurement PDF module: {module_name}")

    number = re.sub(r"[^A-Za-z0-9._-]+", "_", str(document_number or "").strip())
    number = number.strip("._-") or module_name.upper()

    effective_date = document_date or timezone.localdate()
    if isinstance(effective_date, datetime):
        effective_date = effective_date.date()

    return f"{number}_{module_label}_{effective_date:%Y-%m-%d}.pdf"
