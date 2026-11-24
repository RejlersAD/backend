"""Retired AI proposal authoring entry points."""
from rest_framework.exceptions import APIException


class ProposalAuthoringError(APIException):
    status_code = 410
    default_code = 'local_proposal_authoring_only'
    default_detail = (
        'AI technical-proposal authoring has been retired. '
        'Use the local, source-traceable proposal workspace.'
    )

    def __init__(self, detail=None):
        super().__init__({
            'detail': detail or self.default_detail,
            'code': self.default_code,
        })


def _authoring_context(*args, **kwargs):
    raise ProposalAuthoringError()


def analyze_technical_proposal_sources(*args, **kwargs):
    raise ProposalAuthoringError()


def compose_ai_technical_proposal(*args, **kwargs):
    raise ProposalAuthoringError()


def compose_ai_technical_proposal_section(*args, **kwargs):
    raise ProposalAuthoringError()
