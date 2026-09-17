"""Resolve a source supplier or register only the supplier facts in that source."""

import hashlib
import re
import unicodedata

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import Vendor
from .procurement_lifecycle import ProcurementDeleteConflict


def _name(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip()


def _identity(value):
    return re.sub(r'[^\w]+', ' ', _name(value).casefold()).strip()


def _license(value):
    return re.sub(r'[^\w]+', '', _name(value).casefold())


def _check_vendor(vendor, license_number):
    if vendor.status != 'active':
        raise ProcurementDeleteConflict('The matching supplier is inactive or blacklisted. Review its vendor record before saving this PO.')
    if license_number and vendor.trade_license_number and _license(vendor.trade_license_number) != license_number:
        raise ProcurementDeleteConflict('The supplier name matches a vendor with a different trade license. Review the supplier before saving this PO.')


@transaction.atomic
def resolve_document_vendor(fields, *, user, allow_create, create_missing=True):
    name = _name(fields.get('vendor_name'))
    if not name or len(name) > 300:
        if not create_missing:
            return None, False
        raise ValidationError({'vendor_name': 'Enter the supplier name shown on the uploaded PO.'})
    identity = _identity(name)
    if not any(character.isalnum() for character in identity):
        raise ValidationError({'vendor_name': 'Enter the supplier name shown on the uploaded PO.'})
    license_value = _name(fields.get('vendor_license_no'))
    license_number = _license(license_value)
    # Serialize same-name or same-license imports, including suppliers not yet
    # present in the master. SQLite test writes are already serialized.
    if connection.vendor == 'postgresql':
        keys = {f'document-vendor:name:{identity}'}
        if license_number:
            keys.add(f'document-vendor:license:{license_number}')
        with connection.cursor() as cursor:
            for key in sorted(keys):
                lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])
    matches = [vendor for vendor in Vendor.objects.only('id', 'name', 'vendor_code', 'trade_license_number', 'status')
               if _identity(vendor.name) == identity
               or (license_number and _license(vendor.trade_license_number) == license_number)]
    if len(matches) > 1:
        raise ProcurementDeleteConflict('More than one vendor matches the supplier name or trade license. Select the correct existing supplier.')
    if matches:
        vendor = Vendor.objects.select_for_update().get(pk=matches[0].pk)
        _check_vendor(vendor, license_number)
        return vendor, False
    if not create_missing:
        return None, False
    if not allow_create:
        raise PermissionDenied('Vendor create permission is required to register the supplier from this PO. Select an existing supplier or ask a vendor administrator to register it.')
    email = _name(fields.get('seller_email'))
    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            email = ''
    # A deterministic unique code also protects retries racing on an absent
    # row. No supplier rating, certification, tax ID or contact is invented.
    code = 'VEN-PO-' + hashlib.sha256(identity.encode()).hexdigest()[:32].upper()
    vendor, created = Vendor.objects.get_or_create(vendor_code=code, defaults={
        'name': name, 'trade_license_number': license_value[:100],
        'contact_person': _name(fields.get('seller_contact_person'))[:200],
        'email': email, 'phone': _name(fields.get('seller_phone'))[:50],
        'address': _name(fields.get('seller_address')),
        'created_by': user, 'icv_issuing_authority': '',
        'notes': f"Registered from uploaded purchase order {fields.get('source_po_number') or fields.get('po_number') or ''}.",
    })
    _check_vendor(vendor, license_number)
    if _identity(vendor.name) != identity:
        raise ProcurementDeleteConflict('The generated supplier reference is already in use. Review the vendor register before saving this PO.')
    return vendor, created
