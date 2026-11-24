"""Render Office files to PDF. Uses Microsoft Office (COM) when available, else LibreOffice.

Office output matches how the baseline binder was produced; LibreOffice is the server fallback.
Results are cached by content hash so repeated builds do not re-render unchanged sources.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

WORD_FORMATS = {'docx', 'doc', 'dotx', 'rtf'}
SLIDE_FORMATS = {'pptx', 'ppt'}
SHEET_FORMATS = {'xlsx', 'xls'}
RENDER_VERSION = b'3'  # bump when conversion settings change so cached renders are not reused


class ConversionError(RuntimeError):
    pass


def _soffice():
    for candidate in (shutil.which('soffice'), shutil.which('libreoffice'),
                      r'C:\Program Files\LibreOffice\program\soffice.exe'):
        if candidate and Path(candidate).exists():
            return candidate
    return None


class Converter:
    def __init__(self, cache_dir, backend='auto'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self._apps = {}

    def available(self):
        if self.backend in ('auto', 'office'):
            try:
                import win32com.client  # noqa: F401
                return 'office'
            except ImportError:
                if self.backend == 'office':
                    return None
        return 'libreoffice' if _soffice() else None

    def to_pdf(self, source, update_fields=False):
        """Render `source`; `update_fields` refreshes TOC/page fields (generated documents only)."""
        source = Path(source)
        ext = source.suffix.lower().lstrip('.')
        if ext == 'pdf':
            return source
        backend = self.available() or 'none'
        digest = hashlib.sha256(source.read_bytes() + bytes([update_fields]) + RENDER_VERSION
                                + backend.encode()).hexdigest()[:20]
        target = self.cache_dir / f'{digest}.pdf'
        if target.exists() and target.stat().st_size:
            return target
        if backend == 'office':
            self._office(source, target, ext, update_fields)
        elif backend == 'libreoffice':
            self._libreoffice(source, target)
        else:
            raise ConversionError('No PDF converter available (install Microsoft Office or LibreOffice)')
        if not target.exists():
            raise ConversionError(f'{source.name}: converter produced no output')
        return target

    def _app(self, progid):
        if progid not in self._apps:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            app = win32com.client.DispatchEx(progid)
            try:
                app.DisplayAlerts = 0
            except Exception:
                pass
            self._apps[progid] = app
        return self._apps[progid]

    def _office(self, source, target, ext, update_fields=False):
        path = str(source.resolve())
        try:
            if ext in WORD_FORMATS:
                word = self._app('Word.Application')
                try:  # print/export exactly what the author saved; no field refresh on source documents
                    word.Options.UpdateFieldsAtPrint = bool(update_fields)
                    word.Options.UpdateLinksAtPrint = False
                except Exception:
                    pass
                document = word.Documents.Open(path, ConfirmConversions=False, ReadOnly=True,
                                               AddToRecentFiles=False, Visible=False)
                try:
                    if update_fields:
                        document.Fields.Update()
                        for toc in document.TablesOfContents:
                            toc.Update()
                    else:
                        # Freeze saved field results (TOC page refs etc.) for source documents;
                        # the change is never saved (document opened read-only, closed unsaved).
                        for story in document.StoryRanges:
                            story.Fields.Locked = True
                    document.ExportAsFixedFormat(str(target), 17)  # wdExportFormatPDF
                finally:
                    document.Close(False)
            elif ext in SLIDE_FORMATS:
                powerpoint = self._app('PowerPoint.Application')
                presentation = powerpoint.Presentations.Open(path, True, False, False)
                try:
                    presentation.SaveAs(str(target), 32)  # ppSaveAsPDF
                finally:
                    presentation.Close()
            elif ext in SHEET_FORMATS:
                excel = self._app('Excel.Application')
                workbook = excel.Workbooks.Open(path, ReadOnly=True)
                try:
                    workbook.ExportAsFixedFormat(0, str(target))
                finally:
                    workbook.Close(False)
            else:
                raise ConversionError(f'{source.name}: unsupported format .{ext}')
        except ConversionError:
            raise
        except Exception as error:
            raise ConversionError(f'{source.name}: Office conversion failed: {error}') from error

    def _libreoffice(self, source, target):
        outdir = self.cache_dir / 'lo'
        outdir.mkdir(exist_ok=True)
        # A private profile per converter lets parallel workers run soffice without profile locks.
        profile = (self.cache_dir / 'lo_profile').resolve().as_uri()
        result = subprocess.run([_soffice(), f'-env:UserInstallation={profile}', '--headless', '--norestore',
                                 '--convert-to', 'pdf', '--outdir', str(outdir), str(source)],
                                capture_output=True, timeout=600, check=False)
        produced = outdir / (source.stem + '.pdf')
        if result.returncode or not produced.exists():
            raise ConversionError(f'{source.name}: LibreOffice conversion failed ({result.returncode})')
        os.replace(produced, target)

    def close(self):
        for app in self._apps.values():
            try:
                app.Quit()
            except Exception:
                pass
        self._apps.clear()
