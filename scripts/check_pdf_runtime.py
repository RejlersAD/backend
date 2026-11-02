"""Smoke-check the deployed PDF dependency without Django, DB or credentials.

Run in a fresh process: python scripts/check_pdf_runtime.py
The web URL registry imports pymupdf, so importing it must not initialize the
optional Layout/ONNX inference runtime just to serve unrelated API requests.
"""
import importlib.metadata
import sys
import time


def main():
    started = time.monotonic()
    import pymupdf

    imported = time.monotonic() - started
    if any(name == 'onnxruntime' or name.startswith('onnxruntime.') for name in sys.modules):
        raise RuntimeError('Basic PDF import initialized ONNX; check the deployed dependency version.')

    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), 'RADAI PDF runtime smoke check')
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype='pdf') as document:
        if 'RADAI PDF runtime smoke check' not in document[0].get_text():
            raise RuntimeError('PDF text round trip failed.')
        image = document[0].get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5))
        if image.width <= 0 or image.height <= 0:
            raise RuntimeError('PDF page rendering failed.')

    print(f"PDF runtime OK: PyMuPDF {importlib.metadata.version('PyMuPDF')}, "
          f'import {imported:.3f}s, text and rendering passed, no ONNX initialization.')


if __name__ == '__main__':
    main()
