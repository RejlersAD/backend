"""
Procurement Management Serializers
API data serialization for procurement workflows
"""

import copy
from uuid import uuid4

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt, PODocument, PROCUREMENT_CATEGORIES
from .services.purchase_order_numbering import PurchaseOrderNumberService
from .services.purchase_order_approvals import (
    can_approve as can_approve_purchase_order,
    _active_entries,
    _entry_level,
    normalize_assignments,
    notify_assigned_approvers,
    notify_purchase_order_created,
)
from .services.employee_display import (
    employee_display_name,
    employee_display_names,
    name_only,
    normalize_ceo_workflow,
)
from .services.receipt_numbering import ReceiptNumberService
from .services.requisition_status import canonicalize_pr_status
from .services.requisition_source_documents import (
    SIGNED_PR_TYPE, refreshed_requisition_attachments, requisition_original_source,
)
from .services.project_relationships import (
    resolve_enterprise_project_by_code,
    resolve_order_enterprise_project,
    resolve_requisition_enterprise_project,
)
from .services.requisition_validation import (
    line_items_total,
    normalize_line_items,
    validate_attachments,
)
from .services.requisition_workflow import RequisitionWorkflowService, notify_requisition_approver_changes
from .services.approval_integrity import stage_signature_issue, purchase_order_signature_issue, protect_approval_route
from .services.procurement_vat import apply_confirmed_input, CONFIRMED_BASES


