"""Actor-bound retry identity for reviewed manual opportunity registration."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Model
from rest_framework.exceptions import APIException


class RegistrationConflict(APIException):
    status_code = 409
    default_detail = 'This registration was already saved with different details. Open the existing opportunity.'
    default_code = 'registration_already_saved'


def registration_fingerprint(validated_data):
    def normalize(value):
        if isinstance(value, Model):
            return str(value.pk)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    body = json.dumps(normalize(validated_data), cls=DjangoJSONEncoder,
                      sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()
