"""Read bounded Outlook properties without attachments, RTF, links or rendering.

MSG variable-length property streams follow Microsoft's MS-OXMSG format. The
container parser is olefile; property selection is deliberately top-level only.
All returned content remains untrusted evidence for a reviewable suggestion.
"""
import codecs
from html.parser import HTMLParser
import struct

import olefile

MAX_SOURCE = 32 * 1024 * 1024
MAX_STREAM = 1024 * 1024
MAX_TEXT = 20_000
MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')


class _PassiveText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.length, self.skip, self.nodes = [], 0, [], 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 30000:
            raise ValueError('HTML structure limit')
        if self.skip or tag in {'script', 'style', 'svg', 'iframe', 'object', 'template'}:
            if tag not in {'br', 'img', 'hr', 'meta', 'link', 'input'}:
                self.skip.append(tag)
        elif tag in {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.handle_data('\n')

    def handle_endtag(self, tag):
        if self.skip and tag in self.skip:
            del self.skip[len(self.skip) - 1 - self.skip[::-1].index(tag):]
        elif not self.skip and tag in {'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.handle_data('\n')

    def handle_data(self, value):
        if not self.skip and self.length < MAX_TEXT:
            part = value[:MAX_TEXT - self.length]
            self.parts.append(part)
            self.length += len(part)


def _read(archive, name, limit=MAX_STREAM):
    size = archive.get_size(name)
    if size > MAX_STREAM:
        raise ValueError('MSG property limit')
    with archive.openstream(name) as stream:
        value = stream.read(min(limit, size))
    if len(value) != min(limit, size):
        raise ValueError('Truncated MSG property')
    return value, len(value) == size


def _decode(value, encoding, complete=True):
    decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
    return decoder.decode(value, final=complete).rstrip('\x00').replace('\x00', ' ')[:MAX_TEXT]


def extract_msg_text(source, size):
    """Return (text, diagnostic); never infer incoming/outgoing from an address."""
    position = source.tell()
    try:
        if size > MAX_SOURCE:
            return '', 'source_too_large'
        source.seek(0)
        header = source.read(512)
        if len(header) != 512 or header[:8] != MAGIC:
            return '', 'invalid_document'
        sector_shift = struct.unpack_from('<H', header, 30)[0]
        major = struct.unpack_from('<H', header, 26)[0]
        if (major, sector_shift) not in {(3, 9), (4, 12)}:
            return '', 'invalid_document'
        sector_size = 1 << sector_shift
        if size % sector_size or struct.unpack_from('<I', header, 44)[0] > size // sector_size:
            return '', 'invalid_document'
        source.seek(0)
        with olefile.OleFileIO(source, raise_defects=olefile.DEFECT_INCORRECT) as archive:
            if len(archive.direntries) > 4096:
                return '', 'structure_limit'
            paths = archive.listdir(streams=True, storages=False)
            if len(paths) > 4096 or any(len(path) > 64 for path in paths):
                return '', 'structure_limit'
            roots = {path[0] for path in paths if len(path) == 1}
            if '__properties_version1.0' not in roots:
                return '', 'invalid_document'
            properties, complete = _read(archive, '__properties_version1.0', 64 * 1024)
            if not complete or len(properties) < 32 or (len(properties) - 32) % 16:
                return '', 'invalid_document'
            codepages = {}
            for offset in range(32, len(properties), 16):
                tag, = struct.unpack_from('<I', properties, offset)
                if tag in {0x3FFD0003, 0x3FDE0003}:
                    codepages[tag] = struct.unpack_from('<I', properties, offset + 8)[0]

            def encoding(html=False):
                page = codepages.get(0x3FDE0003 if html else 0x3FFD0003)
                if page is None:
                    page = codepages.get(0x3FFD0003 if html else 0x3FDE0003)
                if page is None:
                    return 'ascii'  # No invented legacy code page for non-ASCII bytes.
                if page == 65001:
                    return 'utf-8'
                if page == 1200:
                    return 'utf-16-le'
                if page in {874, 932, 936, 949, 950, 1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257, 1258}:
                    return f'cp{page}'
                raise LookupError('Unsupported MSG code page')

            def text_property(tag):
                for suffix, codec in (('001F', 'utf-16-le'), ('001E', None)):
                    name = f'__substg1.0_{tag}{suffix}'
                    if name in roots:
                        data, full = _read(archive, name, MAX_TEXT * 4)
                        return _decode(data, codec or encoding(), full)
                return ''

            message_class = text_property('001A')
            if message_class and not message_class.startswith('IPM.Note'):
                return '', 'unsupported_message_class'
            parts = []
            for label, tag in [('Subject', '0037'), ('From', '0C1A'), ('Sender', '0C1F'), ('To', '0E04'), ('Cc', '0E03')]:
                value = text_property(tag)
                if value:
                    parts.append(f'{label}: {value[:2000]}')
            body = text_property('1000')
            if not body and '__substg1.0_10130102' in roots:
                data, full = _read(archive, '__substg1.0_10130102', MAX_TEXT * 4)
                parser = _PassiveText()
                parser.feed(_decode(data, encoding(html=True), full))
                parser.close()
                body = ''.join(parser.parts).strip()
            if body:
                parts.append('Body:\n' + body)
            # RTF and nested attachments are deliberately not decoded. Useful
            # subject/header evidence can still yield a suggestion on its own.
            return '\n'.join(parts)[:MAX_TEXT], '' if body else 'msg_body_unavailable'
    except (OSError, ValueError, LookupError, IndexError, TypeError, struct.error, RecursionError):
        return '', 'invalid_document'
    finally:
        source.seek(position)