PR_SERVER_CONTROLLED_FIELDS = {
    'issued_by',
    'status',
    'current_approval_step',
    'pm_name',
    'pm_signature',
    'pm_approval_status',
    'pm_approved_at',
    'eng_manager_name',
    'eng_manager_signature',
    'eng_manager_approval_status',
    'eng_manager_approved_at',
    'manager_projects_name',
    'manager_projects_signature',
    'manager_projects_approval_status',
    'manager_projects_approved_at',
    'vp_op_name',
    'vp_op_signature',
    'vp_op_approval_status',
    'vp_op_approved_at',
    'requested_by',
    'approved_by',
    'approved_at',
    'rejection_reason',
    'approval_hierarchy',
}


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    rating_display = serializers.CharField(source='get_rating_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    remove_logo = serializers.BooleanField(write_only=True, required=False, default=False)
    icv_expiry_date = serializers.DateField(
        required=False,
        allow_null=True,
        input_formats=['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'],
    )
    
    class Meta:
        model = Vendor
        fields = [
            # Core Information
            'id', 'vendor_code', 'name', 'logo', 'logo_url', 'remove_logo',
            'business_type', 'specialization', 'website', 'city',
            'contact_person', 'email', 'phone', 'address', 'country',
            
            # Financial & Legal
            'tax_id', 'trade_license_number', 'vat_number', 'payment_terms', 'credit_limit',
            
            # Status & Performance
            'status', 'status_display', 'rating', 'rating_display', 'performance_notes',
            
            # Categories & Services
            'categories',
            
            # Oil & Gas Specific
            'certifications', 'quality_standards', 'approved_materials', 'inspection_authority',
            
            # HSE Compliance
            'hse_rating', 'safety_certifications', 'last_audit_date', 'audit_status',
            
            # ICV (In-Country Value) - Abu Dhabi Market
            'icv_percentage', 'icv_certificate', 'icv_expiry_date', 'icv_issuing_authority', 'is_icv_certified',
            
            # ADNOC & Industry
            'adnoc_approved', 'vendor_tenure_years',
            
            # Metadata
            'created_by', 'created_by_name', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'vendor_code': {'required': False, 'allow_blank': True}}
    
    def create(self, validated_data):
        validated_data.pop('remove_logo', None)
        if not validated_data.get('vendor_code', '').strip():
            from uuid import uuid4
            validated_data['vendor_code'] = f'VEN-{uuid4().hex[:16].upper()}'
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if 'vendor_code' in validated_data and not validated_data['vendor_code'].strip():
            validated_data.pop('vendor_code')
        remove_logo = validated_data.pop('remove_logo', False)
        if remove_logo and instance.logo:
            instance.logo.delete(save=False)
            instance.logo = None
        return super().update(instance, validated_data)

    def validate_logo(self, value):
        if value and value.size > 2 * 1024 * 1024:
            raise serializers.ValidationError('Supplier logos must be 2 MB or smaller.')
        return value


class VendorICVSerializer(serializers.ModelSerializer):
    """Restricted serializer used when procurement records a missing ICV value."""

    icv_percentage = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
    )

    class Meta:
        model = Vendor
        fields = ['icv_percentage', 'icv_expiry_date', 'is_icv_certified']

    def validate(self, attrs):
        attrs['is_icv_certified'] = True
        return attrs


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    """
    Serializer for Purchase Requisition
    Aligned with RAD-OM-PRC-0001 FRM -1 Rev 0 template (23 fields)
    """
    
    # Display fields
    requisition_type_display = serializers.CharField(source='get_requisition_type_display', read_only=True)
    status_display = serializers.SerializerMethodField()
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    pm_approval_status_display = serializers.CharField(source='get_pm_approval_status_display', read_only=True)
    vp_op_approval_status_display = serializers.CharField(source='get_vp_op_approval_status_display', read_only=True)
    
    # User relationship fields
    issued_by_name = serializers.SerializerMethodField()
    pm_name_display = serializers.SerializerMethodField()
    eng_manager_name_display = serializers.SerializerMethodField()
    manager_projects_name_display = serializers.SerializerMethodField()
    vp_op_name_display = serializers.SerializerMethodField()
    
    # Vendor relationship fields
    vendor_details = VendorSerializer(source='vendor', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True, allow_null=True)
    
    # Legacy fields
    requester_name = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    category_display = serializers.SerializerMethodField()
    enterprise_project_code = serializers.CharField(
        source='enterprise_project.code', read_only=True, allow_null=True,
    )
    enterprise_project_name = serializers.CharField(
        source='enterprise_project.name', read_only=True, allow_null=True,
    )
    
    # Original PR links are temporary storage URLs, refreshed only on reads.
    entered_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, required=False, write_only=True)
    attachments = serializers.SerializerMethodField()

    # File upload fields
    attachments_files = serializers.ListField(
        child=serializers.FileField(),
        write_only=True,
        required=False,
        help_text='Upload multiple files (will be stored in S3)'
    )

    # API alias for frontend compatibility
    approval_hierarchy = serializers.JSONField(source='approval_workflow_config', read_only=True)
    can_approve = serializers.SerializerMethodField()
    current_approval = serializers.SerializerMethodField()
    linked_po_id = serializers.SerializerMethodField()
    linked_po_number = serializers.SerializerMethodField()

    SERVER_CONTROLLED_FIELDS = PR_SERVER_CONTROLLED_FIELDS
    SOURCE_METADATA_FIELDS = (
        'signed_document_verification', 'signed_approval_evidence',
        'signed_pdf_attached', 'source_approval_reviews', 'po_link',
        'po_link_previous_status',
        '_retained_attachment_sources',
    )
    
    class Meta:
        model = PurchaseRequisition
        extra_kwargs = {
            # Validation below is case-insensitive and excludes the edited draft.
            'pr_number': {'validators': []},
        }
        fields = [
            # Header Section (Fields 1-3)
            'id', 'pr_number', 'issued_by', 'issued_by_name', 'issued_date',
            
            # Supplier Section (Fields 4-5)
            'supplier_name', 'supplier_business_id',
            
            # Vendor Integration (Smart linking)
            'vendor', 'vendor_details', 'vendor_name', 'vendor_selection_reason', 'ai_vendor_recommendations',
            
            # Enhanced Vendor Selection (Feedback: Multiple vendors with ICV)
            'selected_vendors', 'single_source_justification',
            
            # Project/Product Section (Fields 6-7)
            'product_service', 'project_department',
            
            # Enhanced Project Selection (Feedback: Multiple projects)
            'project_details', 'enterprise_project', 'enterprise_project_code',
            'enterprise_project_name',
            
            # Description Section (Field 8)
            'description_reason',
            
            # Preferred Supplier Section (Field 9)
            'preferred_supplier_if_any',
            
            # Pricing Section (Fields 10-13)
            'price_description', 'total_price', 'currency', 'price_remarks', 'net_total_excl_vat', 'price_remarks_data',
            'vat_basis', 'entered_amount',
            
            # Management Approval (Feedback: For PR > AED 100k)
            'management_approval', 'management_approval_remarks', 'management_approval_evidence',
            
            # Reference Section (Field 14) - Enhanced with PO Applicable
            'po_applicable', 'po_number_reference', 'linked_po_id', 'linked_po_number',
            
            # Purchase Recommendation Section (Field 15) - RENAMED from special_notes
            'purchase_recommendation',
            
            # Dynamic Approval Workflow
            'approval_workflow_config', 'approval_hierarchy', 'current_approval_step',
            'can_approve', 'current_approval',
            
            # Approvals Section (Fields 16-21) - Enhanced with new tiers
            'pm_name', 'pm_name_display', 'pm_signature', 'pm_approval_status', 'pm_approval_status_display', 'pm_approved_at',
            'eng_manager_name', 'eng_manager_name_display', 'eng_manager_signature', 'eng_manager_approval_status', 'eng_manager_approved_at',
            'manager_projects_name', 'manager_projects_name_display', 'manager_projects_signature', 'manager_projects_approval_status', 'manager_projects_approved_at',
            'vp_op_name', 'vp_op_name_display', 'vp_op_signature', 'vp_op_approval_status', 'vp_op_approval_status_display', 'vp_op_approved_at',
            
            # Footer/Metadata (Fields 22-23)
            'form_reference', 'page_number',
            
            # Legacy fields (backward compatibility)
            'requisition_type', 'requisition_type_display', 'title', 'category', 'category_display',
            'requested_by', 'requester_name', 'requested_by_name', 'department', 'project', 'status',
            'status_display', 'priority', 'priority_display', 'required_date',
            'estimated_budget', 'items', 'approved_by', 'approved_by_name',
            'approved_at', 'rejection_reason', 'notes',
            
            # Review Deadline & Resolution (Feedback fields)
            'review_due_at', 'resolution_referral', 
            
            # Attachments
            'attachments', 'attachments_files',
            
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'attachments',
            *PR_SERVER_CONTROLLED_FIELDS,
        ]

    def get_attachments(self, obj):
        attachments = refreshed_requisition_attachments(obj)
        for index, attachment in enumerate(attachments if isinstance(attachments, list) else []):
            if not isinstance(attachment, dict) or SIGNED_PR_TYPE not in (
                attachment.get('type'), attachment.get('document_type'),
            ):
                continue
            attachment['content_url'] = reverse(
                'requisition-uploaded-document-content',
                kwargs={'pk': obj.pk, 'document_id': index},
            ) if requisition_original_source(obj, index) else ''
        return attachments

    @staticmethod
    def _linked_purchase_order(obj):
        # Consume the view's prefetched relation so register rows add no queries.
        return max(
            obj.purchase_orders.all(),
            key=lambda order: order.created_at,
            default=None,
        )

    def get_linked_po_id(self, obj):
        linked_order = self._linked_purchase_order(obj)
        return str(linked_order.id) if linked_order else None

    def get_linked_po_number(self, obj):
        linked_order = self._linked_purchase_order(obj)
        return linked_order.po_number if linked_order else None

    def _is_super_admin(self, user):
        if getattr(user, 'is_superuser', False):
            return True

        try:
            return user.rbac_profile.roles.filter(
                code='super_admin',
                is_active=True,
            ).exists()
        except (AttributeError, ObjectDoesNotExist):
            return False

    @staticmethod
    def _preserve_source_workflow(instance):
        verification = (getattr(instance, 'price_remarks_data', None) or {}).get('signed_document_verification') or {}
        workflow = getattr(instance, 'approval_workflow_config', None) or []
        return bool(verification.get('signed_off') or (
            verification.get('source_approval_rows') and workflow
            and all(isinstance(stage, dict) and stage.get('external') is True
                    and stage.get('source') == SIGNED_PR_TYPE for stage in workflow)
        ))

    def validate_approval_workflow_config(self, value):
        """Validate approver assignments and discard client-supplied approval state."""
        if self._preserve_source_workflow(self.instance):
            # Editing commercial fields must not replace recorded PDF decisions
            # with the new-form approval route submitted by an older client.
            return self.instance.approval_workflow_config
        if not isinstance(value, list):
            raise serializers.ValidationError('Approval workflow must be a list of stages.')

        if len(value) > 20:
            raise serializers.ValidationError('Approval workflow cannot contain more than 20 stages.')

        User = get_user_model()
        normalized_workflow = []
        assigned_user_ids = set()

        for index, stage in enumerate(value):
            if not isinstance(stage, dict):
                raise serializers.ValidationError(f'Approval stage {index + 1} must be an object.')

            role = str(stage.get('role') or '').strip()
            if not role:
                raise serializers.ValidationError(f'Approval stage {index + 1} requires a role.')
            if len(role) > 100:
                raise serializers.ValidationError(f'Approval stage {index + 1} role is too long.')

            assigned_user_id = stage.get('user_id') or stage.get('approver_id')
            if not assigned_user_id:
                raise serializers.ValidationError(f'Approval stage {index + 1} requires an approver.')

            try:
                approver = User.objects.get(pk=assigned_user_id, is_active=True)
            except (User.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    f'Approval stage {index + 1} must reference an active user.'
                )

            try:
                level = max(0, int(stage.get('level', index + 1)))
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'Approval stage {index + 1} has an invalid level.')

            approver_id = str(approver.pk)
            if approver_id in assigned_user_ids:
                raise serializers.ValidationError(
                    f'Approval stage {index + 1} duplicates an approver already selected in this workflow.'
                )
            assigned_user_ids.add(approver_id)

            normalized_stage = {
                'step': index + 1,
                'level': level,
                'role': role,
                'user_id': str(approver.pk),
                'user_name': employee_display_name(approver),
                'username': (
                    approver.get_username()
                    if callable(getattr(approver, 'get_username', None))
                    else getattr(approver, 'username', '')
                ),
                'user_email': approver.email,
                'status': 'pending',
                'approved_at': None,
                'assignment_id': str(uuid4()),
            }

            if self.instance:
                previous_stage = next((
                    existing for existing in (
                        getattr(self.instance, 'approval_workflow_config', None) or []
                    )
                    if isinstance(existing, dict)
                    and (
                        RequisitionWorkflowService._stage_email(existing) == str(approver.email or '').strip().lower()
                        if RequisitionWorkflowService._stage_email(existing)
                        else str(existing.get('user_id') or existing.get('approver_id') or '') == approver_id
                    )
                    and str(existing.get('level', '')) == str(level)
                    and str(existing.get('role') or '').strip().lower() == role.lower()
                ), None)
                if previous_stage:
                    normalized_stage['assignment_id'] = previous_stage.get('assignment_id', '')
                    for state_field in (
                        'status', 'approved_at', 'approved_by_id', 'approved_by_name', 'approved_by_email',
                        'rejected_at', 'rejected_by_id', 'rejected_by_name',
                        'rejected_by_email', 'rejection_reason', 'signature',
                        'signature_user_id', 'signature_user_email',
                        'evidence_requested_at', 'evidence_requested_by',
                        'evidence_requested_by_id',
                        'evidence_requested_by_name',
                    ):
                        if state_field in previous_stage:
                            normalized_stage[state_field] = previous_stage[state_field]

            stage_name = str(stage.get('stage') or '').strip()
            if stage_name:
                normalized_stage['stage'] = stage_name[:150]

            # This is a presentation-only label for the first column of the
            # approval table (for example L0, L1-A, or PM). Routing continues
            # to use the numeric ``level`` above.
            approval_label = str(stage.get('approval_label') or '').strip()
            if len(approval_label) > 20:
                raise serializers.ValidationError(
                    f'Approval stage {index + 1} table label is too long.'
                )
            if approval_label:
                normalized_stage['approval_label'] = approval_label

            if level == 1:
                normalized_stage['approval_group'] = 'level_1'
                normalized_stage['group_mode'] = 'all'

            normalized_workflow.append(normalized_stage)

        return normalized_workflow

    def validate_price_remarks_data(self, value):
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError('Pricing metadata must be an object.')
        data = dict(value or {})
        existing = getattr(self.instance, 'price_remarks_data', None) or {}
        for key in ('discount_amount', 'discount_percentage'):
            if key in existing and key not in data:
                data[key] = existing[key]
        # Source-document decisions and comparison evidence are written only by
        # the import/link services, never by ordinary form JSON.
        for key in self.SOURCE_METADATA_FIELDS:
            data.pop(key, None)
            if key in existing:
                data[key] = existing[key]
        return data

    def validate_items(self, value):
        return normalize_line_items(value)

    def validate_pr_number(self, value):
        """Persist procurement's manual PR number and reject duplicates."""
        normalized = str(value or '').strip().upper()
        if not normalized:
            raise serializers.ValidationError('Enter the PR number manually.')

        duplicates = PurchaseRequisition.objects.filter(pr_number__iexact=normalized)
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError('This PR number already exists.')

        return normalized

    def validate_attachments_files(self, value):
        existing = self.instance.attachments if self.instance else []
        return validate_attachments(value, existing)

    def validate(self, attrs):
        attempted_server_fields = self.SERVER_CONTROLLED_FIELDS.intersection(self.initial_data.keys())
        if attempted_server_fields:
            raise serializers.ValidationError({
                field: 'This field is controlled by the requisition workflow.'
                for field in sorted(attempted_server_fields)
            })

        if self.instance and 'approval_workflow_config' in attrs:
            request = self.context.get('request')
            user = getattr(request, 'user', None)
            if (
                not user
                or (
                    str(self.instance.issued_by_id) != str(user.id)
                    and not self._is_super_admin(user)
                )
            ):
                raise serializers.ValidationError({
                    'approval_workflow_config': 'Only the requisition issuer may change approval assignments.'
                })

        if 'approval_workflow_config' in attrs:
            po_reference = attrs.get(
                'po_number_reference',
                getattr(self.instance, 'po_number_reference', '') if self.instance else '',
            )
            po_applicable = attrs.get(
                'po_applicable',
                getattr(self.instance, 'po_applicable', False) if self.instance else False,
            )
            attrs['approval_workflow_config'] = normalize_ceo_workflow(
                attrs['approval_workflow_config'],
                po_reference,
                po_applicable,
            )

        if self.instance and not self._preserve_source_workflow(self.instance):
            old_workflow = normalize_ceo_workflow(
                getattr(self.instance, 'approval_workflow_config', None) or [],
                getattr(self.instance, 'po_number_reference', ''), getattr(self.instance, 'po_applicable', False),
            )
            new_workflow = normalize_ceo_workflow(
                attrs.get('approval_workflow_config', old_workflow),
                attrs.get('po_number_reference', getattr(self.instance, 'po_number_reference', '')),
                attrs.get('po_applicable', getattr(self.instance, 'po_applicable', False)),
            )
            protect_approval_route(
                old_workflow or [], new_workflow or [],
                level=RequisitionWorkflowService._stage_level, label='role',
                freeze_route=bool(old_workflow) and canonicalize_pr_status(self.instance.status) != 'draft',
            )

        try:
            attrs = apply_confirmed_input(attrs, self.instance, 'pr')
        except ValueError as error:
            raise serializers.ValidationError({'vat_basis': str(error)}) from error

        if self.instance is None and attrs.get('vat_basis') not in CONFIRMED_BASES and 'items' in attrs and attrs['items']:
            calculated_total = line_items_total(attrs['items'])
            requested_total = attrs.get(
                'total_price',
                getattr(self.instance, 'total_price', None) if self.instance else None,
            )
            if requested_total is None:
                attrs['total_price'] = calculated_total
                attrs.setdefault('net_total_excl_vat', calculated_total)
            elif requested_total != calculated_total:
                raise serializers.ValidationError({
                    'total_price': 'Total price must equal the sum of the line items.'
                })

        project_fields_changed = self.instance is None or bool(
            {'project', 'project_details'}.intersection(attrs)
        )
        if 'enterprise_project' not in attrs and project_fields_changed:
            candidate, _reason = resolve_requisition_enterprise_project(
                project=attrs.get('project', getattr(self.instance, 'project', '')),
                project_details=attrs.get(
                    'project_details', getattr(self.instance, 'project_details', []),
                ),
            )
            # A null value is deliberate for no-match, conflicting, or
            # multi-project requisitions; the migration report surfaces them.
            attrs['enterprise_project'] = candidate

        return attrs
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)

    def get_issued_by_name(self, obj):
        return employee_display_name(obj.issued_by) if obj.issued_by_id else ''

    def get_requested_by_name(self, obj):
        requester = obj.requested_by or obj.issued_by
        return employee_display_name(requester) if requester else ''

    def get_requester_name(self, obj):
        return self.get_requested_by_name(obj)

    def get_approved_by_name(self, obj):
        return employee_display_name(obj.approved_by) if obj.approved_by_id else ''

    def get_pm_name_display(self, obj):
        return employee_display_name(obj.pm_name) if obj.pm_name_id else ''

    def get_eng_manager_name_display(self, obj):
        return employee_display_name(obj.eng_manager_name) if obj.eng_manager_name_id else ''

    def get_manager_projects_name_display(self, obj):
        return employee_display_name(obj.manager_projects_name) if obj.manager_projects_name_id else ''

    def get_vp_op_name_display(self, obj):
        return employee_display_name(obj.vp_op_name) if obj.vp_op_name_id else ''

    def get_status_display(self, obj):
        return canonicalize_pr_status(obj.status).replace('_', ' ').title()

    def get_can_approve(self, obj):
        request = self.context.get('request')
        actor = getattr(request, 'user', None)
        return bool(actor and RequisitionWorkflowService.can_approve(obj, actor))

    def get_current_approval(self, obj):
        status = canonicalize_pr_status(obj.status)
        if status not in RequisitionWorkflowService.ACTIVE_REVIEW_STATUSES | {'converted'}:
            return None
        try:
            workflow = RequisitionWorkflowService._workflow(obj)
            level, stages = RequisitionWorkflowService._active_level_stages(obj, workflow)
        except serializers.ValidationError:
            return None
        if status == 'converted' and not all(stage.get('evidence_requested_at') for _, stage in stages):
            return None
        return {
            'level': level,
            'stages': [{
                'index': index,
                'role': stage.get('role'),
                'user_id': stage.get('user_id') or stage.get('approver_id'),
                'user_email': RequisitionWorkflowService._stage_email(stage),
                'user_name': stage.get('user_name') or stage.get('approver'),
            } for index, stage in stages],
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['status'] = canonicalize_pr_status(instance.status)
        workflow = data.get('approval_workflow_config')
        if isinstance(workflow, list):
            User = get_user_model()
            user_ids = {
                stage.get('user_id') or stage.get('approver_id')
                for stage in workflow if isinstance(stage, dict)
            }
            user_emails = {
                str(stage.get('user_email') or stage.get('approver_email') or '').strip().lower()
                for stage in workflow if isinstance(stage, dict)
            }
            valid_user_ids = [value for value in user_ids if str(value or '').isdigit()]
            valid_emails = [value for value in user_emails if value]
            users = list(User.objects.filter(
                Q(pk__in=valid_user_ids) | Q(email__in=valid_emails)
            ))
            users_by_id = {str(user.pk): user for user in users}
            users_by_email = {str(user.email or '').strip().lower(): user for user in users}
            names = employee_display_names(users)
            normalized = []
            for raw_stage in workflow:
                if not isinstance(raw_stage, dict):
                    normalized.append(raw_stage)
                    continue
                stage = dict(raw_stage)
                user_id = str(stage.get('user_id') or stage.get('approver_id') or '')
                user_email = str(
                    stage.get('user_email') or stage.get('approver_email') or ''
                ).strip().lower()
                # Routing uses email as the stable identity across migrations;
                # showing a stale numeric ID's name can misattribute a signature.
                resolved_user = users_by_email.get(user_email) if user_email else users_by_id.get(user_id)
                if resolved_user and str(stage.get('status', '')).lower() != 'approved':
                    stage['user_id'] = str(resolved_user.pk)
                stage['user_name'] = (
                    names.get(str(resolved_user.pk)) if resolved_user else None
                ) or name_only(
                    stage.get('user_name') or stage.get('approver')
                ) or 'Assigned Employee'
                signature_issue = stage_signature_issue(raw_stage)
                if signature_issue:
                    stage['signature'] = ''
                    stage['signature_review_required'] = True
                    stage['signature_review_reason'] = signature_issue
                if (
                    canonicalize_pr_status(instance.status) == 'converted'
                    and str(stage.get('status', 'pending')).strip().lower() in {'pending', 'in_review'}
                    and not stage.get('evidence_requested_at')
                ):
                    # A converted historical/imported PR cannot still be awaiting
                    # an actionable decision. Its internal evidence was not captured.
                    stage['status'] = 'not_recorded'
                normalized.append(stage)
            normalized = normalize_ceo_workflow(
                normalized,
                instance.po_number_reference,
                instance.po_applicable,
            )
            data['approval_workflow_config'] = normalized
            data['approval_hierarchy'] = normalized
        return data
    
    @transaction.atomic
    def create(self, validated_data):
        # Extract files if present
        files = validated_data.pop('attachments_files', [])
        management_evidence = validated_data.pop('management_approval_evidence_file', None)
        # This JSON column is NOT NULL. Normalize omitted/null multipart data
        # before constructing the model instance.
        validated_data['management_approval_evidence'] = (
            validated_data.get('management_approval_evidence') or []
        )
        
        # Set issued_by to current user if not provided
        if not validated_data.get('issued_by'):
            validated_data['issued_by'] = self.context['request'].user
        
        # Set issued_date to today if not provided
        if not validated_data.get('issued_date'):
            from datetime import date
            validated_data['issued_date'] = date.today()
        
        # Auto-generate title from product_service if not provided
        if not validated_data.get('title') and validated_data.get('product_service'):
            validated_data['title'] = validated_data['product_service'][:300]
        
        # Create the PR instance
        instance = super().create(validated_data)
        
        # Upload files to S3 if any
        if files:
            self._upload_attachments(instance, files)
        if management_evidence:
            instance.management_approval_evidence = self._upload_attachments(instance, [management_evidence])
            instance.save(update_fields=['management_approval_evidence'])
        
        return instance
    
    @transaction.atomic
    def update(self, instance, validated_data):
        # A form may have been validated before an inline source review
        # finished. Save against the locked current record so its status,
        # source decisions and audit cannot be replaced by that stale copy.
        instance = PurchaseRequisition.objects.select_for_update().get(pk=instance.pk)
        # Recheck route changes against decisions committed during validation.
        self.instance = instance
        if not self._preserve_source_workflow(instance):
            old_workflow = normalize_ceo_workflow(instance.approval_workflow_config, instance.po_number_reference, instance.po_applicable)
            new_workflow = normalize_ceo_workflow(
                validated_data.get('approval_workflow_config', old_workflow),
                validated_data.get('po_number_reference', instance.po_number_reference),
                validated_data.get('po_applicable', instance.po_applicable),
            )
            protect_approval_route(
                old_workflow or [], new_workflow or [], level=RequisitionWorkflowService._stage_level, label='role',
                freeze_route=bool(old_workflow) and canonicalize_pr_status(instance.status) != 'draft',
            )
        source_metadata = instance.price_remarks_data or {}
        if 'price_remarks_data' in validated_data:
            metadata = dict(validated_data['price_remarks_data'] or {})
            for key in self.SOURCE_METADATA_FIELDS:
                metadata.pop(key, None)
                if key in source_metadata:
                    metadata[key] = source_metadata[key]
            validated_data['price_remarks_data'] = metadata
        if 'pr_number' in validated_data and validated_data['pr_number'] != instance.pr_number:
            from .services.procurement_lifecycle import RETAINED_ATTACHMENTS, attachment_cleanup_keys
            metadata = dict(validated_data.get('price_remarks_data', source_metadata) or {})
            metadata[RETAINED_ATTACHMENTS] = sorted(attachment_cleanup_keys(instance))
            validated_data['price_remarks_data'] = metadata
        if self._preserve_source_workflow(instance) and 'approval_workflow_config' in validated_data:
            validated_data['approval_workflow_config'] = instance.approval_workflow_config
        elif 'approval_workflow_config' in validated_data:
            # A decision may have committed after validation. Re-read its
            # signature and actor evidence under the same lock as the edit.
            self.instance = instance
            validated_data['approval_workflow_config'] = self.validate_approval_workflow_config(
                validated_data['approval_workflow_config'],
            )
        # Extract files if present
        files = validated_data.pop('attachments_files', [])
        management_evidence = validated_data.pop('management_approval_evidence_file', None)
        previous_workflow = [
            dict(stage) for stage in (instance.approval_workflow_config or [])
            if isinstance(stage, dict)
        ]
        workflow_changed = 'approval_workflow_config' in validated_data
        if 'management_approval_evidence' in validated_data:
            validated_data['management_approval_evidence'] = (
                validated_data.get('management_approval_evidence') or []
            )
        
        # Update the instance
        instance = super().update(instance, validated_data)
        
        # Upload files to S3 if any
        if files:
            self._upload_attachments(instance, files)
        if management_evidence:
            instance.management_approval_evidence = self._upload_attachments(instance, [management_evidence])
            instance.save(update_fields=['management_approval_evidence'])
        if workflow_changed:
            transaction.on_commit(
                lambda: notify_requisition_approver_changes(instance, previous_workflow),
                robust=True,
            )
        
        return instance
    
    def _upload_attachments(self, instance, files):
        """Upload files through the configured storage backend."""
        from django.core.files.storage import default_storage
        from django.utils import timezone
        import logging
        import uuid
        
        logger = logging.getLogger(__name__)
        attachments = list(instance.attachments or [])
        validated_files = validate_attachments(files, attachments)
        uploaded = []
        
        for file in validated_files:
            try:
                safe_name = file.safe_name
                object_id = uuid.uuid4().hex
                s3_key = f"procurement/requisitions/{instance.pr_number}/{object_id}_{safe_name}"
                
                file.seek(0)
                s3_key = default_storage.save(s3_key, file)
                if s3_key:
                    s3_url = default_storage.url(s3_key)
                    
                    # Add to attachments
                    attachments.append({
                        'filename': safe_name,
                        's3_key': s3_key,
                        's3_url': s3_url,
                        'uploaded_at': timezone.now().isoformat(),
                        'uploaded_by': self.context['request'].user.email,
                        'file_size': file.size,
                        'content_type': file.verified_content_type,
                    })
                    uploaded.append(attachments[-1])
                    logger.info(f"Uploaded {safe_name} to S3: {s3_key}")
                else:
                    raise serializers.ValidationError(
                        {'attachments_files': f'Failed to store {safe_name}.'}
                    )
            except serializers.ValidationError:
                raise
            except Exception as e:
                logger.error(f"Error uploading attachment: {type(e).__name__}")
                raise serializers.ValidationError(
                    {'attachments_files': f'Failed to store {file.safe_name}.'}
                ) from e
        
        # Save updated attachments
        instance.attachments = attachments
        instance.save(update_fields=['attachments'])
        return uploaded


class OptionalDateField(serializers.DateField):
    """Treat an empty optional HTML/API date as an unset value."""

    def to_internal_value(self, value):
        if value == '':
            return None
        return super().to_internal_value(value)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order"""

    entered_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, required=False, write_only=True)

    attachments_files = serializers.ListField(
        child=serializers.FileField(),
        write_only=True,
        required=False,
        help_text='Upload the files configured in the PO Attachments tab.',
    )
    po_number = serializers.CharField(required=False, allow_blank=True)
    start_date = OptionalDateField(required=False, allow_null=True)
    end_date = OptionalDateField(required=False, allow_null=True)
    expected_delivery = OptionalDateField(required=False, allow_null=True)
    actual_delivery = OptionalDateField(required=False, allow_null=True)
    approved_date = OptionalDateField(required=False, allow_null=True)
    confirmation_date = OptionalDateField(required=False, allow_null=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    category_display = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    approved_by_user_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    pr_number = serializers.CharField(source='pr_reference.pr_number', read_only=True, allow_null=True)
    po_number_verified = serializers.SerializerMethodField()
    po_number_verification_message = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    current_approval = serializers.SerializerMethodField()
    
    # Project linkage fields (soft-coded relationship)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    project_display = serializers.SerializerMethodField()
    budget_allocation_display = serializers.CharField(source='budget_allocation.description', read_only=True, allow_null=True)
    enterprise_project_code = serializers.CharField(
        source='enterprise_project.code', read_only=True, allow_null=True,
    )
    enterprise_project_name = serializers.CharField(
        source='enterprise_project.name', read_only=True, allow_null=True,
    )
    
    class Meta:
        model = PurchaseOrder
        fields = [
            # Core PO fields
            'id', 'po_number', 'po_number_verified', 'po_number_verification_message',
            'pr_reference', 'pr_number', 'pr_requester_name',
            'vendor', 'vendor_name', 'title', 'description', 
            'status', 'status_display', 'category', 'category_display', 'form_note',
            
            # Seller/Vendor contact details
            'seller_reference', 'quote_ref', 'seller_license_no',
            
            # Buyer/Invoicing contact details
            'invoicing_attn', 'invoicing_emails', 'company_fax',
            
            # Buyer reference contacts
            'buyer_reference_pm', 'buyer_reference_email', 'buyer_reference_pe',
            
            # Financial
            'total_amount', 'currency', 'tax_amount', 'vat_percentage', 'discount_amount',
            'net_amount', 'vat_basis', 'entered_amount',
            
            # Payment & delivery
            'payment_terms', 'payment_mode', 'delivery_terms', 'marking', 
            'payment_milestones', 'workshop_rates',
            
            # Items & pricing
            'items', 'items_table_headers',
            
            # Dates
            'po_date', 'start_date', 'end_date', 'expected_delivery', 'actual_delivery',
            
            # Project linkage
            'project', 'project_name', 'project_display', 'project_number', 'project_manager',
            'enterprise_project', 'enterprise_project_code', 'enterprise_project_name',
            'budget_allocation', 'budget_allocation_display', 'budget',
            
            # Detailed project information
            'end_client', 'contractor', 'subcontractor', 'company_agreement_no', 'rad_project_no',
            
            # Approval section
            'approved_by', 'approved_by_user_name', 'approved_by_name', 'approved_by_title', 'approved_date', 'approved_at',
            'approval_signature', 'approval_stamp',
            'technical_approver', 'financial_approver', 'management_approver',
            'approval_log', 'final_approver_notes', 'can_approve', 'current_approval',
            
            # Order confirmation (vendor response)
            'confirmation_date', 'seller_contact_person', 'seller_phone', 'seller_fax', 'seller_email',
            'seller_address',
            
            # Contract sections
            'scope_of_services', 'safety_requirements', 'variations_clause', 
            'time_schedule', 'reporting_meetings', 'performance_requirements', 'contact_persons',
            
            # People & metadata
            'created_by', 'created_by_name', 'terms_and_conditions', 'notes', 'attachments',
            'attachments_files',
            
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'po_date', 'created_at', 'updated_at',
            'approved_by', 'approved_by_name', 'approved_by_title', 'approved_at',
            'approval_signature', 'approval_stamp',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        rows = []
        for original in data.get('approval_log') or []:
            row = dict(original)
            issue = stage_signature_issue(row)
            if issue:
                row['signature'] = ''
                row['signature_review_required'] = True
                row['signature_review_reason'] = issue
            rows.append(row)
        data['approval_log'] = rows
        issue = purchase_order_signature_issue(instance)
        if issue:
            data['approval_signature'] = ''
            data['signature_review_required'] = True
            data['signature_review_reason'] = issue
        return data

    def to_internal_value(self, data):
        """Accept project identities returned by the unified project picker.

        ``available-projects`` includes company-core projects that do not yet
        have a procurement-master row.  Those entries use ``core:<pk>`` as
        their UI identity, so they cannot be passed to the procurement
        ``project`` UUID foreign key.  Store that selection on the canonical
        enterprise-project relationship instead and leave the optional legacy
        procurement-project relationship unset.
        """
        project_id = data.get('project') if hasattr(data, 'get') else None
        if isinstance(project_id, str) and project_id.startswith('core:'):
            # QueryDict.copy() performs a deep copy. Multipart values include
            # TemporaryUploadedFile objects backed by BufferedRandom, which
            # cannot be pickled. A shallow copy retains repeated file values
            # without trying to copy their open handles.
            normalized_data = copy.copy(data)
            normalized_data.pop('project', None)
            normalized_data['enterprise_project'] = project_id.removeprefix('core:')
            data = normalized_data
        return super().to_internal_value(data)

    def validate_attachments_files(self, value):
        existing = self.instance.attachments if self.instance else []
        return validate_attachments(value, existing)

    def validate_contact_persons(self, value):
        from .services.procurement_lifecycle import RETAINED_ATTACHMENTS, RETAINED_SOURCES

        if not isinstance(value, dict):
            raise serializers.ValidationError('Contact details must be an object.')
        value = dict(value)
        existing = getattr(self.instance, 'contact_persons', None) or {}
        for key in (RETAINED_ATTACHMENTS, RETAINED_SOURCES):
            value.pop(key, None)
            if key in existing:
                value[key] = existing[key]
        return value

    def _upload_attachments(self, instance, files):
        """Store PO attachments using the active local/S3 storage backend."""
        from django.core.files.storage import default_storage
        from django.utils import timezone
        import uuid

        attachments = list(instance.attachments or [])
        validated_files = validate_attachments(files, attachments)
        attachment_details = (instance.contact_persons or {}).get('attachment_details', [])

        for index, file in enumerate(validated_files):
            detail = attachment_details[index] if index < len(attachment_details) else {}
            safe_name = file.safe_name
            s3_key = f"procurement/orders/{instance.po_number}/{uuid.uuid4().hex}_{safe_name}"
            try:
                file.seek(0)
                stored_key = default_storage.save(s3_key, file)
                storage_url = default_storage.url(stored_key)
            except Exception:
                raise serializers.ValidationError(
                    {'attachments_files': f'Failed to store {safe_name}.'}
                )
            attachments.append({
                'title': str(detail.get('title') or f'Attachment {index + 1}').strip(),
                'description': str(detail.get('description') or '').strip(),
                'filename': safe_name,
                's3_key': stored_key,
                's3_url': storage_url,
                'uploaded_at': timezone.now().isoformat(),
                'uploaded_by': self.context['request'].user.email,
                'file_size': file.size,
                'content_type': file.verified_content_type,
            })

        instance.attachments = attachments
        instance.save(update_fields=['attachments'])
    
    def get_project_display(self, obj):
        """Get formatted project display string"""
        if obj.project:
            return f"{obj.project.project_number} - {obj.project.project_name}"
        return None

    def get_current_approval(self, obj):
        active = _active_entries(list(obj.approval_log or []))
        if not active:
            return None
        index, entry = active[0]
        return {
            'stage': entry.get('stage'),
            'level': _entry_level(entry, index),
            'approver': entry.get('approver'),
        }

    def get_can_approve(self, obj):
        request = self.context.get('request')
        if not request or not getattr(request, 'user', None):
            return False
        return can_approve_purchase_order(obj, request.user)
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)

    def _po_number_verification(self, obj):
        pr_number = obj.pr_reference.pr_number if obj.pr_reference_id else None
        return PurchaseOrderNumberService.verify(obj.po_number, pr_number)

    def get_po_number_verified(self, obj):
        return self._po_number_verification(obj)[0]

    def get_po_number_verification_message(self, obj):
        return self._po_number_verification(obj)[1]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Result fields are written only by the decision or reviewed-source
        # services. Older forms may still echo this optional date on save.
        attrs.pop('approved_date', None)
        try:
            attrs = apply_confirmed_input(attrs, self.instance, 'po')
        except ValueError as error:
            raise serializers.ValidationError({'vat_basis': str(error)}) from error
        if self.instance is not None and self.instance.status == 'completed':
            raise serializers.ValidationError(
                'Completed purchase orders are read-only and cannot be edited.'
            )
        if (
            self.instance is not None and self.instance.status != 'draft'
            and attrs.get('status') == 'draft'
            and any(row.get('user_id') or row.get('approver_email')
                    for row in (self.instance.approval_log or []) if isinstance(row, dict))
        ):
            raise serializers.ValidationError({'status': 'A submitted approval workflow cannot be reset through an ordinary edit.'})
        pr = attrs.get('pr_reference')

        # Existing legacy POs may not have a PR. Do not prevent unrelated edits
        # to those records, but every newly-created PO must start from an
        # existing requisition. A requisition may support multiple POs.
        if self.instance is None and pr is None:
            raise serializers.ValidationError({
                'pr_reference': 'Select an existing Purchase Requisition before creating a Purchase Order.'
            })

        po_number = str(attrs.get('po_number') or '').strip().upper()
        if po_number:
            verified, message = PurchaseOrderNumberService.verify(po_number, pr.pr_number if pr else None)
            if not verified:
                raise serializers.ValidationError({'po_number': message})
            duplicate = PurchaseOrder.objects.filter(po_number=po_number)
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError({'po_number': 'This Purchase Order number is already in use.'})
            attrs['po_number'] = po_number

        if 'approval_log' in attrs:
            existing_log = self.instance.approval_log if self.instance is not None else []
            source_history = [dict(entry) for entry in (existing_log or [])
                              if isinstance(entry, dict) and not entry.get('user_id')]
            requisition = pr or (self.instance.pr_reference if self.instance is not None else None)
            has_po_reference = requisition is not None
            attrs['approval_log'] = normalize_assignments(
                attrs.get('approval_log'),
                existing_log=existing_log,
                require_core=False,
                require_management=(
                    attrs.get('status', getattr(self.instance, 'status', 'draft')) != 'draft'
                    and not has_po_reference
                    and not source_history
                ),
            )
            if has_po_reference:
                attrs['approval_log'] = [
                    entry for entry in attrs['approval_log']
                    if entry.get('stage') != 'Final Management Sign-off'
                ]
            attrs['approval_log'].extend(source_history)
            protect_approval_route(
                existing_log or [], attrs['approval_log'], level=_entry_level, label='stage',
                freeze_route=bool(existing_log) and (
                    getattr(self.instance, 'status', 'draft') != 'draft'
                    or any(str(row.get('status') or 'pending').lower() != 'pending' for row in existing_log)
                ),
            )
            by_stage = {entry['stage']: entry for entry in attrs['approval_log']}
            attrs['technical_approver'] = ''
            attrs['financial_approver'] = ''
            attrs['management_approver'] = by_stage.get('Final Management Sign-off', {}).get('approver', '')

        project_fields_changed = self.instance is None or bool(
            {'project', 'project_number', 'pr_reference'}.intersection(attrs)
        )
        if 'enterprise_project' not in attrs and project_fields_changed:
            candidate, _reason = resolve_order_enterprise_project(
                project=attrs.get('project', getattr(self.instance, 'project', None)),
                project_number=attrs.get(
                    'project_number', getattr(self.instance, 'project_number', ''),
                ),
                requisition=pr or getattr(self.instance, 'pr_reference', None),
            )
            attrs['enterprise_project'] = candidate

        return attrs
    
    @transaction.atomic
    def create(self, validated_data):
        files = validated_data.pop('attachments_files', [])
        # Lock the selected PR while the PO relationship is recorded.
        selected_pr = validated_data['pr_reference']
        locked_pr = PurchaseRequisition.objects.select_for_update().get(pk=selected_pr.pk)
        validated_data['pr_reference'] = locked_pr
        if not validated_data.get('po_number'):
            validated_data['po_number'] = PurchaseOrderNumberService.next_for_requisition(locked_pr.pr_number)
        validated_data['created_by'] = self.context['request'].user
        order = super().create(validated_data)
        if files:
            self._upload_attachments(order, files)

        from .services.procurement_lifecycle import mark_requisition_converted
        mark_requisition_converted(locked_pr, order.po_number)
        # Notification delivery is a side effect and must never turn a
        # successfully committed PO into an HTTP 500 response.
        transaction.on_commit(lambda: notify_assigned_approvers(order), robust=True)
        transaction.on_commit(lambda: notify_purchase_order_created(order), robust=True)
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        from .services.procurement_lifecycle import mark_requisition_converted, reconcile_requisition_orders

        old_pr_id = instance.pr_reference_id
        selected_pr = validated_data.get('pr_reference', instance.pr_reference)
        pr_ids = {value for value in (old_pr_id, selected_pr.pk if selected_pr else None) if value}
        locked_prs = {
            pr.pk: pr for pr in PurchaseRequisition.objects.select_for_update().filter(pk__in=pr_ids).order_by('pk')
        }
        instance = PurchaseOrder.objects.select_for_update().get(pk=instance.pk)
        if instance.pr_reference_id != old_pr_id:
            raise serializers.ValidationError('The linked recommendation changed. Refresh this order before saving.')
        if instance.status == 'completed':
            raise serializers.ValidationError('Completed purchase orders are read-only and cannot be edited.')
        if (
            instance.status != 'draft' and validated_data.get('status') == 'draft'
            and any(row.get('user_id') or row.get('approver_email')
                    for row in (instance.approval_log or []) if isinstance(row, dict))
        ):
            raise serializers.ValidationError({'status': 'A submitted approval workflow cannot be reset through an ordinary edit.'})
        # A stale edit must not erase original-document evidence recorded after
        # its serializer was validated, or turn that history into assignments.
        source_history = [dict(entry) for entry in (instance.approval_log or [])
                          if isinstance(entry, dict) and not entry.get('user_id')]
        if 'approval_log' in validated_data:
            protect_approval_route(
                instance.approval_log or [], validated_data['approval_log'], level=_entry_level, label='stage',
                freeze_route=bool(instance.approval_log) and (
                    instance.status != 'draft'
                    or any(str(row.get('status') or 'pending').lower() != 'pending' for row in instance.approval_log)
                ),
            )
            validated_data['approval_log'] = normalize_assignments(
                [entry for entry in validated_data['approval_log'] if entry.get('user_id')],
                existing_log=instance.approval_log,
                require_core=False,
            ) + source_history
        if any(entry.get('evidence_document_id') and entry.get('signature_verified') for entry in source_history):
            for field in ('approved_by', 'approved_by_name', 'approved_by_title', 'approved_date',
                          'approval_signature', 'approval_stamp'):
                if field in validated_data and getattr(instance, field):
                    validated_data[field] = getattr(instance, field)
        if 'attachments' in validated_data:
            originals = [entry for entry in (instance.attachments or []) if isinstance(entry, dict)
                         and entry.get('type') == 'signed_purchase_order_pdf']
            validated_data['attachments'] = [entry for entry in (validated_data['attachments'] or [])
                                             if not (isinstance(entry, dict) and entry.get('type') == 'signed_purchase_order_pdf')] + originals
        if 'contact_persons' in validated_data:
            from .services.procurement_lifecycle import RETAINED_ATTACHMENTS, RETAINED_SOURCES
            contacts = instance.contact_persons or {}
            for key in (RETAINED_ATTACHMENTS, RETAINED_SOURCES):
                validated_data['contact_persons'].pop(key, None)
                if key in contacts:
                    validated_data['contact_persons'][key] = contacts[key]
        previous_number = instance.po_number
        if 'po_number' in validated_data and validated_data['po_number'] != previous_number:
            from .services.procurement_lifecycle import RETAINED_ATTACHMENTS, attachment_cleanup_keys
            contacts = dict(validated_data.get('contact_persons', instance.contact_persons) or {})
            contacts[RETAINED_ATTACHMENTS] = sorted(attachment_cleanup_keys(instance))
            validated_data['contact_persons'] = contacts
        files = validated_data.pop('attachments_files', [])
        order = super().update(instance, validated_data)
        if files:
            self._upload_attachments(order, files)
        if old_pr_id != order.pr_reference_id:
            if old_pr_id in locked_prs:
                reconcile_requisition_orders(locked_prs[old_pr_id], previous_number)
            if order.pr_reference_id in locked_prs:
                mark_requisition_converted(locked_prs[order.pr_reference_id], order.po_number)
        elif order.pr_reference_id and order.po_number != previous_number:
            mark_requisition_converted(locked_prs[order.pr_reference_id], order.po_number)
        transaction.on_commit(lambda: notify_assigned_approvers(order), robust=True)
        return order


class ReceiptSerializer(serializers.ModelSerializer):
    """Serializer for Goods Receipt"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True, allow_null=True)
    po_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    
    class Meta:
        model = Receipt
        fields = [
            'id', 'receipt_number', 'purchase_order', 'po_number', 'receipt_date',
            'received_by', 'received_by_name', 'status', 'status_display',
            'items_received', 'quality_check_passed', 'inspection_notes',
            'certificates_received', 'heat_numbers', 'inspector_name',
            'inspection_agency', 'inspection_report_number', 'ndt_performed',
            'ndt_results', 'dimensional_check_passed',
            'visual_inspection_passed', 'material_verification_passed',
            'delivery_note_number', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'receipt_number', 'receipt_date', 'received_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        from apps.rbac.action_policy import request_action_allowed
        from rest_framework.exceptions import PermissionDenied

        if self.instance is None:
            changes_disposition = attrs.get('status', 'pending') != 'pending'
        else:
            changes_disposition = 'status' in attrs and attrs['status'] != self.instance.status
        request = self.context.get('request')
        if changes_disposition and not (request and request_action_allowed(request, 'procurement_receipts', 'approve')):
            raise PermissionDenied('Receipt approval access is required to set or change its inspection disposition.')
        return super().validate(attrs)

    def to_representation(self, instance):
        from .services.receipt_inspection import enrich_receipt
        return enrich_receipt(super().to_representation(instance), instance, self.context.get('request'))

    def create(self, validated_data):
        validated_data['receipt_number'] = ReceiptNumberService.next_number()
        validated_data['received_by'] = self.context['request'].user
        return super().create(validated_data)


class ProcurementCategorySerializer(serializers.Serializer):
    """Serializer for procurement category configuration"""
    
    code = serializers.CharField()
    name = serializers.CharField()
    icon = serializers.CharField()
    color = serializers.CharField()


class PODocumentReviewSerializer(serializers.Serializer):
    """Editable business fields only; source, signatures and approval remain immutable."""
    po_number = serializers.CharField(max_length=100, required=False)
    summary = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    vendor_name = serializers.CharField(max_length=300, required=False, allow_blank=True)
    currency = serializers.CharField(max_length=3, min_length=3, required=False)
    total_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, required=False, allow_null=True)
    tax_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, required=False, allow_null=True)
    gross_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, required=False, allow_null=True)
    entered_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, required=False)
    vat_basis = serializers.ChoiceField(choices=['unconfirmed', 'exclusive', 'inclusive', 'none'], required=False)
    po_date = serializers.DateField(required=False, allow_null=True)
    expected_delivery = serializers.DateField(required=False, allow_null=True)
    pr_id = serializers.PrimaryKeyRelatedField(queryset=PurchaseRequisition.objects.all(), required=False, allow_null=True)

    def validate_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('Enter a three-letter currency code.')
        return value

    def validate_po_number(self, value):
        from .services.po_excel_import import canonical_po_number
        if not canonical_po_number(value):
            raise serializers.ValidationError('Enter a valid RAD purchase order number.')
        return value

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({field: 'This field cannot be edited.' for field in unknown})
        return attrs


