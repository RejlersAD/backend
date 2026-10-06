"""
Sales Serializers
DRF serializers for Sales Management API
"""

from .saved_email_analysis import analyze_saved_email
from .email_customer_matching import enrich_customer_match
from .email_permissions import (
    can_create_email_opportunity, visible_email_intakes, visible_email_opportunities,
)

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.rbac.action_policy import module_action_allowed
from .models import (
    Client, Contact, Deal, FrameworkAgreement, OpportunityAuditEvent,
    ProjectHandover, Quote, SalesActivity, SalesEmailIntake, SalesForecast,
    SalesMailboxConnection,
)

User = get_user_model()


def _complete_value_sum(queryset, field):
    """An incomplete commercial total is unknown, not a partial total or zero."""
    from django.db.models import Count, Sum
    totals = queryset.aggregate(records=Count('pk'), known=Count(field), total=Sum(field))
    if totals['known'] != totals['records']:
        return None
    return totals['total'] if totals['records'] else 0


def _user_display_name(user):
    if user is None:
        return ''
    return user.get_full_name() or user.username or user.email


def _opportunity_creator_name(opportunity):
    return _user_display_name(opportunity.created_by)


class SalesEmailIntakeSerializer(serializers.ModelSerializer):
    """Employee-facing intake record with immutable email provenance."""

    reviewed_by_name = serializers.CharField(
        source='reviewed_by.get_full_name', read_only=True,
    )
    opportunity_name = serializers.CharField(
        source='opportunity.deal_name', read_only=True,
    )
    duplicate_of_subject = serializers.CharField(
        source='duplicate_of.subject', read_only=True,
    )
    extracted_information = serializers.SerializerMethodField()
    source_token = serializers.SerializerMethodField()
    can_create_opportunity = serializers.SerializerMethodField()
    can_create_client = serializers.SerializerMethodField()

    class Meta:
        model = SalesEmailIntake
        fields = [
            'id', 'source_message_id', 'internet_message_id', 'subject',
            'sender_name', 'sender_email', 'received_at', 'sent_at', 'body_preview',
            'has_attachments', 'importance', 'status', 'opportunity',
            'opportunity_name', 'reviewed_by', 'reviewed_by_name',
            'reviewed_at', 'resolution_note', 'duplicate_of',
            'duplicate_of_subject', 'created_at', 'updated_at',
            'extracted_information', 'source_token', 'can_create_opportunity', 'can_create_client',
            'mailbox_connection', 'source_mailbox_address', 'source_tenant_id',
            'conversation_id', 'captured_by',
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = request.user if request else None
        visibility = getattr(self, '_related_visibility', None)
        if visibility is None:
            visibility = self._related_visibility = {}
        if instance.duplicate_of_id:
            key = ('intake', instance.duplicate_of_id)
            if key not in visibility:
                visibility[key] = bool(user and visible_email_intakes(user).filter(pk=instance.duplicate_of_id).exists())
            if not visibility[key]:
                data['duplicate_of'], data['duplicate_of_subject'] = None, ''
        if instance.opportunity_id:
            key = ('opportunity', instance.opportunity_id)
            if key not in visibility:
                visibility[key] = bool(
                    user and module_action_allowed(user, 'sales_opportunities', 'read')
                    and visible_email_opportunities(user).filter(pk=instance.opportunity_id).exists()
                )
            if not visibility[key]:
                data['opportunity'], data['opportunity_name'] = None, ''
        return data

    def get_extracted_information(self, obj):
        information = self._email_information(obj)
        return enrich_customer_match(information, request=self.context.get('request'))

    def _email_information(self, obj):
        analyses = self.context.setdefault('_saved_email_analyses', {})
        key = str(obj.pk)
        if key not in analyses:
            view = self.context.get('view')
            self.context['email_ai_allow_provider'] = getattr(view, 'action', None) == 'retrieve'
            self.context['email_ai_skip'] = getattr(view, 'action', None) == 'list'
            analyses[key] = analyze_saved_email(obj, request=self.context.get('request'), context=self.context)
        return analyses[key]

    def get_source_token(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or not user.is_active:
            return None
        if getattr(self.context.get('view'), 'action', None) == 'list':
            return None
        information = self._email_information(obj)
        source_hash = self.context.get('_saved_email_source_hashes', {}).get(str(obj.pk))
        if not source_hash:
            return None
        from .email_opportunity_evidence import saved_email_review_token
        return saved_email_review_token(obj, user, source_hash, information)

    def get_can_create_opportunity(self, obj):
        return bool(obj.status in {'received', 'under_review'} and self._creation_capabilities()[0])

    def get_can_create_client(self, obj):
        return bool(obj.status in {'received', 'under_review'} and self._creation_capabilities()[1])

    def _creation_capabilities(self):
        """Same-actor display grants are shared only within this response.

        Record status remains checked per row; conversion commands always
        recheck live authority. Never attach this memo to a user/global cache.
        """
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or not user.is_active:
            return False, False
        key = (id(request), user.pk)
        cached = getattr(self, '_email_creation_capabilities', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        opportunity = can_create_email_opportunity(user)
        capabilities = (
            opportunity, opportunity and module_action_allowed(user, 'sales_clients', 'create'),
        )
        self._email_creation_capabilities = (key, capabilities)
        return capabilities


# ==============================================================================
# USER SERIALIZERS
# ==============================================================================

class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user info for relationships"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name']
        read_only_fields = fields
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username


# ==============================================================================
# CLIENT SERIALIZERS
# ==============================================================================

class ContactSerializer(serializers.ModelSerializer):
    """Contact serializer"""
    full_name = serializers.ReadOnlyField()
    
    class Meta:
        model = Contact
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ClientListSerializer(serializers.ModelSerializer):
    """Lightweight client list serializer"""
    account_manager_name = serializers.CharField(source='account_manager.get_full_name', read_only=True)
    primary_contact = serializers.SerializerMethodField()
    active_deals_count = serializers.SerializerMethodField()
    total_deal_value = serializers.SerializerMethodField()
    
    class Meta:
        model = Client
        fields = [
            'id', 'client_code', 'company_name', 'industry_type', 'client_tier',
            'status', 'account_manager', 'account_manager_name', 'health_score',
            'churn_risk', 'lifetime_value', 'last_contact_date', 'created_at',
            'primary_contact', 'active_deals_count', 'total_deal_value', 'tags'
            , 'legal_name', 'trading_name', 'parent_client', 'country',
            'market_sectors', 'verification_status', 'new_proposals_permitted',
            'email', 'website'
        ]
        read_only_fields = ['id', 'created_at', 'health_score']
    
    def get_primary_contact(self, obj):
        contact = obj.contacts.filter(is_primary=True).first()
        return contact.full_name if contact else None
    
    def get_active_deals_count(self, obj):
        return obj.deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).count()
    
    def get_total_deal_value(self, obj):
        return _complete_value_sum(
            obj.deals.exclude(stage__in=['lost', 'no_bid', 'cancelled']), 'estimated_value',
        )


class ClientDetailSerializer(serializers.ModelSerializer):
    """Detailed client serializer with all relationships"""
    account_manager_details = UserBasicSerializer(source='account_manager', read_only=True)
    contacts = ContactSerializer(many=True, read_only=True)
    recent_activities = serializers.SerializerMethodField()
    deals_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'health_score', 'lifetime_value']
    
    def get_recent_activities(self, obj):
        activities = obj.activities.all()[:5]
        return SalesActivityListSerializer(activities, many=True).data
    
    def get_deals_summary(self, obj):
        deals = obj.deals.all()
        return {
            'total_count': deals.count(),
            'active_count': deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).count(),
            'won_count': deals.filter(stage__in=['awarded', 'converted']).count(),
            'lost_count': deals.filter(stage='lost').count(),
            'total_value': _complete_value_sum(deals, 'estimated_value'),
            'pipeline_value': _complete_value_sum(
                deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']),
                'weighted_value',
            ),
        }


class ClientCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating clients"""
    
    class Meta:
        model = Client
        exclude = ['health_score', 'churn_risk', 'lifetime_value', 'created_at', 'updated_at']
        extra_kwargs = {'client_code': {'required': False}}

    def validate(self, attrs):
        company_name = attrs.get('company_name', getattr(self.instance, 'company_name', '')).strip()
        legal_name = attrs.get('legal_name', getattr(self.instance, 'legal_name', '')).strip()
        candidates = Client.objects.exclude(pk=getattr(self.instance, 'pk', None))
        if candidates.filter(company_name__iexact=company_name).exists():
            raise serializers.ValidationError({'company_name': 'A possible duplicate client already exists.'})
        if legal_name and candidates.filter(legal_name__iexact=legal_name).exists():
            raise serializers.ValidationError({'legal_name': 'A client with this legal identity already exists.'})
        return attrs
    
    def create(self, validated_data):
        # Auto-generate client code if not provided
        if not validated_data.get('client_code'):
            from django.utils.crypto import get_random_string
            prefix = validated_data['company_name'][:3].upper()
            validated_data['client_code'] = f"CLT-{prefix}-{get_random_string(6, '0123456789')}"
        
        return super().create(validated_data)


class FrameworkAgreementSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    remaining_value = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    is_eligible = serializers.BooleanField(read_only=True)

    class Meta:
        model = FrameworkAgreement
        fields = '__all__'
        read_only_fields = ['id', 'approved_by', 'approved_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        effective = attrs.get('effective_date', getattr(self.instance, 'effective_date', None))
        expiry = attrs.get('expiry_date', getattr(self.instance, 'expiry_date', None))
        if effective and expiry and expiry < effective:
            raise serializers.ValidationError({'expiry_date': 'Expiry must be after the effective date.'})
        ceiling = attrs.get('ceiling_value', getattr(self.instance, 'ceiling_value', None))
        committed = attrs.get('committed_value', getattr(self.instance, 'committed_value', 0))
        if ceiling is not None and committed > ceiling:
            raise serializers.ValidationError({'committed_value': 'Committed value cannot exceed the framework ceiling.'})
        return attrs


# ==============================================================================
# DEAL SERIALIZERS
# ==============================================================================

class DealListSerializer(serializers.ModelSerializer):
    """Lightweight deal list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    owner_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    stage_display = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    framework_number = serializers.CharField(source='framework.framework_number', read_only=True)
    
    class Meta:
        model = Deal
        fields = [
            'id', 'deal_code', 'deal_name', 'client', 'client_name', 'stage',
            'stage_display', 'probability', 'priority', 'estimated_value',
            'weighted_value', 'currency', 'expected_close_date', 'owner',
            'owner_name', 'ai_win_probability', 'created_at', 'days_in_stage',
            'scope_type', 'location', 'client_reference', 'submission_due_date',
            'next_action_date', 'bid_decision', 'award_status', 'award_value',
            'converted_project', 'stage_entered_at',
            'framework', 'framework_number', 'client_contact', 'disciplines',
            'estimated_hours', 'delivery_office', 'opportunity_source', 'service_categories',
            'next_action', 'risk_level',
            'opportunity_type', 'open_date', 'created_by', 'created_by_name',
        ]
        read_only_fields = ['id', 'deal_code', 'weighted_value', 'created_at', 'created_by']
    
    def get_stage_display(self, obj):
        from .models import DEAL_STAGES
        return DEAL_STAGES.get(obj.stage, {}).get('name', obj.stage)

    def get_created_by_name(self, obj):
        return _opportunity_creator_name(obj)

    def get_owner_name(self, obj):
        return _user_display_name(obj.owner)
    
    def get_days_in_stage(self, obj):
        from django.utils import timezone
        entered = obj.stage_entered_at or obj.updated_at
        return (timezone.now().date() - entered.date()).days


class OpportunityAuditEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = OpportunityAuditEvent
        fields = [
            'id', 'event_type', 'from_stage', 'to_stage', 'reason', 'data',
            'actor', 'actor_name', 'occurred_at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        if not obj.actor:
            return ''
        return (
            obj.actor.get_full_name()
            or obj.actor.username
            or obj.actor.email
        )


class DealDetailSerializer(serializers.ModelSerializer):
    """Detailed deal serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    client_details = ClientListSerializer(source='client', read_only=True)
    owner_name = serializers.SerializerMethodField()
    owner_details = UserBasicSerializer(source='owner', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    team_members_details = UserBasicSerializer(source='team_members', many=True, read_only=True)
    quotes = serializers.SerializerMethodField()
    activities = serializers.SerializerMethodField()
    stage_history = serializers.SerializerMethodField()
    permitted_actions = serializers.SerializerMethodField()
    
    class Meta:
        model = Deal
        fields = '__all__'
        read_only_fields = ['id', 'deal_code', 'created_by', 'weighted_value', 'ai_win_probability', 'created_at', 'updated_at']
    
    def get_quotes(self, obj):
        quotes = obj.quotes.all()[:5]
        return QuoteListSerializer(quotes, many=True).data

    def get_created_by_name(self, obj):
        return _opportunity_creator_name(obj)

    def get_owner_name(self, obj):
        return _user_display_name(obj.owner)
    
    def get_activities(self, obj):
        activities = obj.activities.all()[:10]
        return SalesActivityListSerializer(activities, many=True).data
    
    def get_stage_history(self, obj):
        history = list(
            OpportunityAuditEventSerializer(
                obj.audit_events.select_related('actor').all()[:100],
                many=True,
            ).data
        )
        creation_events = {
            'opportunity_created', 'opportunity_created_from_email',
        }
        if not any(item['event_type'] in creation_events for item in history):
            source = obj.custom_fields or {}
            creator_name = self.get_created_by_name(obj)
            history.append({
                'id': f'created-{obj.id}',
                'event_type': (
                    'opportunity_created_from_email'
                    if source.get('source_email_intake_id')
                    else 'opportunity_created'
                ),
                'from_stage': '',
                'to_stage': 'lead',
                'reason': '',
                'data': {
                    key: source[key]
                    for key in (
                        'source_email_intake_id', 'source_message_id',
                        'internet_message_id', 'sender_email', 'received_at',
                    )
                    if source.get(key)
                },
                'actor': obj.created_by_id,
                'actor_name': creator_name,
                'occurred_at': obj.created_at,
            })
        return sorted(
            history,
            key=lambda item: str(item.get('occurred_at') or ''),
            reverse=True,
        )

    def get_permitted_actions(self, obj):
        actions = {
            'lead': ['submit_qualification'],
            'qualified': ['bid_decision'],
            'proposal': ['enter_negotiation'],
            'negotiation': ['submit_award'],
            'award_pending': ['approve_award', 'reject_award'],
            'awarded': ['convert_to_project'],
        }
        permitted = actions.get(obj.stage, [])
        if obj.stage not in {'awarded', 'converted', 'lost', 'no_bid', 'cancelled'}:
            permitted = [*permitted, 'close']
        return permitted


class DealCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating deals"""
    
    class Meta:
        model = Deal
        exclude = ['weighted_value', 'ai_win_probability', 'ai_recommended_actions', 'created_at', 'updated_at']
        read_only_fields = [
            'deal_code', 'created_by',
            'stage', 'stage_entered_at', 'bid_decision', 'bid_decision_reason',
            'bid_decided_by', 'bid_decided_at', 'award_status', 'award_submitted_by',
            'award_submitted_at', 'award_approved_by', 'award_approved_at',
            'award_rejection_reason', 'converted_project', 'converted_by',
            'converted_at', 'actual_close_date',
        ]

    def _actor(self):
        return self.context.get('actor') or getattr(self.context.get('request'), 'user', None)

    def validate_owner(self, value):
        from .opportunity_registration import visible_opportunity_owners
        actor = self._actor()
        if value is None:
            if self.instance is not None:
                raise serializers.ValidationError('Choose an active owner within your Sales access.')
            return value
        if self.instance is not None and value.pk != self.instance.owner_id:
            try:
                profile = actor.rbac_profile
                is_manager = profile.status == 'active' and profile.roles.filter(
                    is_active=True, level__lte=3,
                ).exists()
            except Exception:
                is_manager = False
            if not is_manager:
                raise serializers.ValidationError('Only Sales managers or higher may reassign opportunity ownership.')
        if not visible_opportunity_owners(actor).filter(pk=value.pk).exists():
            raise serializers.ValidationError('Choose an active owner within your Sales access.')
        return value

    def validate_client(self, value):
        from .email_permissions import visible_email_clients
        actor = self._actor()
        if (
            not actor or not actor.is_authenticated or not actor.is_active
            or not module_action_allowed(actor, 'sales_clients', 'read')
            or not visible_email_clients(actor).filter(pk=value.pk).exists()
        ):
            raise serializers.ValidationError('Choose a client within your Sales access.')
        return value

    def validate_nominated_project_manager(self, value):
        if self.instance and getattr(value, 'pk', None) != self.instance.nominated_project_manager_id:
            if ProjectHandover.objects.filter(opportunity=self.instance).exists():
                raise serializers.ValidationError('The award nomination is retained with its handover. Manage a pending handover assignment through the handover workflow.')
        if value is not None:
            from .workflow import require_project_manager
            require_project_manager(value)
        return value

    def validate(self, attrs):
        framework = attrs.get('framework', getattr(self.instance, 'framework', None))
        client = attrs.get('client', getattr(self.instance, 'client', None))
        if self.instance and getattr(client, 'pk', None) != self.instance.client_id:
            from .models import BidPreparation
            if BidPreparation.objects.filter(opportunity=self.instance).exists():
                raise serializers.ValidationError({'client': 'The preparation connection retains its canonical client.'})
        if framework and framework.client_id != getattr(client, 'id', None):
            raise serializers.ValidationError({
                'framework': 'The framework must belong to the selected client.',
            })
        return attrs
    
    def create(self, validated_data):
        from .opportunity_registration import create_registered_opportunity
        return create_registered_opportunity(actor=self._actor(), validated_data=validated_data)


# ==============================================================================
# QUOTE SERIALIZERS
# ==============================================================================

class QuoteListSerializer(serializers.ModelSerializer):
    """Lightweight quote list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    deal_name = serializers.CharField(source='deal.deal_name', read_only=True)
    deal_code = serializers.CharField(source='deal.deal_code', read_only=True)
    submission_due_date = serializers.DateField(source='deal.submission_due_date', read_only=True, allow_null=True)
    service_categories = serializers.JSONField(source='deal.service_categories', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    days_until_expiry = serializers.SerializerMethodField()
    
    class Meta:
        model = Quote
        fields = [
            'id', 'quote_number', 'version', 'deal', 'deal_name', 'deal_code',
            'submission_due_date', 'service_categories', 'client',
            'client_name', 'status', 'total_amount', 'currency', 'issue_date',
            'valid_until', 'prepared_by', 'prepared_by_name', 'created_at',
            'days_until_expiry'
            , 'estimated_cost', 'expected_margin_percent', 'approved_by',
            'approved_at', 'submitted_version_hash', 'submission_recipient'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_days_until_expiry(self, obj):
        from django.utils import timezone
        if obj.valid_until:
            delta = obj.valid_until - timezone.now().date()
            return delta.days
        return None


class QuoteDetailSerializer(serializers.ModelSerializer):
    """Detailed quote serializer"""
    issue_date = serializers.DateField(default=serializers.CreateOnlyDefault(timezone.localdate))
    client_details = ClientListSerializer(source='client', read_only=True)
    deal_details = DealListSerializer(source='deal', read_only=True)
    prepared_by_details = UserBasicSerializer(source='prepared_by', read_only=True)
    PROTECTED_FIELDS = ('prepared_by', 'approved_by', 'approved_at', 'approval_history',
                        'submitted_version_hash', 'submission_recipient', 'submission_evidence',
                        'sent_date', 'viewed_date', 'response_date', 'expected_margin_percent')
    
    class Meta:
        model = Quote
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'prepared_by', 'approved_by', 'approved_at',
                            'approval_history', 'submitted_version_hash', 'submission_recipient',
                            'submission_evidence', 'sent_date', 'viewed_date', 'response_date', 'expected_margin_percent']

    def validate(self, attrs):
        from .bid_preparation import DRAFT_STATES, require_editable, visible_deals
        from .email_permissions import visible_email_clients
        for field in self.PROTECTED_FIELDS:
            if field in self.initial_data:
                current = getattr(self.instance, field, None) if self.instance else None
                current = getattr(current, 'pk', current)
                supplied = self.initial_data[field]
                if (self.instance is None and supplied not in (None, '', [], {})) or (
                        self.instance is not None and str(supplied) != str(current)):
                    raise serializers.ValidationError({field: 'This field is maintained by its governed workflow.'})
        if attrs.get('status', 'draft') not in DRAFT_STATES:
            raise serializers.ValidationError({'status': 'Use the governed proposal command for this status.'})
        if self.instance:
            require_editable(self.instance)
            for field in ('deal', 'client', 'version', 'quote_number'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'Proposal identity is retained. Create a separate revision.'})
        deal = attrs.get('deal', getattr(self.instance, 'deal', None))
        client = attrs.get('client', getattr(self.instance, 'client', None))
        actor = getattr(self.context.get('request'), 'user', None) or self.context.get('actor')
        if not self.instance:
            from .proposal_readiness import require_proposal_creation
            _, deal, client = require_proposal_creation(actor, deal, client)
            attrs['deal'], attrs['client'] = deal, client
            return attrs
        if actor:
            if (not module_action_allowed(actor, 'sales_opportunities', 'read')
                    or not deal or not visible_deals(actor).filter(pk=deal.pk).exists()):
                raise serializers.ValidationError({'deal': 'Choose an opportunity within your Sales access.'})
            if (not module_action_allowed(actor, 'sales_clients', 'read')
                    or not client or not visible_email_clients(actor).filter(pk=client.pk).exists()):
                raise serializers.ValidationError({'client': 'Choose a client within your current Sales access.'})
        if deal and client and deal.client_id != client.id:
            raise serializers.ValidationError({
                'client': 'Proposal client must match its opportunity.',
            })
        from .proposal_readiness import client_permits_proposal_preparation
        if client and not client_permits_proposal_preparation(client):
            raise serializers.ValidationError({
                'client': 'This client is not currently permitted for new proposals.',
            })
        return attrs


class ProjectHandoverSerializer(serializers.ModelSerializer):
    opportunity_code = serializers.CharField(source='opportunity.deal_code', read_only=True)
    opportunity_name = serializers.CharField(source='opportunity.deal_name', read_only=True)
    client_name = serializers.CharField(source='opportunity.client.company_name', read_only=True)
    proposal_number = serializers.CharField(source='proposal.quote_number', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    project_manager_name = serializers.CharField(source='project_manager.get_full_name', read_only=True)
    accepted_by_name = serializers.CharField(source='accepted_by.get_full_name', read_only=True)

    def validate_project_manager(self, value):
        from .workflow import require_project_manager
        return require_project_manager(value)

    class Meta:
        model = ProjectHandover
        fields = '__all__'
        read_only_fields = [
            'id', 'opportunity', 'proposal', 'owner', 'contract_value', 'currency',
            'status', 'accepted_by', 'accepted_at', 'project', 'created_at', 'updated_at',
        ]


# ==============================================================================
# ACTIVITY SERIALIZERS
# ==============================================================================

class SalesActivityListSerializer(serializers.ModelSerializer):
    """Lightweight activity list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    deal_name = serializers.CharField(source='deal.deal_name', read_only=True, allow_null=True)
    contact_name = serializers.CharField(source='contact.full_name', read_only=True, allow_null=True)
    performed_by_name = serializers.CharField(source='performed_by.get_full_name', read_only=True)
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    
    class Meta:
        model = SalesActivity
        fields = [
            'id', 'activity_type', 'activity_type_display', 'subject', 'client',
            'client_name', 'deal', 'deal_name', 'contact', 'contact_name',
            'activity_date', 'duration_minutes', 'performed_by', 'performed_by_name',
            'outcome', 'follow_up_date', 'sentiment_score', 'created_at'
        ]
        read_only_fields = ['id', 'sentiment_score', 'created_at']


class SalesActivityDetailSerializer(serializers.ModelSerializer):
    """Detailed activity serializer"""
    client_details = ClientListSerializer(source='client', read_only=True)
    deal_details = DealListSerializer(source='deal', read_only=True, allow_null=True)
    contact_details = ContactSerializer(source='contact', read_only=True, allow_null=True)
    performed_by_details = UserBasicSerializer(source='performed_by', read_only=True)
    participants_details = UserBasicSerializer(source='participants', many=True, read_only=True)
    
    class Meta:
        model = SalesActivity
        fields = '__all__'
        read_only_fields = ['id', 'sentiment_score', 'key_topics', 'created_at', 'updated_at']


# ==============================================================================
# FORECAST SERIALIZERS
# ==============================================================================

class SalesForecastSerializer(serializers.ModelSerializer):
    """Sales forecast serializer"""
    generated_by_name = serializers.CharField(source='generated_by.get_full_name', read_only=True)
    variance = serializers.SerializerMethodField()
    
    class Meta:
        model = SalesForecast
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'accuracy']
    
    def get_variance(self, obj):
        if obj.actual_revenue and obj.predicted_revenue:
            return float(obj.actual_revenue - obj.predicted_revenue)
        return None

    def validate_manual_adjustments(self, value):
        invalid = [index for index, item in enumerate(value) if not item.get('reason')]
        if invalid:
            raise serializers.ValidationError('Every manual adjustment requires a reason.')
        return value


