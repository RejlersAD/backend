"""python-docx helpers: templates (.docx/.dotx), generated TOC/dividers, and document merging."""
from __future__ import annotations

import io
import re
import zipfile
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

DOTX_TYPE = b'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml'
DOCX_TYPE = b'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
R_ATTRS = (qn('r:embed'), qn('r:id'), qn('r:link'), qn('r:pict'))
IMAGE_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
HYPERLINK_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink'


def open_template(path):
    """Open a .docx or .dotx template as a new Document (a .dotx is re-typed as a document)."""
    data = Path(path).read_bytes()
    if str(path).lower().endswith('.dotx'):
        source, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                blob = source.read(item.filename)
                if item.filename == '[Content_Types].xml':
                    blob = blob.replace(DOTX_TYPE, DOCX_TYPE)
                target.writestr(item, blob)
        data = out.getvalue()
    return Document(io.BytesIO(data))


def replace_text(document, old, new):
    """Replace text in body, headers and footers at w:t level (tokens must not be split across runs)."""
    parts = [document.element.body]
    for package_part in document.part.package.iter_parts():
        if re.search(r'/word/(header|footer)\d*\.xml$', str(package_part.partname)) and hasattr(package_part, 'element'):
            parts.append(package_part.element)
    count = 0
    for root in parts:
        for paragraph in root.iter(qn('w:p')):
            nodes = [node for node in paragraph.iter(qn('w:t'))
                     if next(node.iterancestors(qn('w:p')), None) is paragraph]
            joined = ''.join(node.text or '' for node in nodes)
            while old in joined:
                start = joined.index(old)
                end = start + len(old)
                position, first = 0, True
                for node in nodes:
                    text = node.text or ''
                    lo, hi = position, position + len(text)
                    position = hi
                    if hi <= start or lo >= end:
                        continue
                    keep_before = text[:max(0, start - lo)]
                    keep_after = text[max(0, end - lo):] if hi > end else ''
                    node.text = keep_before + (new if first else '') + keep_after
                    first = False
                joined = ''.join(node.text or '' for node in nodes)
                count += 1
    return count


def clear_body(document):
    body = document.element.body
    for child in list(body):
        if child.tag != qn('w:sectPr'):
            body.remove(child)


def curate_template(source, target, replacements):
    """Create a curated company template from an approved document: keep styles, numbering,
    page setup, headers and footers; empty the body; turn bid values into tokens."""
    document = Document(str(source))
    clear_body(document)
    for sect in document.element.body.iter(qn('w:sectPr')):
        # Drop first-page / even-page header/footer links that the source never displays
        # (no titlePg / evenAndOddHeaders), so dormant legacy content cannot resurface.
        for reference in list(sect):
            if reference.tag in (qn('w:headerReference'), qn('w:footerReference')):
                kind = reference.get(qn('w:type'))
                if (kind == 'first' and sect.find(qn('w:titlePg')) is None) or kind == 'even':
                    sect.remove(reference)
                    document.part.drop_rel(reference.get(qn('r:id')))
    for old, new in replacements:
        replace_text(document, old, new)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))


def strip_shapes(document, *, text_markers=(), image_descriptions=()):
    """Remove floating shapes (text boxes, pictures) whose own text contains a marker or whose own
    picture description matches — e.g. legacy cover text hidden behind images, client logos.
    A picture nested inside a text box removes only that picture, never the enclosing text box."""
    alternate = '{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent'
    doc_pr = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr'
    removed = 0
    for drawing in reversed(list(document.element.body.iter(qn('w:drawing')))):
        if drawing.getroottree().getroot() is not document.element:
            continue  # already removed with an enclosing shape
        holder = drawing
        for ancestor in drawing.iterancestors():
            if ancestor.tag in (qn('w:drawing'), qn('w:txbxContent'), qn('w:pict')):
                break  # nested inside another shape (incl. VML fallbacks): remove only this drawing
            if ancestor.tag == alternate:
                holder = ancestor
                break
        nested = {id(node) for inner in drawing.iter(qn('w:drawing')) if inner is not drawing
                  for node in inner.iter(qn('w:t'))}
        own_text = ''.join(node.text or '' for node in drawing.iter(qn('w:t')) if id(node) not in nested)
        own_pr = next((pr for pr in drawing.iter(doc_pr)
                       if next(pr.iterancestors(qn('w:drawing')), None) is drawing), None)
        descr = own_pr.get('descr', '') if own_pr is not None else ''
        if any(m in own_text for m in text_markers) or any(m in descr for m in image_descriptions):
            if holder.getparent() is not None:
                holder.getparent().remove(holder)
                removed += 1
    return removed


