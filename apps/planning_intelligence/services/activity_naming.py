"""Propose concise, traceable names without treating every clause as work.

These are draft naming conventions, not an ISO activity-code specification.
The original requirement must remain attached to the activity: a short name
cannot replace its conditions, acceptance criteria, or contractual wording.
"""
from __future__ import annotations

import re
import hashlib


NAMING_VERSION = 'activity-naming-v1'
_MODAL = re.compile(r'\b(?:shall|must|is required to|are required to)\b', re.I)
_ACTOR = r'(?:(?:the\s+)?(?:FEED\s+)?(?:CONTRACTOR|CONTRATOR|CONSULTANT|BIDDER)(?:\s*/\s*BIDDER)?(?:[’\x27]s)?\s+)'
_VERBS = (
    'prepare|develop|produce|conduct|perform|undertake|design|review|verify|'
    'check|revalidate|collect|submit|update|identify|provide|study|gather|'
    'compile|maintain|augment|mobilize|facilitate|arrange|obtain|follow|'
    'configure|include|finalize|agree|allow|monitor|control|make|liaise|'
    'propose|refer|familiarise|familiarize|extend|achieve|cover|indicate'
)
_PASSIVE = {
    'prepared': 'Prepare', 'developed': 'Develop', 'produced': 'Produce',
    'conducted': 'Conduct', 'performed': 'Perform', 'reviewed': 'Review',
    'verified': 'Verify', 'checked': 'Check', 'collected': 'Collect',
    'submitted': 'Submit', 'updated': 'Update', 'identified': 'Identify',
    'provided': 'Provide', 'studied': 'Study', 'compiled': 'Compile',
    'maintained': 'Maintain', 'configured': 'Configure', 'included': 'Review',
    'finalized': 'Finalize', 'agreed': 'Agree', 'arranged': 'Arrange',
    'obtained': 'Obtain', 'implemented': 'Review', 'used': 'Confirm use of',
    'considered': 'Review', 'organized': 'Organize', 'approved': 'Obtain approval for',
}
_DANGLING = re.compile(r'\b(?:a|an|the|and|or|of|for|to|in|with|by|all|any|'
                       r'existing|new|required|necessary|following|including|within)\s*$', re.I)