class SalesMailboxConnectionSerializer(serializers.ModelSerializer):
    secret_configured = serializers.SerializerMethodField()
    token_encryption_configured = serializers.SerializerMethodField()
    delegated_connected = serializers.SerializerMethodField()
    sync = serializers.SerializerMethodField()
    last_error = serializers.SerializerMethodField()

    class Meta:
        model = SalesMailboxConnection
        exclude = ['encrypted_refresh_token']
        read_only_fields = [
            'id', 'last_status', 'last_health_check_at', 'last_error',
            'mailbox_display_name', 'total_item_count', 'unread_item_count',
            'delegated_account_id', 'delegated_account_name', 'delegated_scopes',
            'connected_at',
            'created_by', 'updated_by', 'created_at', 'updated_at',
        ]

    def to_internal_value(self, data):
        if self.instance is None and isinstance(data, dict) and data.get('auth_mode') == 'application':
            allowed = {'name', 'auth_mode', 'tenant_id', 'client_id', 'mailbox_address', 'enabled'}
            unexpected = set(data) - allowed
            if unexpected:
                raise serializers.ValidationError({
                    field: 'This field cannot be supplied when adding a shared mailbox.'
                    for field in sorted(unexpected)
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None and attrs.get('auth_mode') == 'application':
            if attrs.get('enabled', False):
                raise serializers.ValidationError({'enabled': 'Save the connection first, then use configure-sync to enable automatic syncing.'})
            attrs['enabled'] = False
        protected = ('mailbox_address', 'tenant_id', 'client_id', 'auth_mode')
        changed = [field for field in protected if self.instance is not None
                   and field in attrs and attrs[field] != getattr(self.instance, field)]
        has_sync = self.instance is not None and hasattr(self.instance, 'sync_state')
        identity_locked = self.instance is not None and (has_sync or self.instance.email_intakes.exists())
        if has_sync and 'enabled' in attrs and attrs['enabled'] != self.instance.enabled:
            raise serializers.ValidationError({'enabled': 'Use configure-sync to change automatic syncing.'})
        if changed and identity_locked:
            raise serializers.ValidationError({
                field: 'This mailbox connection has saved emails or sync history. Create a separate connection for a different identity.'
                for field in changed
            })
        auth_mode = attrs.get('auth_mode', self.instance.auth_mode if self.instance else None)
        if auth_mode == 'application' and 'mailbox_address' in attrs:
            # Normalize correctable setup records, never retained source identity.
            address = attrs['mailbox_address'] if identity_locked else attrs['mailbox_address'].strip().lower()
            duplicates = SalesMailboxConnection.objects.filter(mailbox_address__iexact=address)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError({'mailbox_address': 'This mailbox connection already exists.'})
            attrs['mailbox_address'] = address
        return attrs

    def get_sync(self, obj):
        from .mailbox_sync import sync_projection
        return sync_projection(obj)

    def get_last_error(self, obj):
        # Historical provider diagnostics remain server-side as well; reopening
        # setup must not expose them through the ordinary list/detail response.
        return 'Microsoft could not verify this mailbox. Check its configuration and server access.' if obj.last_error else ''

    def get_secret_configured(self, obj):
        import os
        return bool(os.environ.get('RADAI_SALES_GRAPH_CLIENT_SECRET', '').strip())

    def get_token_encryption_configured(self, obj):
        from .graph_crypto import is_configured
        return is_configured()

    def get_delegated_connected(self, obj):
        return bool(obj.auth_mode == 'delegated' and obj.encrypted_refresh_token)


# ==============================================================================
# DASHBOARD & ANALYTICS SERIALIZERS
# ==============================================================================

class SalesDashboardSerializer(serializers.Serializer):
    """Dashboard summary statistics"""
    total_clients = serializers.IntegerField()
    active_clients = serializers.IntegerField()
    total_deals = serializers.IntegerField()
    active_deals = serializers.IntegerField()
    pipeline_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    won_value_mtd = serializers.DecimalField(max_digits=15, decimal_places=2)
    avg_deal_size = serializers.DecimalField(max_digits=15, decimal_places=2)
    win_rate = serializers.FloatField()
    avg_sales_cycle_days = serializers.IntegerField()
    top_clients = ClientListSerializer(many=True)
    top_deals = DealListSerializer(many=True)
    recent_activities = SalesActivityListSerializer(many=True)
    deals_by_stage = serializers.DictField()
    revenue_by_industry = serializers.DictField()
    forecast_next_month = serializers.DecimalField(max_digits=15, decimal_places=2)


class AIInsightSerializer(serializers.Serializer):
    """AI-generated insights"""
    insight_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    confidence = serializers.FloatField()
    recommendation = serializers.CharField()
    action_items = serializers.ListField(child=serializers.CharField())
    impact = serializers.CharField()
    related_entities = serializers.DictField()