def fill_tokens(path, out_path, values):
    """Copy a template and replace `{{token}}` values everywhere (body, headers, text boxes)."""
    document = Document(str(path))
    for token, value in values.items():
        replace_text(document, '{{%s}}' % token, str(value))
    document.save(str(out_path))
    return out_path


# --- generated TOC and divider pages -------------------------------------------------------

def _is_page_break(element):
    return element.tag == qn('w:p') and any(br.get(qn('w:type')) == 'page' for br in element.iter(qn('w:br')))


def _style(element):
    node = element.find(qn('w:pPr') + '/' + qn('w:pStyle'))
    return node.get(qn('w:val')) if node is not None else ''


def _text(element):
    return ''.join(node.text or '' for node in element.iter(qn('w:t')))


def _set_runs(paragraph, pieces, rpr_from=None):
    """Replace a paragraph's content with runs; `pieces` are strings or '\t'. Removes numbering."""
    template_rpr = None
    source = rpr_from if rpr_from is not None else paragraph
    for run in source.iter(qn('w:r')):
        if _text(run).strip():
            rpr = run.find(qn('w:rPr'))
            template_rpr = deepcopy(rpr) if rpr is not None else None
    if template_rpr is not None:  # drop hyperlink character formatting inherited from TOC entries
        for tag in ('w:rStyle', 'w:u', 'w:color'):
            node = template_rpr.find(qn(tag))
            if node is not None:
                template_rpr.remove(node)
    for child in list(paragraph):
        if child.tag == qn('w:pPr'):
            continue
        # Keep anchored graphics (letterhead art, stamps) but remove all text and fields.
        if any(True for _ in child.iter(qn('w:drawing'), qn('w:pict'))):
            for node in list(child.iter(qn('w:t'), qn('w:tab'), qn('w:instrText'), qn('w:fldChar'))):
                if not any(a.tag in (qn('w:drawing'), qn('w:pict')) for a in node.iterancestors()):
                    node.getparent().remove(node)
            continue
        paragraph.remove(child)
    ppr = paragraph.find(qn('w:pPr'))
    if ppr is not None:
        numpr = ppr.find(qn('w:numPr'))
        if numpr is not None:
            ppr.remove(numpr)
        numpr = OxmlElement('w:numPr')
        for tag, value in (('w:ilvl', '0'), ('w:numId', '0')):  # numId 0 suppresses style numbering
            node = OxmlElement(tag)
            node.set(qn('w:val'), value)
            numpr.append(node)
        anchor = ppr.find(qn('w:pStyle'))
        (anchor.addnext(numpr) if anchor is not None else ppr.insert(0, numpr))
    for piece in pieces:
        run = OxmlElement('w:r')
        if template_rpr is not None:
            run.append(deepcopy(template_rpr))
        if piece == '\t':
            run.append(OxmlElement('w:tab'))
        else:
            text = OxmlElement('w:t')
            text.set(qn('xml:space'), 'preserve')
            text.text = piece
            run.append(text)
        paragraph.append(run)


def build_frontmatter(template_path, toc_entries, dividers, out_path):
    """Write a DOCX whose page 1 is the TOC and each following page one divider.

    The divider template's own layout is reused: its first page (up to the first page break)
    is the TOC page, and its first divider block (page break … centred title) is cloned for
    every divider. Numbers come from the configured section tree, never from list numbering.
    """
    document = open_template(template_path)
    body = document.element.body
    children = [c for c in body if c.tag != qn('w:sectPr')]
    first_break = next(i for i, c in enumerate(children) if _is_page_break(c))
    title_index = next(i for i, c in enumerate(children[first_break + 1:], first_break + 1)
                       if c.tag == qn('w:p') and _text(c).strip())
    toc_page, divider_block = children[:first_break], children[first_break:title_index + 1]
    for child in children:
        body.remove(child)
    sect = body.find(qn('w:sectPr'))

    toc_items = [c for c in toc_page if _style(c).lower().startswith('toc') and _text(c).strip()]

    def toc_proto(number):
        # Same-depth template entry (e.g. "2.1" vs "1.") keeps its own indents and tab stops.
        depth = len([part for part in number.split('.') if part])
        same = [c for c in toc_items if len([p for p in re.match(r'^[\d.]*', _text(c)).group(0).split('.') if p]) == depth]
        return (same or toc_items or [None])[0]
    emitted = False
    for element in toc_page:
        if _style(element).lower().startswith('toc'):
            if emitted or not toc_items:
                continue
            for number, title in toc_entries:
                paragraph = deepcopy(toc_proto(number))
                for bookmark in list(paragraph.iter(qn('w:bookmarkStart'))) + list(paragraph.iter(qn('w:bookmarkEnd'))):
                    bookmark.getparent().remove(bookmark)
                _set_runs(paragraph, [number, '\t', title] if number else [title])
                sect.addprevious(paragraph)
            emitted = True
            continue
        sect.addprevious(deepcopy(element))
    for label in dividers:
        block = [deepcopy(element) for element in divider_block]
        title = block[-1]
        for bookmark in list(title.iter(qn('w:bookmarkStart'))) + list(title.iter(qn('w:bookmarkEnd'))):
            bookmark.getparent().remove(bookmark)
        _set_runs(title, [label])
        for element in block:
            sect.addprevious(element)
    document.save(str(out_path))
    return out_path


