"""Isolated local PDF/OCR execution; no Django, providers or network calls.

Run with python -m ...local_extraction. The parent kills the process on its
remaining chunk budget, including native OCR/model initialization time.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .local_tables import LocalTableParser, positioned_rows
from .lng_tables import LNGTableReader


def local_capabilities(config):
    def installed(name):
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    paddle_models = [config.get('paddle_detection_model_dir', ''), config.get('paddle_recognition_model_dir', '')]
    rapid_spec = importlib.util.find_spec('rapidocr_onnxruntime')
    rapid_assets = bool(rapid_spec and rapid_spec.origin and len(list(Path(rapid_spec.origin).parent.joinpath('models').glob('*.onnx'))) >= 3)
    return {
        'mode': 'local_only' if config.get('local_only', True) else 'local_first', 'structured_tables': True,
        'engineering_acceptance_verified': False,
        'engines': [
            {'id': 'pymupdf_text', 'label': 'PDF text tables', 'available': installed('fitz')},
            {'id': 'rapidocr', 'label': 'RapidOCR', 'available': rapid_assets,
             'readiness': 'installation_only; packaged model assets required'},
            {'id': 'paddleocr', 'label': 'PaddleOCR',
             'available': installed('paddleocr') and all(value and Path(value).is_dir() for value in paddle_models),
             'readiness': 'local detection and recognition model directories required'},
            {'id': 'tesseract', 'label': 'Tesseract',
             'available': installed('pytesseract') and bool(shutil.which('tesseract')),
             'readiness': 'binary and language data required'},
        ],
        'limitations': 'Explicit class headings and recognised table columns only. Review extracted records against the source.',
    }


def _last_result(output):
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    for line in reversed((output or '').splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and 'piping_classes' in result:
            return result
    return None


def run_local_engine(pdf_path, start, end, engine, config, timeout, pages=None):
    request = dict(pdf_path=str(pdf_path), start=start, end=end, engine=engine, config={
        key: config[key] for key in ('piping_class_header_regex', 'render_dpi', 'max_image_size',
                                    'paddle_detection_model_dir', 'paddle_recognition_model_dir',
                                    'lng_context_lookback_pages', 'local_engine_threads') if key in config})
    request['pages'] = pages
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = str(config.get('local_engine_threads', 1))
    # Only local parser configuration goes into the child. Never serialize keys,
    # provider clients or the environment to the result/logs.
    try:
        completed = subprocess.run(
            [sys.executable, '-m', 'apps.spec_customization.services.local_extraction'],
            input=json.dumps(request), text=True, encoding='utf-8', capture_output=True,
            timeout=max(.01, timeout), cwd=str(Path(__file__).resolve().parents[3]),
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except subprocess.TimeoutExpired as exc:
        # Each completed page emits a checkpoint. A slow later page must not
        # erase already parsed source rows when the child is killed.
        partial = _last_result(exc.stdout)
        if partial is not None:
            processed = set(partial.get('pages_processed', []))
            pending = [page for page in (pages if pages is not None else range(start + 1, end + 2)) if page not in processed]
            partial.setdefault('local_report', {}).setdefault('issues', []).extend(
                {'page': page, 'reason': 'local_engine_timeout', 'engine': engine} for page in pending)
            partial['timed_out'] = bool(pending)
            return partial
        raise TimeoutError('Local extraction exceeded the remaining chunk budget.') from exc
    if completed.returncode:
        raise OSError('Local extraction process failed; inspect local engine installation.')
    result = _last_result(completed.stdout)
    if result is None:
        raise OSError('Local extraction process returned no structured result.')
    return result


def _ocr_words(engine, image, reader):
    import numpy as np
    if engine == 'rapidocr':
        result, _ = reader(np.asarray(image))
        records = [(item[0], item[1]) for item in (result or [])]
    elif engine == 'paddleocr':
        records = []
        for result in reader.predict(np.asarray(image)):
            payload = dict(result)
            for box, text in zip(payload.get('rec_polys', payload.get('rec_boxes', [])), payload.get('rec_texts', [])):
                box = np.asarray(box)
                if box.ndim == 1:
                    x0, y0, x1, y1 = box.tolist()
                    box = [[x0, y0], [x1, y1]]
                records.append((box, text))
    else:
        import pytesseract
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        return [(data['left'][i], data['top'][i], data['left'][i] + data['width'][i],
                 data['top'][i] + data['height'][i], text)
                for i, text in enumerate(data['text']) if str(text).strip()]
    words = []
    for box, text in records:
        coords = np.asarray(box)
        words.append((float(coords[:, 0].min()), float(coords[:, 1].min()),
                      float(coords[:, 0].max()), float(coords[:, 1].max()), str(text)))
    return words


def _rapidocr_reader(config):
    from rapidocr_onnxruntime import RapidOCR
    from rapidocr_onnxruntime import utils
    # The pinned 1.3.2 SDK has no public thread option. Its SessionOptions
    # factory is used only during construction in this isolated, single-threaded
    # child. Bound ONNX pools explicitly: OMP variables alone do not bound them.
    factory = utils.SessionOptions
    def bounded_options():
        options = factory()
        options.intra_op_num_threads = int(config.get('local_engine_threads', 1))
        options.inter_op_num_threads = 1
        return options
    utils.SessionOptions = bounded_options
    try:
        return RapidOCR()
    finally:
        utils.SessionOptions = factory


def extract_local(request, on_progress=None):
    import fitz
    config = request['config']
    engine = request['engine']
    parser = LocalTableParser(config['piping_class_header_regex'], engine)
    lng = LNGTableReader(parser, config.get('lng_context_lookback_pages', 64))
    reader = None
    if engine != 'pymupdf_text':
        readiness = next(item for item in local_capabilities(config)['engines'] if item['id'] == engine)
        if not readiness['available']:
            return {'piping_classes': [], 'engine_used': engine, 'available': False}
        if engine == 'rapidocr':
            reader = _rapidocr_reader(config)
        elif engine == 'paddleocr':
            from paddleocr import PaddleOCR
            reader = PaddleOCR(
                text_detection_model_dir=config['paddle_detection_model_dir'],
                text_recognition_model_dir=config['paddle_recognition_model_dir'],
                use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False,
            )
    chars = 0
    ocr_pages = []
    processed = []
    pages_with_components = []
    def snapshot():
        return {**parser.result(), 'page_text_chars': chars, 'available': True,
                'ocr_pages': list(ocr_pages), 'pages_processed': list(processed),
                'pages_with_components': list(pages_with_components)}
    with fitz.open(request['pdf_path']) as document:
        for index in range(request['start'], min(request['end'] + 1, len(document))):
            if request.get('pages') is not None and index + 1 not in request['pages']:
                continue
            page = document[index]
            previous_count = parser.component_count
            if engine == 'pymupdf_text':
                text = page.get_text('text', sort=True)
                chars += len(text)
                # Scanned tables may have a readable native title/footer. Do not
                # mistake that overlay for a fully readable native page.
                images = page.get_image_info()
                image_dominated = any((fitz.Rect(item['bbox']) & page.rect).get_area() >= page.rect.get_area() * .5 for item in images)
                if images and (len(text.strip()) < 100 or image_dominated):
                    ocr_pages.append(index + 1)
                    parser.issues.append({'page': index + 1, 'reason': 'image_page_requires_ocr'})
                words = page.get_text('words')
                if not lng.read_page(document, index, text, words):
                    if '|' in text or '\t' in text:
                        parser.read_text(text, index + 1)
                    else:
                        parser.read_rows(positioned_rows(words), index + 1)
                if 'COMPONENT MATERIAL DESCRIPTION' in text.upper():
                    parser.issues.append({'page': index + 1, 'reason': 'material_description_join_not_supported'})
            else:
                from PIL import Image
                scale = min(config.get('render_dpi', 150) / 72,
                            config.get('max_image_size', 3072) / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
                image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
                parser.read_rows(positioned_rows(_ocr_words(engine, image, reader)), index + 1)
                if parser.component_count == previous_count:
                    parser.issues.append({'page': index + 1, 'reason': 'ocr_no_component_rows', 'engine': engine})
            processed.append(index + 1)
            if parser.component_count > previous_count:
                pages_with_components.append(index + 1)
            if on_progress:
                on_progress(snapshot())
    return snapshot()


if __name__ == '__main__':
    # Some native libraries print banners. Keep the stdout protocol JSON only.
    import contextlib
    request = json.load(sys.stdin)
    protocol = sys.stdout
    def checkpoint(result):
        protocol.write(json.dumps(result) + '\n')
        protocol.flush()
    with contextlib.redirect_stdout(sys.stderr):
        result = extract_local(request, on_progress=checkpoint)
    checkpoint(result)