class PODocumentReconcileSerializer(serializers.Serializer):
    vendor_id = serializers.PrimaryKeyRelatedField(queryset=Vendor.objects.filter(status='active'))
    pr_id = serializers.PrimaryKeyRelatedField(queryset=PurchaseRequisition.objects.all(), required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({field: 'Save reviewed business fields before completing reconciliation.' for field in unknown})
        return attrs


class PODocumentSerializer(serializers.ModelSerializer):
    """Serializer for uploaded PO/PR documents and their AI-extracted data."""

    uploaded_by_name = serializers.CharField(source='uploaded_by.get_full_name', read_only=True, allow_null=True)
    extraction_status_display = serializers.CharField(source='get_extraction_status_display', read_only=True)
    document_type_display = serializers.CharField(source='get_document_type_display', read_only=True)
    canonical_financials = serializers.SerializerMethodField()

    def get_canonical_financials(self, instance):
        return (instance.extracted_data or {}).get('canonical_financials')

    def to_representation(self, instance):
        result = super().to_representation(instance)
        fields = result.get('extracted_data')
        if instance.document_type == 'purchase_order' and isinstance(fields, dict) and fields.get('summary') and 'summary' not in fields.get('manually_reviewed_fields', []):
            from .services.signed_po_pdf_import import normalize_po_summary
            original_summary = fields['summary']
            cleaned_summary = normalize_po_summary(original_summary)
            if cleaned_summary != original_summary:
                fields = {**fields, 'summary': cleaned_summary, 'ocr_summary_raw': fields.get('ocr_summary_raw', original_summary)}
                result['extracted_data'] = fields
        if instance.document_type == 'purchase_order' and isinstance(fields, dict) and fields.get('vendor_name') and not fields.get('vendor_name_source'):
            # Older uploads used an unbounded OCR seller span. Keep the source
            # evidence intact while presenting the actual company name.
            from .services.signed_po_pdf_import import _seller_name
            original = fields['vendor_name']
            cleaned = _seller_name(f'Seller: {original}')
            if cleaned and cleaned != original:
                result['extracted_data'] = {**fields, 'vendor_name': cleaned, 'ocr_vendor_name_raw': fields.get('ocr_vendor_name_raw', original)}
        return result

    class Meta:
        model = PODocument
        fields = [
            'id', 'original_filename', 's3_key', 's3_url', 'file_size_bytes',
            'document_type', 'document_type_display', 'extraction_status',
            'extraction_status_display', 'extraction_error', 'extracted_data', 'canonical_financials',
            'uploaded_by', 'uploaded_by_name', 'confirmed_po',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


# ============================================================================
# MASTER DATABASE SERIALIZERS - Professional Project-Based Procurement
# ============================================================================

class CostCenterSerializer(serializers.ModelSerializer):
    """Cost Center master table serializer"""
    
    manager_name = serializers.CharField(source='manager.get_full_name', read_only=True, allow_null=True)
    parent_name = serializers.CharField(source='parent.name', read_only=True, allow_null=True)
    
    class Meta:
        from .models import CostCenter
        model = CostCenter
        fields = [
            'id', 'code', 'name', 'description', 'parent', 'parent_name',
            'department', 'division', 'is_active', 'manager', 'manager_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BudgetSerializer(serializers.ModelSerializer):
    """Budget allocation serializer with computed spend tracking"""
    
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    project_number = serializers.CharField(source='project.project_number', read_only=True, allow_null=True)
    cost_center_name = serializers.CharField(source='cost_center.name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    
    # Computed fields (soft-coded)
    spent_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    utilization_percentage = serializers.SerializerMethodField()
    is_over_budget = serializers.SerializerMethodField()
    
    class Meta:
        from .models import Budget
        model = Budget
        fields = [
            'id', 'project', 'project_name', 'project_number',
            'cost_center', 'cost_center_name', 'category', 'category_display',
            'sub_category', 'description', 'allocated_amount', 'currency',
            'fiscal_year', 'period_start', 'period_end',
            'is_approved', 'approved_by', 'approved_by_name', 'approved_at',
            'spent_amount', 'remaining_amount', 'utilization_percentage', 'is_over_budget',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_spent_amount(self, obj):
        return float(obj.get_spent_amount())
    
    def get_remaining_amount(self, obj):
        return float(obj.get_remaining_amount())
    
    def get_utilization_percentage(self, obj):
        return float(obj.get_utilization_percentage())
    
    def get_is_over_budget(self, obj):
        return obj.is_over_budget()


class ProjectListSerializer(serializers.ModelSerializer):
    """Lightweight project serializer for list views"""
    
    project_manager_display = serializers.SerializerMethodField()
    cost_center_name = serializers.CharField(source='cost_center.name', read_only=True, allow_null=True)
    project_type_display = serializers.CharField(source='get_project_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    # Key metrics (soft-coded computations)
    total_budget = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    budget_utilization = serializers.SerializerMethodField()
    enterprise_project_code = serializers.CharField(
        source='enterprise_project.code', read_only=True, allow_null=True,
    )
    enterprise_project_name = serializers.CharField(
        source='enterprise_project.name', read_only=True, allow_null=True,
    )
    
    class Meta:
        from .models import Project
        model = Project
        fields = [
            'id', 'project_number', 'project_name', 'client_name',
            'enterprise_project', 'enterprise_project_code', 'enterprise_project_name',
            'project_type', 'project_type_display', 'status', 'status_display',
            'cost_center', 'cost_center_name', 'project_manager', 'project_manager_display',
            'start_date', 'planned_end_date', 'contract_value', 'contract_currency',
            'progress_percentage', 'health_status', 'is_active', 'is_billable',
            'total_budget', 'total_spent', 'budget_utilization',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if 'enterprise_project' not in attrs and (
            self.instance is None or 'project_number' in attrs
        ):
            candidate, _reason = resolve_enterprise_project_by_code(
                attrs.get('project_number', getattr(self.instance, 'project_number', ''))
            )
            attrs['enterprise_project'] = candidate
        return attrs
    
    def get_project_manager_display(self, obj):
        if obj.project_manager:
            return obj.project_manager.get_full_name()
        return obj.project_manager_name or 'N/A'
    
    def get_total_budget(self, obj):
        return float(obj.get_total_budget())
    
    def get_total_spent(self, obj):
        return float(obj.get_total_spent())
    
    def get_budget_utilization(self, obj):
        return float(obj.get_budget_utilization())


class ProjectDetailSerializer(ProjectListSerializer):
    """Full project serializer with all relationships"""
    
    lead_engineer_name = serializers.CharField(source='lead_engineer.get_full_name', read_only=True, allow_null=True)
    team_member_names = serializers.SerializerMethodField()
    budgets = BudgetSerializer(many=True, read_only=True)
    purchase_order_count = serializers.SerializerMethodField()
    
    class Meta(ProjectListSerializer.Meta):
        fields = ProjectListSerializer.Meta.fields + [
            'client_reference', 'project_manager_name', 'lead_engineer', 'lead_engineer_name',
            'team_members', 'team_member_names', 'description', 'scope_of_work',
            'deliverables', 'actual_end_date', 'site_location', 'country',
            'region', 'payment_terms', 'notes', 'tags', 'is_internal',
            'budgets', 'purchase_order_count'
        ]
    
    def get_team_member_names(self, obj):
        return [m.get_full_name() for m in obj.team_members.all()]
    
    def get_purchase_order_count(self, obj):
        return obj.purchase_orders.count()