# --- master document assembly ---------------------------------------------------------------

def add_page_break(document):
    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    br = OxmlElement('w:br')
    br.set(qn('w:type'), 'page')
    run._r.append(br)
    return paragraph


def add_field(paragraph, instruction, placeholder=''):
    run = paragraph.add_run()
    for kind in ('begin', None, 'separate', 'text', 'end'):
        if kind is None:
            node = OxmlElement('w:instrText')
            node.set(qn('xml:space'), 'preserve')
            node.text = f' {instruction} '
        elif kind == 'text':
            node = OxmlElement('w:t')
            node.text = placeholder
        else:
            node = OxmlElement('w:fldChar')
            node.set(qn('w:fldCharType'), kind)
        run._r.append(node)
    return run


def ensure_paragraph_style(document, name, outline_level, size_pt, bold=True, base='Normal'):
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Pt

    styles = document.styles
    if name in [s.name for s in styles]:
        return styles[name]
    style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = styles[base]
    style.font.size = Pt(size_pt)
    style.font.bold = bold
    style.paragraph_format.space_before = Pt(12)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.keep_with_next = True
    ppr = style.element.get_or_add_pPr()
    level = OxmlElement('w:outlineLvl')
    level.set(qn('w:val'), str(outline_level))
    ppr.append(level)
    return style


def repeat_header_row(table):
    trpr = table.rows[0]._tr.get_or_add_trPr()
    node = OxmlElement('w:tblHeader')
    node.set(qn('w:val'), 'true')
    trpr.append(node)