def _clean(value):
    value = str(value or '').replace('\u00ad', '')
    value = re.sub(r'\(cid:\d+\)|[•▪●]', ' ', value)
    value = re.sub(r'(?<=[a-zA-Z])(?=shall\b)', ' ', value)
    value = re.sub(r'\bprovide(?=electrical)', 'provide ', value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip()


def _clause(statement, excerpt):
    """Recover only the sentence containing the selected requirement modal."""
    value = _clean(statement)
    context = _clean(re.sub(r'\(cid:\d+\)|[•▪●]', '. ', str(excerpt or '')))
    index = context.casefold().find(value.casefold()) if value else -1
    if index < 0:
        return value
    modal = _MODAL.search(value)
    anchor = index + (modal.start() if modal else 0)
    # Numbered list markers and PDF bullets do not become activity subjects.
    boundaries = list(re.finditer(r'[.!?]\s+(?=[A-Z(])', context[:anchor]))
    start = boundaries[-1].end() if boundaries else index
    if start < index and re.search(r'\b(?:page|doc\.? no|scope of work)\b', context[start:index], re.I):
        start = index
    # If an excerpt begins mid-sentence, retain the available preceding noun
    # phrase (e.g. "... a Deliverables Register ... dates shall be developed").
    if not boundaries and index and not re.search(r'\b(?:page|doc\.? no)\b', context[:index], re.I):
        start = 0
    # Multiple obligations in one sentence belong to their own selected modal.
    preceding_modal = list(_MODAL.finditer(context[start:anchor]))
    if preceding_modal:
        own_prefix = context[index:anchor]
        actor = re.search(_ACTOR, own_prefix, re.I)
        if actor:
            start = index + actor.start()
        else:
            joins = list(re.finditer(r'\b(?:and|,|;)\s*', context[start:anchor], re.I))
            if joins:
                start += joins[-1].end()
    end = re.search(r'[.!?](?=\s+(?:[A-Z(]|\d+[.)])|$)', context[anchor:])
    stop = anchor + end.start() if end else len(context)
    result = context[start:stop].strip(' .;:')
    if re.match(r'^(?:FEED\s+)?(?:CONTRACTOR|CONSULTANT|BIDDER)\s+shall\b', value, re.I):
        own_actor = result.casefold().find(value.split(' shall', 1)[0].casefold())
        if own_actor > 0 and not re.search(r'\b(?:if|in case|prior to|upon|towards)\b', result[:own_actor], re.I):
            result = result[own_actor:]
    # Starting mid-line is safer than including unrelated PDF page furniture.
    return re.sub(r'^\d+[.)]\s*', '', result)


def _source_context(statement, excerpt, source_text, locator):
    """Use a bounded, verified locator window, never search another document."""
    if not source_text or not isinstance(locator, dict):
        return excerpt
    try:
        start, end = int(locator['character_start']), int(locator['character_end'])
    except (KeyError, ValueError, TypeError):
        return excerpt
    if start < 0 or end <= start or end > len(source_text):
        return excerpt
    digest = locator.get('extracted_text_sha256')
    if digest and hashlib.sha256(source_text.encode('utf-8')).hexdigest() != digest:
        return excerpt
    selected = _clean(source_text[start:end])
    if _clean(statement).casefold() != selected.casefold():
        return excerpt
    return source_text[max(0, start - 200):min(len(source_text), end + 500)]


def _sentence_case(value):
    words = []
    for word in value.split():
        letters = re.sub(r'[^A-Za-z]', '', word)
        acronym = re.fullmatch(r'[^A-Za-z]*[A-Z][A-Z0-9&/.-]*(?:[sS]|[’\x27][sS])?[^A-Za-z]*', word)
        words.append(word if acronym else word.lower())
    text = ' '.join(words)
    return text[:1].upper() + text[1:]


def _compact(value, limit=108):
    value = _clean(value).strip(' ,.;:–—-')
    value = re.sub(r'^(?:the|a|an)\s+', '', value, flags=re.I)
    value = re.split(r'\b(?:shall|AllA\s+plla|All parties consent)\b|\s+[.]\s*', value, maxsplit=1, flags=re.I)[0].strip()
    value = re.sub(r'\b(?:as per|in accordance with|in conformity with|to ensure|'
                   r'presenting|which|who)\b.*$', '', value, flags=re.I).strip()
    value = re.sub(r'\s*\.\s*\d+(?:\.\d+)*\s*$', '', value)
    if len(value) > limit:
        value = value[:limit + 1].rsplit(' ', 1)[0]
    while _DANGLING.search(value):
        value = _DANGLING.sub('', value).rstrip(' ,.;:–—-')
    return value


def _condition(clause):
    leading = re.match(r'\s*((?:if|in case|in the event)\b[^,;]+),\s*', clause, re.I)
    if leading:
        return leading.group(1).strip()
    trailing = re.search(r'\b((?:if|unless|provided that|only when)\b[^.;)]*)', clause, re.I)
    if trailing:
        return trailing.group(1).strip(' ,)')
    optional = re.search(r'\b(as required|as applicable|where applicable|when required)\b', clause, re.I)
    return optional.group(1) if optional else ''


def _result(title, basis):
    return {'title': _sentence_case(title), 'naming_basis': basis, 'needs_review': True}


def _with_condition(title, condition):
    if not condition or condition.casefold() in title.casefold():
        return _compact(title, 140)
    # Keep a complete short qualifier; never silently turn conditional work into
    # unconditional work when the excerpt cuts off its condition.
    qualifier = _clean(condition).strip(' ,.;:')
    if len(qualifier) > 64 or _MODAL.search(qualifier):
        qualifier = 'subject to source condition'
    if not qualifier or qualifier.lower() in {'if', 'unless', 'provided that', 'only when'}:
        qualifier = 'condition requires clarification'
    core = _compact(title, max(50, 137 - len(qualifier)))
    return f'{core} ({qualifier})'


def proposed_activity_name(statement, *, source_excerpt='', source_locator=None, source_text=''):
    """Return a reviewable verb/subject title; never call a model or change scope.

    A verified ``source_locator`` can recover the selected clause from optional
    ``source_text``. It never influences the activity identity code.
    All results need review, including complete source-supported obligations.
    """
    source_excerpt = _source_context(statement, source_excerpt, source_text, source_locator)
    clause = _clause(statement, source_excerpt)
    value = clause.casefold()
    condition = _condition(clause)

    if re.search(r'\b(?:use of the word|word\s+[“\"\x27]?shall).*\bmandatory', value):
        return _result('Confirm mandatory requirement terminology', 'requirement_review')
    if 'order of precedence' in value:
        return _result('Review document order of precedence', 'requirement_review')
    if re.search(r'conflict.*(?:notice|responsibility|bring)', value):
        return _result('Notify COMPANY in writing of document conflicts, if any', 'requirement_review')
    if re.search(r'company\s+interpretation', value):
        return _result('Confirm COMPANY interpretation of document conflicts', 'requirement_review')
    if re.search(r'\b(?:dgs|ages)\b.*shall not be (?:altered|modified)', value):
        return _result(_with_condition('Confirm no changes to COMPANY DGS / AGES except through addendum'
                       if 'addendum' in value else 'Confirm no changes to COMPANY DGS / AGES', condition),
                       'requirement_review')
    if re.search(r'\bnot be (?:re-used|reused)', value):
        return _result(_with_condition('Confirm no reuse of demolished components'
                       if 'demolished components' in _clean(source_excerpt or clause).casefold() else 'Review component reuse prohibition',
                       condition), 'requirement_review')
    if re.search(r'\bnot be sub[- ]contracted', value):
        return _result(_with_condition('Confirm prohibition on subcontracting source-specified work', condition), 'requirement_review')
    if re.search(r'\bnot proceed\b.*\bift\b', value):
        return _result('Confirm no IFT release while IFA comments remain open', 'requirement_review')
    if re.search(r'\bmaintain and achieve a high level of performance\b', value):
        return _result('Review project performance requirements', 'requirement_review')
    if re.search(r'\bprovide all services\b', value):
        return _result('Review required service scope', 'requirement_review')
    if re.search(r'\b(?:include but not be limited to|following in package)\b', value):
        return _result('Review source-specified scope inclusions', 'requirement_review')
    if re.search(r'\bthis shall be reviewed and revised\b', value) and 'deliverable list' in _clean(source_excerpt).casefold():
        return _result('Review and revise FEED deliverables list', 'requirement_review')

    modal = _MODAL.search(clause)
    if not modal:
        subject = _compact(re.sub(r'^\s*(?:and|or|but)\s+', '', clause, flags=re.I), 106)
        return _result(f'Clarify requirement: {subject or "source statement"}', 'source_fragment_review')
    subject = clause[:modal.start()].strip(' ,;:')
    action = clause[modal.end():].strip()
    nested_prohibition = re.search(r'\b(?:shall|must)\s+not\s+(.+)', action, re.I)
    if nested_prohibition:
        restriction = _compact(nested_prohibition.group(1))
        return _result(_with_condition(f'Review restriction: do not {restriction}', condition), 'requirement_review')
    if re.match(r'not\b|never\b', action, re.I):
        action = re.sub(r'^not\s+', 'do not ', action, flags=re.I)
        return _result(_with_condition(f'Review restriction: {_compact(action)}', condition), 'requirement_review')
    action = re.sub(r'^(?:(?:also|further|accordingly|generally|fully)\s+)+', '', action, flags=re.I)
    action = re.sub(r'^be responsible (?:to|for)\s+', '', action, flags=re.I)
    for gerund, verb in (('developing', 'develop'), ('identifying', 'identify'), ('verifying', 'verify'),
                         ('monitoring', 'monitor'), ('obtaining', 'obtain')):
        action = re.sub(r'^' + gerund + r'\b', verb, action, flags=re.I)
    action = re.sub(r'^carry\s*out\b', 'perform', action, flags=re.I)
    action = re.sub(r'^ensure(?:\s+that|\s+to)?\s+', 'verify ', action, flags=re.I)
    direct = re.match(r'(' + _VERBS + r')\s+(.+)', action, re.I)
    if direct:
        verb, obj = direct.groups()
        obj = re.split(r'\s+(?:if|unless|provided that|as required|as applicable)\b|\s*\((?:if|as)\b', obj, maxsplit=1, flags=re.I)[0]
        obj = _compact(obj)
        # A dangling object or generic heading is not a resolved work package.
        if not obj or obj.casefold() in {'following', 'this', 'these', 'it', 'be', 'all'}:
            return _result('Clarify scope of requirement', 'source_fragment_review')
        title = _with_condition(f'{verb} {obj}', condition)
        basis = 'requirement_review' if verb.lower() in {'allow', 'follow', 'include', 'cover', 'refer'} else 'source_obligation'
        return _result(title, basis)

    passive = re.match(r'be\s+(' + '|'.join(_PASSIVE) + r')\b(.*)', action, re.I)
    if passive and subject:
        verb, tail = passive.groups()
        subject = re.sub(r'^(?:it is emphasized that|note\s*:)\s*', '', subject, flags=re.I)
        subject = re.sub(r'^(?:if\b[^,]+,\s*)', '', subject, flags=re.I)
        subject = _compact(subject, 98)
        if subject.casefold() not in {'this', 'it', 'these', 'and', 'which'}:
            # The title names the work; actors, timing and acceptance details
            # remain in the unchanged source statement and attached evidence.
            if re.search(r'\bonly by\b', tail, re.I):
                return _result(_with_condition(f'Review execution restriction for {subject}', condition), 'requirement_review')
            return _result(_with_condition(f'{_PASSIVE[verb.lower()]} {subject}', condition), 'source_obligation')

    subject = re.sub(r'^' + _ACTOR, '', subject, flags=re.I)
    if not subject or subject.casefold() in {'this', 'it', 'these', 'and', 'which', 'scope', 'programme'} or re.search(_ACTOR, subject + ' ', re.I):
        subject = action
    subject = re.sub(r'^(?:be|remain)\s+', '', subject, flags=re.I)
    subject = _compact(subject, 100)
    return _result(_with_condition(f'Review requirement: {subject or "source scope"}', condition), 'requirement_review')
