"""RADAI Line Tagger — a purpose-trained token-classification model for P&ID
line-list extraction.

This package lives OUTSIDE the Django backend (root `ai/` folder) so the ML
code, training data, and model weights stay decoupled from the web app and can
be versioned/trained/shipped independently. The backend imports it as a
confidence-gated validator/fallback alongside the existing regex parser
(backend/apps/pid_checker_v2/services/line_list_parser.py).

Pipeline position (hybrid — regex first, ML catches the messy cases):
    P&ID PDF → OCR text+layout → regex parser (fast path)
                                   └─ low-confidence rows → line_tagger.infer
                                                              → tagged fields

Model: LayoutLMv3 token classification (text + 2D position — critical for
P&IDs where the same characters mean different things at different locations).

Labels (BIO scheme):
    B-SIZE / I-SIZE        — nominal bore (2", 3/4")
    B-SERVICE / I-SERVICE  — service/fluid code (D, FL, UA, IA)
    B-SPEC / I-SPEC        — piping spec (A1AU01, 033842)
    B-SERIAL / I-SERIAL    — line sequence/serial (00066, 149472)
    B-FROM / I-FROM        — upstream reference
    B-TO / I-TO            — downstream reference
    O                      — anything else

All knobs are soft-coded in `config.py`.
"""

__version__ = '0.1.0'
