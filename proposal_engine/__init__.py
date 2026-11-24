"""Template-driven technical proposal engine (DEC-TP-001).

Framework-independent core shared by the CLI (`python -m proposal_engine`) and the Django
sales module. Pipeline: scan → plan → extract → validate → render (master DOCX, client PDF binder).
"""
from .pipeline import build_proposal  # noqa: F401
