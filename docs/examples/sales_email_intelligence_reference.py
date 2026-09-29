"""Runnable reference using RADAI's real deterministic engine.

Run from backend: python docs/examples/sales_email_intelligence_reference.py input.json
Input is a normalized message envelope, not credentials or a mailbox connection.
No network, database writes, attachment access or automatic opportunity creation.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.sales.email_analysis import analyze_email_conversation  # noqa: E402


def analyze_email(messages, *, selected_message_id, mailbox_address, coverage=None, customer_matcher=None):
    """Keep transport headers separate from body/quoted text.

    messages: normalized Graph or saved-source dictionaries with id, subject,
    body_text, sender_email, sender_name, sent_at, received_at and optional
    _thread_metadata. sent_at must be actual sent evidence, never received time.

    The application supplies its current authorized EmailCustomerMatcher only
    after authenticating the actor. This offline reference has no client access.
    """
    output = analyze_email_conversation(
        messages, selected_message_id=selected_message_id,
        mailbox_address=mailbox_address, coverage=coverage,
    )
    information = {**output['extracted_information'], 'analysis': output['analysis']}
    if customer_matcher is not None:
        information['customer_match'] = customer_matcher.match(information)
    else:
        information['customer_match'] = {
            'version': 1, 'status': 'unavailable', 'method': 'exact_name_v1',
            'detected_name': '', 'needs_review': True,
            'evidence': {'excerpt': '', 'source_ids': []}, 'candidates': [], 'has_more': False,
        }
    return information


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='UTF-8 JSON envelope containing messages, selected_message_id and mailbox_address')
    args = parser.parse_args()
    if args.input.stat().st_size > 8_000_000:
        parser.error('Input exceeds the offline reference file limit.')
    payload = json.loads(args.input.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
        parser.error('Input must be an object containing a messages list.')
    print(json.dumps(analyze_email(
        payload['messages'], selected_message_id=payload.get('selected_message_id'),
        mailbox_address=payload.get('mailbox_address', ''), coverage=payload.get('coverage'),
    ), indent=2, ensure_ascii=False))
