"""Valve Standards — DRF serializers (all read-only reference data)."""
from rest_framework import serializers

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
    MaterialAllowableStressPoint,
    HighPressureAllowableStress,
    HighPressureAllowableStressPoint,
    CastingQualityFactor,
    WeldJointQualityFactor,
    ThermalExpansionCoefficient,
    ModulusOfElasticity,
    DrillingTemplate,
    FlangeDimension,
    FlangeBoltingRecommendation,
)


class StandardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Standard
        fields = ['id', 'code', 'title', 'edition_year']


class MaterialGroupDesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialGroupDesignation
        fields = ['seq', 'composition']


class MaterialGroupSpecSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialGroupSpec
        fields = ['product_form', 'seq', 'spec_no', 'grade']


class MaterialGroupListSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialGroup
        fields = ['group_no', 'family', 'family_name']


class MaterialGroupDetailSerializer(serializers.ModelSerializer):
    designations = MaterialGroupDesignationSerializer(many=True, read_only=True)
    specs = MaterialGroupSpecSerializer(many=True, read_only=True)

    class Meta:
        model = MaterialGroup
        fields = ['group_no', 'family', 'family_name', 'designations', 'specs']


class Group4BoltingMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group4BoltingMaterial
        fields = ['seq', 'spec_no', 'grade', 'notes']


class PressureTemperatureRatingSerializer(serializers.ModelSerializer):
    group_no = serializers.CharField(source='material_group.group_no', read_only=True)

    class Meta:
        model = PressureTemperatureRating
        fields = [
            'id', 'group_no', 'class_section', 'class_type', 'class_number',
            'temp_label', 'temp_unit', 'pressure', 'pressure_unit',
        ]


class WallThicknessByDiameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = WallThicknessByDiameter
        fields = ['id', 'unit', 'inside_dia_d', 'class_number', 'min_wall_thickness_tm']


class WallThicknessSocketweldThreadedSerializer(serializers.ModelSerializer):
    class Meta:
        model = WallThicknessSocketweldThreaded
        fields = ['id', 'nps', 'class_group', 'mm', 'inch']


class NpsToInsideDiameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = NpsToInsideDiameter
        fields = ['id', 'nps', 'dn', 'class_number', 'mm', 'inch']


class ReferenceStandardCitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferenceStandardCitation
        fields = ['id', 'citation']


# ───────────────────────────────────────────────────────────────────
# ASME B31.3
# ───────────────────────────────────────────────────────────────────
class MaterialAllowableStressPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialAllowableStressPoint
        fields = ['temp_f', 'allowable_stress_ksi']


class MaterialAllowableStressSerializer(serializers.ModelSerializer):
    points = MaterialAllowableStressPointSerializer(many=True, read_only=True)

    class Meta:
        model = MaterialAllowableStress
        fields = [
            'id', 'category', 'composition', 'product_form', 'spec_no', 'type_grade',
            'uns_no', 'class_condition', 'size', 'p_no', 'notes', 'min_temp',
            'tensile_ksi', 'yield_ksi', 'points',
        ]


class MaterialAllowableStressListSerializer(serializers.ModelSerializer):
    """Lighter list view (no nested points) for browsing large result sets."""
    class Meta:
        model = MaterialAllowableStress
        fields = [
            'id', 'category', 'composition', 'product_form', 'spec_no', 'type_grade',
            'uns_no', 'class_condition', 'size', 'p_no', 'notes', 'min_temp',
            'tensile_ksi', 'yield_ksi',
        ]


class HighPressureAllowableStressPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = HighPressureAllowableStressPoint
        fields = ['temp_f', 'allowable_stress_ksi']


class HighPressureAllowableStressSerializer(serializers.ModelSerializer):
    points = HighPressureAllowableStressPointSerializer(many=True, read_only=True)

    class Meta:
        model = HighPressureAllowableStress
        fields = [
            'id', 'category', 'composition', 'product_form', 'spec_no', 'type_grade',
            'uns_no', 'p_no', 'notes', 'tensile_ksi', 'yield_ksi', 'points',
        ]


class CastingQualityFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = CastingQualityFactor
        fields = ['id', 'category', 'spec_no', 'description', 'ec', 'notes']


class WeldJointQualityFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeldJointQualityFactor
        fields = ['id', 'category', 'spec_no', 'class_or_type', 'description', 'ej', 'notes']


class ThermalExpansionCoefficientSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThermalExpansionCoefficient
        fields = ['id', 'material', 'coefficient_1e6_in_per_in_f', 'linear_expansion_in_per_100ft']


class ModulusOfElasticitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ModulusOfElasticity
        fields = ['id', 'material', 'modulus_1e6_psi']
        fields = ['id', 'citation']


# ───────────────────────────────────────────────────────────────────
# ASME B16.5
# ───────────────────────────────────────────────────────────────────
class DrillingTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DrillingTemplate
        fields = [
            'id', 'class_number', 'unit', 'nps', 'outside_diameter_o', 'bolt_circle_w',
            'bolt_hole_diameter', 'num_bolts', 'bolt_diameter',
            'bolt_length_1', 'bolt_length_2', 'bolt_length_3', 'note',
        ]


class FlangeDimensionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlangeDimension
        fields = ['id', 'class_number', 'unit', 'nps', 'outside_diameter_o', 'values', 'note']


class FlangeBoltingRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlangeBoltingRecommendation
        fields = ['id', 'product', 'carbon_steel', 'alloy_steel']
