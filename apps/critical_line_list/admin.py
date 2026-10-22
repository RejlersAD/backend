from django.contrib import admin

from .models import CllProject, CllProjectData


@admin.register(CllProject)
class CllProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'owner', 'storage', 'is_deleted', 'updated_at')
    list_filter = ('storage', 'is_deleted')
    search_fields = ('name', 'unit', 'client', 'owner__email')


@admin.register(CllProjectData)
class CllProjectDataAdmin(admin.ModelAdmin):
    list_display = ('project', 'updated_at')
