"""Valve Standards — Django admin."""
from django.contrib import admin

from .models import (
    Standard,
    MaterialGroup,
    MaterialGroupDesignation,
    MaterialGroupSpec,
    Group4BoltingMaterial,
    PressureTemperatureRating,
    WallThicknessByDiameter,
    WallThicknessSocketweldThreaded,
    NpsToInsideDiameter,
    ReferenceStandardCitation,
    MaterialAllowableStress,
    HighPressureAllowableStress,
    CastingQualityFactor,
    WeldJointQualityFactor,
    ThermalExpansionCoefficient,
    ModulusOfElasticity,
    DrillingTemplate,
    FlangeDimension,
    FlangeBoltingRecommendation,
)


@admin.register(Standard)
class StandardAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'edition_year')
    search_fields = ('code', 'title')


class MaterialGroupDesignationInline(admin.TabularInline):
    model = MaterialGroupDesignation
    extra = 0


class MaterialGroupSpecInline(admin.TabularInline):
    model = MaterialGroupSpec
    extra = 0


@admin.register(MaterialGroup)
class MaterialGroupAdmin(admin.ModelAdmin):
    list_display = ('group_no', 'family', 'family_name', 'standard')
    list_filter = ('family',)
    search_fields = ('group_no',)
    inlines = [MaterialGroupDesignationInline, MaterialGroupSpecInline]


@admin.register(Group4BoltingMaterial)
class Group4BoltingMaterialAdmin(admin.ModelAdmin):
    list_display = ('seq', 'spec_no', 'grade')
    search_fields = ('spec_no', 'grade')


@admin.register(PressureTemperatureRating)
class PressureTemperatureRatingAdmin(admin.ModelAdmin):
    list_display = ('material_group', 'class_section', 'class_number', 'temp_label', 'pressure', 'pressure_unit')
    list_filter = ('class_section', 'class_number', 'pressure_unit', 'temp_unit')
    search_fields = ('material_group__group_no',)


@admin.register(DrillingTemplate)
class DrillingTemplateAdmin(admin.ModelAdmin):
    list_display = ('class_number', 'unit', 'nps', 'outside_diameter_o', 'bolt_circle_w', 'num_bolts')
    list_filter = ('class_number', 'unit')
    search_fields = ('nps',)


@admin.register(FlangeDimension)
class FlangeDimensionAdmin(admin.ModelAdmin):
    list_display = ('class_number', 'unit', 'nps', 'outside_diameter_o')
    list_filter = ('class_number', 'unit')
    search_fields = ('nps',)


@admin.register(FlangeBoltingRecommendation)
class FlangeBoltingRecommendationAdmin(admin.ModelAdmin):
    list_display = ('product', 'carbon_steel', 'alloy_steel')


@admin.register(WallThicknessByDiameter)
class WallThicknessByDiameterAdmin(admin.ModelAdmin):
    list_display = ('unit', 'inside_dia_d', 'class_number', 'min_wall_thickness_tm')
    list_filter = ('unit', 'class_number')


@admin.register(WallThicknessSocketweldThreaded)
class WallThicknessSocketweldThreadedAdmin(admin.ModelAdmin):
    list_display = ('nps', 'class_group', 'mm', 'inch')
    list_filter = ('class_group',)


@admin.register(NpsToInsideDiameter)
class NpsToInsideDiameterAdmin(admin.ModelAdmin):
    list_display = ('nps', 'dn', 'class_number', 'mm', 'inch')
    list_filter = ('class_number',)


@admin.register(ReferenceStandardCitation)
class ReferenceStandardCitationAdmin(admin.ModelAdmin):
    list_display = ('citation',)
    search_fields = ('citation',)


@admin.register(MaterialAllowableStress)
class MaterialAllowableStressAdmin(admin.ModelAdmin):
    list_display = ('spec_no', 'type_grade', 'category', 'product_form', 'tensile_ksi', 'yield_ksi')
    list_filter = ('category', 'product_form')
    search_fields = ('spec_no', 'type_grade', 'uns_no')


@admin.register(HighPressureAllowableStress)
class HighPressureAllowableStressAdmin(admin.ModelAdmin):
    list_display = ('spec_no', 'type_grade', 'category', 'tensile_ksi', 'yield_ksi')
    search_fields = ('spec_no', 'type_grade', 'uns_no')


@admin.register(CastingQualityFactor)
class CastingQualityFactorAdmin(admin.ModelAdmin):
    list_display = ('spec_no', 'category', 'description', 'ec')
    list_filter = ('category',)


@admin.register(WeldJointQualityFactor)
class WeldJointQualityFactorAdmin(admin.ModelAdmin):
    list_display = ('spec_no', 'category', 'class_or_type', 'description', 'ej')
    list_filter = ('category',)


@admin.register(ThermalExpansionCoefficient)
class ThermalExpansionCoefficientAdmin(admin.ModelAdmin):
    list_display = ('material',)
    search_fields = ('material',)


@admin.register(ModulusOfElasticity)
class ModulusOfElasticityAdmin(admin.ModelAdmin):
    list_display = ('material',)
    search_fields = ('material',)
