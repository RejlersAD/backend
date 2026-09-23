"""Readable Teams previews of saved rich text, without renderer dependencies."""

import html
from html.parser import HTMLParser
import re


_BLOCK_TAGS = {
    'address', 'article', 'blockquote', 'dd', 'div', 'dl', 'dt', 'fieldset',
    'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section',
    'table', 'tbody', 'tfoot', 'thead', 'tr', 'ul',
}
_HIDDEN_TAGS = {'script', 'style', 'head', 'iframe', 'object', 'embed', 'svg', 'math', 'template', 'noscript'}
_VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_HTML_TAGS = _BLOCK_TAGS | _HIDDEN_TAGS | _VOID_TAGS | {
    'a', 'abbr', 'acronym', 'b', 'bdi', 'bdo', 'big', 'body', 'button', 'caption',
    'center', 'cite', 'code', 'colgroup', 'data', 'del', 'details', 'dfn', 'dialog',
    'em', 'font', 'form', 'html', 'i', 'ins', 'kbd', 'label', 'legend', 'mark',
    'menu', 'meter', 'optgroup', 'option', 'output', 'picture', 'progress', 'q',
    'rp', 'rt', 'ruby', 's', 'samp', 'select', 'small', 'span', 'strike', 'strong',
    'sub', 'summary', 'sup', 'td', 'textarea', 'th', 'time', 'title', 'tt', 'u',
    'var', 'video', 'audio',
}
_PREVIEW_SUFFIX = '… (Open Request for full text)'


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.has_markup = False

    def _hidden(self):
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        self.has_markup = True
        attrs = dict(attrs)
        hidden = bool(
            self._hidden() or tag in _HIDDEN_TAGS or 'hidden' in attrs
            or str(attrs.get('aria-hidden', '')).lower() == 'true'
            or re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b',
                         attrs.get('style') or '', re.I)
        )
        if tag not in _VOID_TAGS:
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag == 'li':
            self.parts.append(('\n• ', True))
        elif tag in _BLOCK_TAGS or tag == 'br':
            self.parts.append(('\n', True))

    def handle_endtag(self, tag):
        hidden = self._hidden()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                hidden = self.stack[index][1]
                del self.stack[index:]
                break
        if hidden:
            return
        if tag in ('td', 'th'):
            self.parts.append(('\t', True))
        elif tag in _BLOCK_TAGS:
            self.parts.append(('\n', True))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self._hidden():
            self.parts.append((data, False))


def teams_plain_text(value, *, default='Not specified', max_length=1500):
    """Extract visible prose; shorten only its outbound notification preview.

    Legacy editor values can contain HTML escaped multiple times. Decode before
    parsing so attributes and hidden content cannot leak into the final text.
    This returns text, not safe HTML: Flow must retain its existing HTML escaping.
    """
    source = '' if value is None else str(value)
    for _ in range(3):
        decoded = html.unescape(source)
        if decoded == source:
            break
        source = decoded
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    source = re.sub(r'<!--.*?(?:-->|$)', '', source, flags=re.S)
    # Rich-text paste can include custom wrappers. Attribute-bearing wrappers
    # are markup, while plain engineering notation such as <DN50> is text.
    wrapper_tags = set(re.findall(
        r'<([a-zA-Z][\w:-]*)\s+[^<>]*?(?:[\w:-]+\s*=)', source,
    ))
    # Office pastes also use attribute-free namespace wrappers such as <o:p>.
    wrapper_tags.update(re.findall(r'</?((?:o|w|v|m):[\w-]+)(?=[\s/>])', source, re.I))
    tag_names = '|'.join(re.escape(tag) for tag in sorted(_HTML_TAGS | wrapper_tags))
    # A clipped attribute-bearing tag must not leak its style into the message.
    source = re.sub(r'<(?:' + tag_names + r')\s+[^<>]*=[^<>]*\Z', '', source, flags=re.I)
    source = re.sub(r'<(?!(?:/?(?:' + tag_names + r')(?=[\s/>])|!doctype\b))',
                    '&lt;', source, flags=re.I)
    parser = _VisibleText()
    parser.feed(source)
    parser.close()
    # Only separators produced by HTML elements represent layout. Indentation
    # and tabs within HTML source are spaces, not extra rows or table columns.
    plain = ''.join(
        text if layout else re.sub(r'\s+', ' ', text) if parser.has_markup else text.replace('\t', ' ')
        for text, layout in parser.parts
    ).replace('\xa0', ' ').replace('\u200b', '').replace('\ufeff', '')
    lines = []
    for line in plain.splitlines():
        line = re.sub(r'[^\S\t\n]+', ' ', line).strip()
        line = re.sub(r'\s*\t\s*', ' | ', line)
        if line and line != '•':
            lines.append(line)
    text = '\n'.join(lines) or default
    if max_length is not None and len(text) > max_length:
        available = max_length - len(_PREVIEW_SUFFIX)
        if available <= 0:
            return '…'[:max(0, max_length)]
        preview = text[:available].rstrip()
        boundary = max(preview.rfind(' '), preview.rfind('\n'))
        if boundary >= available * .7:
            preview = preview[:boundary].rstrip()
        text = preview + _PREVIEW_SUFFIX
    return text


def teams_card_text(value):
    """Keep user punctuation literal in Adaptive Card Markdown only."""
    escaped = re.sub(r'([\\`*_\[\]])', r'\\\1', value)
    return escaped.replace('\n', '\n\n')