class DocxMerger:
    """Append the body of other DOCX files to a master document, carrying images, hyperlinks,
    styles and list numbering. Embedded objects other than images/hyperlinks are dropped and
    counted so the build report can flag them."""

    def __init__(self, master):
        self.master = master
        self.dropped = []
        self._numbering = master.part.numbering_part.element
        ids = [int(n.get(qn('w:numId'))) for n in self._numbering.findall(qn('w:num'))]
        abstract = [int(n.get(qn('w:abstractNumId'))) for n in self._numbering.findall(qn('w:abstractNum'))]
        self._next_num = max(ids or [0]) + 1
        self._next_abstract = max(abstract or [0]) + 1

    def append(self, path, letterhead=True):
        """Append `path`; the component keeps its page setup in its own section (next page)."""
        source = Document(str(path))
        num_map = self._copy_numbering(source)
        self._copy_styles(source, num_map)
        body = self.master.element.body
        sect = body.find(qn('w:sectPr'))
        children = [c for c in source.element.body if c.tag != qn('w:sectPr')]
        skip = set()
        for index, child in enumerate(children):
            if self._is_component_toc(child):
                skip.add(index)
                if index and re.fullmatch(r'\s*table of contents\s*', _text(children[index - 1]), re.I):
                    skip.add(index - 1)
        for index, child in enumerate(children):
            if index in skip:
                continue
            element = deepcopy(child)
            for inner in element.iter(qn('w:sectPr')):  # keep master page setup
                inner.getparent().remove(inner)
            self._remap_relationships(source, element, Path(path).name)
            self._remap_numbering(element, num_map)
            sect.addprevious(element)
        self._close_section(source, sect, letterhead)

    def close_letterhead_section(self):
        """End the current section with the master's own page setup and letterhead."""
        sect = self.master.element.body.find(qn('w:sectPr'))
        paragraph = OxmlElement('w:p')
        ppr = OxmlElement('w:pPr')
        ppr.append(deepcopy(sect))
        paragraph.append(ppr)
        sect.addprevious(paragraph)

    def _close_section(self, source, master_sect, letterhead):
        """End the merged component with a section break carrying the component's own page
        setup (size, orientation, margins, columns) and the master's header/footer, or no
        header/footer at all (`letterhead=False`, e.g. the cover)."""
        src_sect = source.element.body.find(qn('w:sectPr'))
        if src_sect is None:
            return
        new = deepcopy(src_sect)
        for child in list(new):
            if child.tag in (qn('w:headerReference'), qn('w:footerReference'), qn('w:titlePg'), qn('w:type')):
                new.remove(child)
        if letterhead:
            for reference in reversed([c for c in master_sect if c.tag in (qn('w:headerReference'), qn('w:footerReference'))]):
                new.insert(0, deepcopy(reference))
        paragraph = OxmlElement('w:p')
        ppr = OxmlElement('w:pPr')
        ppr.append(new)
        paragraph.append(ppr)
        master_sect.addprevious(paragraph)

    @staticmethod
    def _is_component_toc(element):
        """A component's own table of contents (TOC styles, TOC field, or TOC content control)."""
        if element.tag == qn('w:sdt'):
            gallery = element.find('.//' + qn('w:docPartGallery'))
            return gallery is not None and 'contents' in (gallery.get(qn('w:val')) or '').lower()
        if element.tag != qn('w:p'):
            return False
        if re.match(r'toc\s*\d|tocheading', _style(element), re.I):
            return True
        return any((node.text or '').strip().upper().startswith('TOC ') for node in element.iter(qn('w:instrText')))

    def _remap_relationships(self, source, element, label):
        for node in list(element.iter()):
            for attr in R_ATTRS:
                rid = node.get(attr)
                if not rid:
                    continue
                rel = source.part.rels.get(rid)
                if rel is None:
                    continue
                if rel.is_external:
                    node.set(attr, self.master.part.relate_to(rel.target_ref, rel.reltype, is_external=True))
                elif rel.reltype == IMAGE_REL:
                    new_rid, _ = self.master.part.get_or_add_image(io.BytesIO(rel.target_part.blob))
                    node.set(attr, new_rid)
                else:
                    self.dropped.append(f'{label}: {rel.reltype.rsplit("/", 1)[-1]}')
                    holder = next((a for a in node.iterancestors() if a.tag in (qn('w:object'), qn('w:drawing'), qn('w:pict'))), None)
                    if holder is not None and holder.getparent() is not None:
                        holder.getparent().remove(holder)
                    break

    def _copy_numbering(self, source):
        try:
            src = source.part.numbering_part.element
        except (KeyError, NotImplementedError):
            return {}
        abstract_map, num_map = {}, {}
        first_num = self._numbering.find(qn('w:num'))
        for abstract in src.findall(qn('w:abstractNum')):
            new = deepcopy(abstract)
            new_id = self._next_abstract
            self._next_abstract += 1
            abstract_map[abstract.get(qn('w:abstractNumId'))] = str(new_id)
            new.set(qn('w:abstractNumId'), str(new_id))
            for link in new.findall(qn('w:numStyleLink')) + new.findall(qn('w:styleLink')):
                new.remove(link)
            (first_num.addprevious(new) if first_num is not None else self._numbering.append(new))
        for num in src.findall(qn('w:num')):
            new = deepcopy(num)
            new_id = self._next_num
            self._next_num += 1
            num_map[num.get(qn('w:numId'))] = str(new_id)
            new.set(qn('w:numId'), str(new_id))
            ref = new.find(qn('w:abstractNumId'))
            if ref is not None and ref.get(qn('w:val')) in abstract_map:
                ref.set(qn('w:val'), abstract_map[ref.get(qn('w:val'))])
            self._numbering.append(new)
        return num_map

    @staticmethod
    def _remap_numbering(element, num_map):
        for node in element.iter(qn('w:numId')):
            value = node.get(qn('w:val'))
            if value in num_map:
                node.set(qn('w:val'), num_map[value])

    def _copy_styles(self, source, num_map):
        existing = {s.style_id for s in self.master.styles}
        src_styles = {s.style_id: s for s in source.styles}
        target = self.master.styles.element
        for style_id, style in src_styles.items():
            if style_id in existing:
                continue
            element = deepcopy(style.element)
            self._remap_numbering(element, num_map)
            target.append(element)
            existing.add(style_id)


def body_text_pages_hint(path):
    """Cheap readability check used by validation."""
    try:
        return len(Document(str(path)).paragraphs)
    except Exception:
        return 0


__all__ = [name for name in dir() if not name.startswith('_') and name not in ('io', 're', 'zipfile')]
