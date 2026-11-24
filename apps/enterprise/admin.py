from django.contrib import admin

from .models import (OrganizationAdmin, OrganizationEdition, OrganizationPolicy, Team,
                     TeamMembership, TeamWorkspaceAccess)


@admin.register(OrganizationEdition)
class OrganizationEditionAdmin(admin.ModelAdmin):
    list_display = ('organization', 'edition', 'seat_limit', 'max_teams', 'valid_until', 'updated_at')
    list_filter = ('edition',)
    raw_id_fields = ('updated_by',)


@admin.register(OrganizationPolicy)
class OrganizationPolicyAdmin(admin.ModelAdmin):
    list_display = ('organization', 'require_mfa', 'allow_external_guests', 'ai_outputs_require_approval',
                    'data_residency_region', 'updated_at')
    raw_id_fields = ('updated_by',)


@admin.register(OrganizationAdmin)
class OrganizationAdminAdmin(admin.ModelAdmin):
    list_display = ('organization', 'user', 'role', 'created_at')
    list_filter = ('role',)
    raw_id_fields = ('user', 'granted_by')


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    raw_id_fields = ('user', 'invited_by')


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'is_active', 'created_at')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'slug')
    inlines = [TeamMembershipInline]
    raw_id_fields = ('created_by',)


@admin.register(TeamWorkspaceAccess)
class TeamWorkspaceAccessAdmin(admin.ModelAdmin):
    list_display = ('team', 'workspace', 'role', 'created_at')
    raw_id_fields = ('workspace', 'linked_by')
