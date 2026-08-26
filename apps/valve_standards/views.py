"""
Valve Standards — read-only reference API + validate endpoint.

Function-based @api_view, matching this repo's convention (see
apps.spec_customization.views). All endpoints are read-only reference data
except /validate/, which performs a simple two-point linear interpolation
per ASME B16.34 para 2.1(f) — NOT a full rules engine (no footnote/exception
handling; those remain visible as `notes`/null cells on the underlying rows).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import constants as c
from .models import (
    MaterialGroup,
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
from .serializers import (
    MaterialGroupListSerializer,
    MaterialGroupDetailSerializer,
    PressureTemperatureRatingSerializer,
    WallThicknessByDiameterSerializer,
    WallThicknessSocketweldThreadedSerializer,
    NpsToInsideDiameterSerializer,
    ReferenceStandardCitationSerializer,
    MaterialAllowableStressSerializer,
    MaterialAllowableStressListSerializer,
    HighPressureAllowableStressSerializer,
    CastingQualityFactorSerializer,
    WeldJointQualityFactorSerializer,
    ThermalExpansionCoefficientSerializer,
    ModulusOfElasticitySerializer,
    DrillingTemplateSerializer,
    FlangeDimensionSerializer,
    FlangeBoltingRecommendationSerializer,
)


# ─────────────────────────────────────────────────────────────────────────────
# Config — drives every frontend filter dropdown, nothing hardcoded client-side
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def config_view(request):
    # Scoped to B16.34 — this API predates B16.5, whose MaterialGroup rows
    # reuse the same group_no numbering under a different Standard.
    group_nos = list(
        MaterialGroup.objects.filter(standard__code=c.DEFAULT_STANDARD_CODE)
        .order_by('family', 'group_no').values_list('group_no', flat=True)
    )
    class_numbers = sorted({
        row['class_number'] for row in PressureTemperatureRating.objects.filter(
            material_group__standard__code=c.DEFAULT_STANDARD_CODE
        ).values('class_number').distinct()
    })
    return Response({
        'standard_code': c.DEFAULT_STANDARD_CODE,
        'group_nos': group_nos,
        'families': [{'value': v, 'label': lbl} for v, lbl in c.FAMILY_CHOICES],
        'product_forms': [{'value': v, 'label': lbl} for v, lbl in c.PRODUCT_FORM_CHOICES],
        'class_numbers': class_numbers,
        'class_sections': [{'value': v, 'label': lbl} for v, lbl in c.CLASS_SECTION_CHOICES],
        'temp_units': [{'value': v, 'label': lbl} for v, lbl in c.TEMP_UNIT_CHOICES],
        'pressure_units': [{'value': v, 'label': lbl} for v, lbl in c.PRESSURE_UNIT_CHOICES],
        'length_units': [{'value': v, 'label': lbl} for v, lbl in c.LENGTH_UNIT_CHOICES],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Material groups (browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_group_list(request):
    qs = MaterialGroup.objects.filter(standard__code=c.DEFAULT_STANDARD_CODE).order_by('family', 'group_no')
    family = request.query_params.get('family')
    if family:
        qs = qs.filter(family=family)
    return Response(MaterialGroupListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_group_detail(request, group_no):
    obj = get_object_or_404(
        MaterialGroup.objects.filter(standard__code=c.DEFAULT_STANDARD_CODE).prefetch_related('designations', 'specs'),
        group_no=group_no,
    )
    return Response(MaterialGroupDetailSerializer(obj).data)


# ─────────────────────────────────────────────────────────────────────────────
# Pressure-temperature ratings (Table 2, browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rating_list(request):
    qs = PressureTemperatureRating.objects.select_related('material_group').filter(
        material_group__standard__code=c.DEFAULT_STANDARD_CODE
    ).order_by('material_group__group_no', 'class_section', 'class_number')
    group_no = request.query_params.get('group_no')
    class_number = request.query_params.get('class_number')
    class_section = request.query_params.get('class_section')
    pressure_unit = request.query_params.get('pressure_unit')
    if group_no:
        qs = qs.filter(material_group__group_no=group_no)
    if class_number:
        qs = qs.filter(class_number=class_number)
    if class_section:
        qs = qs.filter(class_section=class_section.upper())
    if pressure_unit:
        qs = qs.filter(pressure_unit=pressure_unit)
    return Response(PressureTemperatureRatingSerializer(qs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Table 3 — wall thickness by diameter (browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def wall_thickness_list(request):
    qs = WallThicknessByDiameter.objects.all().order_by('unit', 'inside_dia_d')
    unit = request.query_params.get('unit')
    class_number = request.query_params.get('class_number')
    if unit:
        qs = qs.filter(unit=unit)
    if class_number:
        qs = qs.filter(class_number=class_number)
    return Response(WallThicknessByDiameterSerializer(qs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Table 4 — socket-weld / threaded wall thickness (browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def wall_thickness_socketweld_list(request):
    qs = WallThicknessSocketweldThreaded.objects.all().order_by('id')
    class_group = request.query_params.get('class_group')
    if class_group:
        qs = qs.filter(class_group=class_group)
    return Response(WallThicknessSocketweldThreadedSerializer(qs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Appendix A-1 — NPS to inside diameter (browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def nps_to_id_list(request):
    qs = NpsToInsideDiameter.objects.all().order_by('dn')
    class_number = request.query_params.get('class_number')
    if class_number:
        qs = qs.filter(class_number=class_number)
    return Response(NpsToInsideDiameterSerializer(qs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Appendix VIII — reference standards (browse)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reference_standard_list(request):
    qs = ReferenceStandardCitation.objects.all().order_by('citation')
    return Response(ReferenceStandardCitationSerializer(qs, many=True).data)


# ─────────────────────────────────────────────────────────────────────────────
# Validate — interpolated pressure-temperature rating lookup
# ─────────────────────────────────────────────────────────────────────────────
def _parse_temp_label(label: str) -> float:
    """'-29 to 38' -> upper bound 38.0 (conservative — the standard tabulates
    ranges as a single rated value); a plain '100' -> 100.0."""
    label = label.strip()
    if 'to' in label:
        label = label.split('to')[-1].strip()
    return float(label.replace('\u2212', '-'))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_rating(request):
    """
    Body: {group_no, class_number, class_section, temp_value, temp_unit,
           pressure_unit? (defaults to the section's native unit),
           target_pressure? (optional pass/fail check)}

    Two-point linear interpolation ONLY (ASME B16.34 para 2.1(f)) between the
    two tabulated temperatures bracketing `temp_value`. Does not reproduce
    footnote exceptions (e.g. flanged-end 538°C cutoff) — those remain visible
    via `bracketing_rows` for the caller to read.
    """
    body = request.data
    required = ['group_no', 'class_number', 'class_section', 'temp_value', 'temp_unit']
    missing = [f for f in required if body.get(f) in (None, '')]
    if missing:
        return Response(
            {'error': f"Missing required field(s): {', '.join(missing)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        temp_value = float(body['temp_value'])
    except (TypeError, ValueError):
        return Response({'error': 'temp_value must be numeric'}, status=status.HTTP_400_BAD_REQUEST)

    qs = PressureTemperatureRating.objects.select_related('material_group').filter(
        material_group__group_no=body['group_no'],
        class_number=body['class_number'],
        class_section=str(body['class_section']).upper(),
        temp_unit=str(body['temp_unit']).upper(),
    )
    pressure_unit = body.get('pressure_unit')
    if pressure_unit:
        qs = qs.filter(pressure_unit=pressure_unit)

    rows = sorted(
        ({'temp': _parse_temp_label(r.temp_label), 'pressure': r.pressure, 'row': r} for r in qs),
        key=lambda x: x['temp'],
    )
    rows = [r for r in rows if r['pressure'] is not None]  # skip "not rated" cells

    if not rows:
        return Response(
            {'error': 'No tabulated rating rows found for this group/class/section/unit combination.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if temp_value <= rows[0]['temp']:
        lo = hi = rows[0]
    elif temp_value >= rows[-1]['temp']:
        lo = hi = rows[-1]
    else:
        lo, hi = None, None
        for i in range(len(rows) - 1):
            if rows[i]['temp'] <= temp_value <= rows[i + 1]['temp']:
                lo, hi = rows[i], rows[i + 1]
                break

    if lo is hi:
        interpolated = float(lo['pressure'])
    else:
        span = hi['temp'] - lo['temp']
        frac = (temp_value - lo['temp']) / span if span else 0
        interpolated = float(lo['pressure']) + frac * (float(hi['pressure']) - float(lo['pressure']))

    result = {
        'group_no': body['group_no'],
        'class_number': int(body['class_number']),
        'class_section': str(body['class_section']).upper(),
        'temp_value': temp_value,
        'temp_unit': str(body['temp_unit']).upper(),
        'interpolated_pressure': round(interpolated, 3),
        'pressure_unit': lo['row'].pressure_unit,
        'bracketing_rows': [
            PressureTemperatureRatingSerializer(lo['row']).data,
            PressureTemperatureRatingSerializer(hi['row']).data,
        ],
    }

    target_pressure = body.get('target_pressure')
    if target_pressure not in (None, ''):
        try:
            target = float(target_pressure)
            result['target_pressure'] = target
            result['pass'] = target <= interpolated
        except (TypeError, ValueError):
            return Response({'error': 'target_pressure must be numeric'}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result)


# ───────────────────────────────────────────────────────────────────
# ASME B31.3 — Process Piping (Tables A-1/A-2, K-1, A-1A, A-1B, C-1, C-6)
# ───────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b313_config_view(request):
    qs = MaterialAllowableStress.objects.values_list('category', flat=True).distinct()
    categories = sorted({cat for cat in qs if cat})
    spec_nos = sorted(set(
        MaterialAllowableStress.objects.values_list('spec_no', flat=True).distinct()
    ))
    return Response({
        'standard_code': c.B31_3_STANDARD_CODE,
        'categories': categories,
        'spec_nos': spec_nos,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_allowable_stress_list(request):
    qs = MaterialAllowableStress.objects.all().order_by('category', 'spec_no', 'type_grade')
    spec_no = request.query_params.get('spec_no')
    category = request.query_params.get('category')
    product_form = request.query_params.get('product_form')
    if spec_no:
        qs = qs.filter(spec_no__iexact=spec_no)
    if category:
        qs = qs.filter(category=category)
    if product_form:
        qs = qs.filter(product_form__iexact=product_form)
    return Response(MaterialAllowableStressListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_allowable_stress_detail(request, pk):
    obj = get_object_or_404(MaterialAllowableStress.objects.prefetch_related('points'), pk=pk)
    return Response(MaterialAllowableStressSerializer(obj).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def high_pressure_allowable_stress_list(request):
    qs = HighPressureAllowableStress.objects.prefetch_related('points').all().order_by('spec_no', 'type_grade')
    spec_no = request.query_params.get('spec_no')
    if spec_no:
        qs = qs.filter(spec_no__iexact=spec_no)
    return Response(HighPressureAllowableStressSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def casting_quality_factor_list(request):
    qs = CastingQualityFactor.objects.all().order_by('category', 'spec_no')
    return Response(CastingQualityFactorSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def weld_joint_quality_factor_list(request):
    qs = WeldJointQualityFactor.objects.all().order_by('category', 'spec_no')
    return Response(WeldJointQualityFactorSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def thermal_expansion_coefficient_list(request):
    qs = ThermalExpansionCoefficient.objects.all().order_by('material')
    return Response(ThermalExpansionCoefficientSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def modulus_of_elasticity_list(request):
    qs = ModulusOfElasticity.objects.all().order_by('material')
    return Response(ModulusOfElasticitySerializer(qs, many=True).data)


# ───────────────────────────────────────────────────────────────────
# ASME B16.5 — Pipe Flanges and Flanged Fittings
# ───────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_config_view(request):
    group_nos = list(
        MaterialGroup.objects.filter(standard__code=c.B16_5_STANDARD_CODE)
        .order_by('family', 'group_no').values_list('group_no', flat=True)
    )
    class_numbers = sorted({
        row['class_number'] for row in DrillingTemplate.objects.filter(
            standard__code=c.B16_5_STANDARD_CODE
        ).values('class_number').distinct()
    })
    return Response({
        'standard_code': c.B16_5_STANDARD_CODE,
        'group_nos': group_nos,
        'class_numbers': class_numbers,
        'units': [{'value': v, 'label': lbl} for v, lbl in c.LENGTH_UNIT_CHOICES],
        'temp_units': [{'value': v, 'label': lbl} for v, lbl in c.TEMP_UNIT_CHOICES],
        'pressure_units': [{'value': v, 'label': lbl} for v, lbl in c.PRESSURE_UNIT_CHOICES],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_material_group_list(request):
    qs = MaterialGroup.objects.filter(standard__code=c.B16_5_STANDARD_CODE).order_by('family', 'group_no')
    family = request.query_params.get('family')
    if family:
        qs = qs.filter(family=family)
    return Response(MaterialGroupListSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_material_group_detail(request, group_no):
    obj = get_object_or_404(
        MaterialGroup.objects.filter(standard__code=c.B16_5_STANDARD_CODE).prefetch_related('designations', 'specs'),
        group_no=group_no,
    )
    return Response(MaterialGroupDetailSerializer(obj).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_rating_list(request):
    qs = PressureTemperatureRating.objects.select_related('material_group').filter(
        material_group__standard__code=c.B16_5_STANDARD_CODE
    ).order_by('material_group__group_no', 'class_number', 'temp_unit')
    group_no = request.query_params.get('group_no')
    class_number = request.query_params.get('class_number')
    temp_unit = request.query_params.get('temp_unit')
    pressure_unit = request.query_params.get('pressure_unit')
    if group_no:
        qs = qs.filter(material_group__group_no=group_no)
    if class_number:
        qs = qs.filter(class_number=class_number)
    if temp_unit:
        qs = qs.filter(temp_unit=temp_unit.upper())
    if pressure_unit:
        qs = qs.filter(pressure_unit=pressure_unit)
    return Response(PressureTemperatureRatingSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_drilling_template_list(request):
    qs = DrillingTemplate.objects.filter(standard__code=c.B16_5_STANDARD_CODE).order_by('class_number', 'unit', 'id')
    class_number = request.query_params.get('class_number')
    unit = request.query_params.get('unit')
    if class_number:
        qs = qs.filter(class_number=class_number)
    if unit:
        qs = qs.filter(unit=unit)
    return Response(DrillingTemplateSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_flange_dimension_list(request):
    qs = FlangeDimension.objects.filter(standard__code=c.B16_5_STANDARD_CODE).order_by('class_number', 'unit', 'id')
    class_number = request.query_params.get('class_number')
    unit = request.query_params.get('unit')
    if class_number:
        qs = qs.filter(class_number=class_number)
    if unit:
        qs = qs.filter(unit=unit)
    return Response(FlangeDimensionSerializer(qs, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def b165_bolting_recommendation_list(request):
    qs = FlangeBoltingRecommendation.objects.filter(standard__code=c.B16_5_STANDARD_CODE).order_by('id')
    return Response(FlangeBoltingRecommendationSerializer(qs, many=True).data)
