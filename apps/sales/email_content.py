"""Project untrusted email HTML into text and a small, inert display vocabulary."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from lxml import etree, html


MAX_HTML_CHARACTERS = 1_000_000
MAX_CONTENT_NODES = 5000
MAX_CONTENT_DEPTH = 40
MAX_TABLE_SPAN = 50

ALLOWED_TYPES = {
    'p', 'div', 'br', 'strong', 'em', 'u', 's', 'ul', 'ol', 'li',
    'blockquote', 'pre', 'code', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'caption', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'hr', 'a',
}
ALIASES = {'b': 'strong', 'i': 'em'}
DROP_SUBTREES = {
    'script', 'style', 'head', 'title', 'svg', 'math', 'iframe', 'object',
    'applet', 'template', 'noscript', 'audio', 'video', 'canvas', 'frameset',
    'form', 'button', 'select', 'textarea',
}
DROP_ELEMENTS = {'img', 'image', 'input', 'embed', 'link', 'meta', 'base', 'source', 'track', 'frame'}
LINE_BREAK_TYPES = {
    'p', 'div', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'table', 'caption', 'thead', 'tbody', 'tfoot', 'tr', 'hr',
}


class _TextBodyParser(HTMLParser):
    """Keep a full text fallback even when structured projection is too complex."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.append(tag)
            return
        if tag in DROP_SUBTREES:
            self.suppressed.append(tag)
        elif tag == 'br' or tag in LINE_BREAK_TYPES:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in {'td', 'th'}:
            self.parts.append('\t')
        elif tag in LINE_BREAK_TYPES:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)

    def text(self):
        text = ''.join(self.parts).replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'[\t ]+\n', '\n', text)
        return re.sub(r'\n[\t ]*\n(?:[\t ]*\n)+', '\n\n', text).strip()


def _safe_link(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    # Reject control/whitespace obfuscation instead of normalizing it into a URL.
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {'http', 'https'}:
            if not parsed.hostname or parsed.username or parsed.password or '\\' in value:
                return None
            parsed.port  # Reject malformed port syntax.
            return value
        if parsed.scheme.lower() == 'mailto':
            if parsed.netloc or not parsed.path or '\\' in value or re.search(r'%0[ad]', value, re.IGNORECASE):
                return None
            if not re.fullmatch(r'[^<>@,;:?/#]+@[^<>@,;:?/#]+', parsed.path):
                return None
            return value
    except ValueError:
        pass
    return None


class _UnsupportedStructure(ValueError):
    pass


class _BodyProjection:
    def __init__(self):
        self.count = 0

    def _count_node(self):
        self.count += 1
        if self.count > MAX_CONTENT_NODES:
            raise _UnsupportedStructure()

    def text(self, value):
        if not value:
            return []
        self._count_node()
        return [{'type': 'text', 'text': value}]

    @staticmethod
    def _without_whitespace(nodes):
        return [node for node in nodes if node['type'] != 'text' or node['text'].strip()]

    def _table_children(self, tag, children):
        children = self._without_whitespace(children)
        if tag == 'table':
            grouped = []
            rows = []
            for child in children:
                if child['type'] == 'tr':
                    rows.append(child)
                    continue
                if child['type'] not in {'caption', 'thead', 'tbody', 'tfoot'}:
                    raise _UnsupportedStructure()
                if rows:
                    self._count_node()
                    grouped.append({'type': 'tbody', 'children': rows})
                    rows = []
                grouped.append(child)
            if rows:
                self._count_node()
                grouped.append({'type': 'tbody', 'children': rows})
            return grouped
        allowed = {'tr'} if tag in {'thead', 'tbody', 'tfoot'} else {'th', 'td'}
        if any(child['type'] not in allowed for child in children):
            raise _UnsupportedStructure()
        return children

    def element(self, element, depth=0):
        if depth > MAX_CONTENT_DEPTH:
            raise _UnsupportedStructure()
        self._count_node()
        if not isinstance(element.tag, str):
            return []
        tag = element.tag.lower()
        if tag in DROP_SUBTREES or tag in DROP_ELEMENTS:
            return []
        children = self.text(element.text)
        for child in element:
            children.extend(self.element(child, depth + 1))
            children.extend(self.text(child.tail))
        tag = ALIASES.get(tag, tag)
        if tag not in ALLOWED_TYPES:
            return children
        if tag in {'caption', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th'}:
            parent = element.getparent()
            parent_tag = parent.tag.lower() if parent is not None and isinstance(parent.tag, str) else ''
            required_parents = {
                'caption': {'table'}, 'thead': {'table'}, 'tbody': {'table'}, 'tfoot': {'table'},
                'tr': {'table', 'thead', 'tbody', 'tfoot'}, 'td': {'tr'}, 'th': {'tr'},
            }
            if parent_tag not in required_parents[tag]:
                raise _UnsupportedStructure()
        if tag in {'table', 'thead', 'tbody', 'tfoot', 'tr'}:
            children = self._table_children(tag, children)
        node = {'type': tag, 'children': [] if tag in {'br', 'hr'} else children}
        if tag == 'a':
            href = _safe_link(element.get('href'))
            if href is None:
                return children
            node['href'] = href
        if tag in {'td', 'th'}:
            for attribute, output_name in (('colspan', 'col_span'), ('rowspan', 'row_span')):
                value = element.get(attribute, '')
                if re.fullmatch(r'[1-9]\d?', value) and int(value) <= MAX_TABLE_SPAN:
                    node[output_name] = int(value)
        return [node]


def project_email_body(content, content_type):
    """Return inert structure plus text; never return original markup or assets."""
    if str(content_type).lower() != 'html':
        return {'body_text': content, 'body_content': None}
    fallback = _TextBodyParser()
    fallback.feed(content)
    fallback.close()
    result = {'body_text': fallback.text(), 'body_content': None}
    if not content.strip():
        result['body_content'] = []
        return result
    if len(content) > MAX_HTML_CHARACTERS:
        return result
    try:
        parser = html.HTMLParser(
            encoding='utf-8', no_network=True, recover=True,
            remove_comments=True, huge_tree=False,
        )
        document = html.document_fromstring(content.encode('utf-8'), parser=parser)
        result['body_content'] = _BodyProjection().element(document)
    except (_UnsupportedStructure, etree.LxmlError, ValueError, UnicodeError, RecursionError):
        # Keep the complete available text instead of fabricating or truncating a table.
        pass
    return result
