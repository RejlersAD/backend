"""
Valve Standards — Reference Data Models
========================================

Read-only reference data extracted from ASME B16.34-2004 (Valves — Flanged,
Threaded, and Welding End). Loaded via `manage.py load_asme_b16_34` from the
bundled `data/consolidated_asme_b16_34.json`.

    Standard
      ├── MaterialGroup
      │     ├── MaterialGroupDesignation   (nominal chemical composition)
      │     ├── MaterialGroupSpec          (ASTM spec/grade per product form)
      │     └── PressureTemperatureRating  (Table 2 — the big one, 24k+ rows)
      ├── Group4BoltingMaterial
      ├── WallThicknessByDiameter          (Table 3)
      ├── WallThicknessSocketweldThreaded  (Table 4)
      ├── NpsToInsideDiameter              (Appendix A-1)
      └── ReferenceStandardCitation        (Appendix VIII)

`Standard` exists so a future standard (e.g. ASME B16.5) can be added without
a schema redesign — every table below is scoped to a Standard.
"""
from __future__ import annotations

from django.db import models

from . import constants as c


class Standard(models.Model):
    code = models.CharField(max_length=32, unique=True, help_text="e.g. 'ASME_B16_34'")
    title = models.CharField(max_length=255)
    edition_year = models.PositiveSmallIntegerField()

    class Meta:
        db_table = 'valve_standards_standard'

    def __str__(self):
        return f"{self.code} ({self.edition_year})"


class MaterialGroup(models.Model):
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='material_groups')
    group_no = models.CharField(max_length=8, help_text="e.g. '1.1', '2.8', '3.18'")
    family = models.SmallIntegerField(choices=c.FAMILY_CHOICES)
    family_name = models.CharField(max_length=32)

    class Meta:
        db_table = 'valve_standards_material_group'
        ordering = ['family', 'group_no']
        constraints = [
            # group_no is NOT globally unique — the same numbering (e.g.
            # '1.1') is reused across ASME B16 standards (B16.34, B16.5),
            # each owning its own MaterialGroup row scoped by `standard`.
            models.UniqueConstraint(fields=['standard', 'group_no'], name='uq_material_group_standard_group_no'),
        ]

    def __str__(self):
        return self.group_no


class MaterialGroupDesignation(models.Model):
    material_group = models.ForeignKey(MaterialGroup, on_delete=models.CASCADE, related_name='designations')
    seq = models.SmallIntegerField()
    composition = models.CharField(max_length=64, help_text="e.g. 'C-Mn-Si'")

    class Meta:
        db_table = 'valve_standards_material_group_designation'
        constraints = [
            models.UniqueConstraint(fields=['material_group', 'seq'], name='uq_designation_group_seq'),
        ]
        ordering = ['material_group_id', 'seq']


class MaterialGroupSpec(models.Model):
    material_group = models.ForeignKey(MaterialGroup, on_delete=models.CASCADE, related_name='specs')
    product_form = models.CharField(max_length=16, choices=c.PRODUCT_FORM_CHOICES)
    seq = models.SmallIntegerField()
    spec_no = models.CharField(max_length=32, help_text="e.g. 'A 350'")
    grade = models.CharField(max_length=32, blank=True, default='')

    class Meta:
        db_table = 'valve_standards_material_group_spec'
        constraints = [
            models.UniqueConstraint(
                fields=['material_group', 'product_form', 'seq'], name='uq_spec_group_form_seq'
            ),
        ]
        ordering = ['material_group_id', 'product_form', 'seq']


class Group4BoltingMaterial(models.Model):
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='bolting_materials')
    seq = models.SmallIntegerField(unique=True)
    spec_no = models.CharField(max_length=32)
    grade = models.CharField(max_length=64, blank=True, default='')
    notes = models.JSONField(default=list, blank=True, help_text="Footnote numbers, e.g. [2, 3]")

    class Meta:
        db_table = 'valve_standards_group4_bolting_material'
        ordering = ['seq']


class PressureTemperatureRating(models.Model):
    """Table 2 — pressure-temperature ratings. ~24k rows, the largest table."""
    material_group = models.ForeignKey(MaterialGroup, on_delete=models.CASCADE, related_name='ratings')
    class_section = models.CharField(max_length=1, choices=c.CLASS_SECTION_CHOICES)
    class_type = models.CharField(max_length=16, help_text="'Standard' or 'Special'")
    class_number = models.SmallIntegerField(help_text="150,300,600,900,1500,2500,4500")
    temp_label = models.CharField(max_length=32, help_text="e.g. '-29 to 38' or '500'")
    temp_unit = models.CharField(max_length=1, choices=c.TEMP_UNIT_CHOICES)
    pressure = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="NULL where the standard has no established rating at this point",
    )
    pressure_unit = models.CharField(max_length=8, choices=c.PRESSURE_UNIT_CHOICES)

    class Meta:
        db_table = 'valve_standards_pt_rating'
        indexes = [
            models.Index(
                fields=['material_group', 'class_number', 'class_section'],
                name='idx_rating_group_class_section',
            ),
        ]


class WallThicknessByDiameter(models.Model):
    """Table 3 — valve body minimum wall thickness (tm) by inside diameter."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='wall_thickness_rows')
    unit = models.CharField(max_length=2, choices=c.LENGTH_UNIT_CHOICES)
    inside_dia_d = models.DecimalField(max_digits=10, decimal_places=3)
    class_number = models.SmallIntegerField()
    min_wall_thickness_tm = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        db_table = 'valve_standards_wall_thickness_by_diameter'
        indexes = [
            models.Index(fields=['unit', 'class_number'], name='idx_wtbd_unit_class'),
        ]
        ordering = ['unit', 'inside_dia_d']


class WallThicknessSocketweldThreaded(models.Model):
    """Table 4 — minimum wall thickness for socket-welding and threaded ends."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='socketweld_rows')
    nps = models.CharField(max_length=8, help_text="e.g. '1-1/4'")
    class_group = models.CharField(max_length=16, help_text="'150_300','600','800','900','1500','2500','4500'")
    mm = models.DecimalField(max_digits=10, decimal_places=3)
    inch = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        db_table = 'valve_standards_wall_thickness_socketweld_threaded'
        ordering = ['id']


class NpsToInsideDiameter(models.Model):
    """Appendix A-1 — NPS/DN to inside diameter."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='nps_to_id_rows')
    nps = models.CharField(max_length=8)
    dn = models.SmallIntegerField()
    class_number = models.SmallIntegerField()
    mm = models.DecimalField(max_digits=10, decimal_places=3)
    inch = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        db_table = 'valve_standards_nps_to_inside_diameter'
        indexes = [
            models.Index(fields=['class_number'], name='idx_nps_id_class'),
        ]
        ordering = ['dn']


class ReferenceStandardCitation(models.Model):
    """Appendix VIII — referenced standards/specifications."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='citations')
    citation = models.CharField(max_length=255)

    class Meta:
        db_table = 'valve_standards_reference_citation'
        ordering = ['citation']


# ─────────────────────────────────────────────────────────────────────────────
# ASME B31.3 (Process Piping) — allowable stresses, quality factors, physical
# properties. Scoped to the same Standard model (code='ASME_B31_3').
#
#     Standard
#       ├── MaterialAllowableStress        (Table A-1/A-2 — metals + bolting)
#       │     └── MaterialAllowableStressPoint  (temp -> allowable stress)
#       ├── HighPressureAllowableStress    (Appendix K, Table K-1)
#       │     └── HighPressureAllowableStressPoint
#       ├── CastingQualityFactor           (Table A-1A, Ec)
#       ├── WeldJointQualityFactor         (Table A-1B, Ej)
#       ├── ThermalExpansionCoefficient    (Table C-1)
#       └── ModulusOfElasticity            (Table C-6)
# ─────────────────────────────────────────────────────────────────────────────
class MaterialAllowableStress(models.Model):
    """Table A-1 (metals) / Table A-2 (bolting) — one row per material line item."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='material_allowable_stresses')
    category = models.CharField(max_length=128, blank=True, default='', help_text="e.g. 'Carbon Steel — Pipes and Tubes'")
    composition = models.CharField(max_length=128, blank=True, default='')
    product_form = models.CharField(max_length=32, blank=True, default='', help_text="e.g. 'Bolts', 'Tube', 'Forgings'")
    spec_no = models.CharField(max_length=32, help_text="e.g. 'A53'")
    type_grade = models.CharField(max_length=32, blank=True, default='')
    uns_no = models.CharField(max_length=32, blank=True, default='')
    class_condition = models.CharField(max_length=64, blank=True, default='')
    size = models.CharField(max_length=64, blank=True, default='')
    p_no = models.CharField(max_length=16, blank=True, default='')
    notes = models.CharField(max_length=64, blank=True, default='')
    min_temp = models.CharField(max_length=16, blank=True, default='', help_text="numeric °F or a lettered curve code")
    tensile_ksi = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    yield_ksi = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = 'valve_standards_b313_material_allowable_stress'
        indexes = [
            models.Index(fields=['spec_no', 'type_grade'], name='idx_b313_mas_spec_grade'),
        ]


class MaterialAllowableStressPoint(models.Model):
    material = models.ForeignKey(MaterialAllowableStress, on_delete=models.CASCADE, related_name='points')
    temp_f = models.SmallIntegerField()
    allowable_stress_ksi = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        db_table = 'valve_standards_b313_material_allowable_stress_point'
        constraints = [
            models.UniqueConstraint(fields=['material', 'temp_f'], name='uq_b313_mas_point_temp'),
        ]
        ordering = ['temp_f']


class HighPressureAllowableStress(models.Model):
    """Appendix K, Table K-1 — allowable stresses for high-pressure piping."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='high_pressure_allowable_stresses')
    category = models.CharField(max_length=128, blank=True, default='')
    composition = models.CharField(max_length=128, blank=True, default='')
    product_form = models.CharField(max_length=32, blank=True, default='')
    spec_no = models.CharField(max_length=32)
    type_grade = models.CharField(max_length=32, blank=True, default='')
    uns_no = models.CharField(max_length=32, blank=True, default='')
    p_no = models.CharField(max_length=16, blank=True, default='')
    notes = models.CharField(max_length=64, blank=True, default='')
    tensile_ksi = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    yield_ksi = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = 'valve_standards_b313_high_pressure_allowable_stress'
        indexes = [
            models.Index(fields=['spec_no', 'type_grade'], name='idx_b313_hpas_spec_grade'),
        ]


class HighPressureAllowableStressPoint(models.Model):
    material = models.ForeignKey(HighPressureAllowableStress, on_delete=models.CASCADE, related_name='points')
    temp_f = models.SmallIntegerField()
    allowable_stress_ksi = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        db_table = 'valve_standards_b313_high_pressure_allowable_stress_point'
        constraints = [
            models.UniqueConstraint(fields=['material', 'temp_f'], name='uq_b313_hpas_point_temp'),
        ]
        ordering = ['temp_f']


class CastingQualityFactor(models.Model):
    """Table A-1A — Basic Casting Quality Factors, Ec."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='casting_quality_factors')
    category = models.CharField(max_length=64, blank=True, default='', help_text="e.g. 'Iron', 'Carbon Steel'")
    spec_no = models.CharField(max_length=32)
    description = models.CharField(max_length=255)
    ec = models.DecimalField(max_digits=4, decimal_places=2)
    notes = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'valve_standards_b313_casting_quality_factor'


class WeldJointQualityFactor(models.Model):
    """Table A-1B — Basic Quality Factors for Longitudinal Weld Joints, Ej."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='weld_joint_quality_factors')
    category = models.CharField(max_length=64, blank=True, default='')
    spec_no = models.CharField(max_length=32)
    class_or_type = models.CharField(max_length=32, blank=True, default='')
    description = models.CharField(max_length=255)
    ej = models.DecimalField(max_digits=4, decimal_places=2)
    notes = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'valve_standards_b313_weld_joint_quality_factor'


class ThermalExpansionCoefficient(models.Model):
    """Table C-1 — Thermal Expansion Data (metals, US units).
    The two 17-point temperature series are stored as JSON (small table,
    17 fixed temperature keys — normalizing to a child table isn't worth it
    at this size, unlike the two big stress tables above)."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='thermal_expansion_coefficients')
    material = models.CharField(max_length=128)
    coefficient_1e6_in_per_in_f = models.JSONField(help_text="{'-325': 5.5, '70': 6.4, ...}")
    linear_expansion_in_per_100ft = models.JSONField()

    class Meta:
        db_table = 'valve_standards_b313_thermal_expansion_coefficient'


class ModulusOfElasticity(models.Model):
    """Table C-6 — Moduli of Elasticity for Metals (US units)."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='moduli_of_elasticity')
    material = models.CharField(max_length=128)
    modulus_1e6_psi = models.JSONField(help_text="{'-425': 31.9, '70': 29.4, ...}")

    class Meta:
        db_table = 'valve_standards_b313_modulus_of_elasticity'


# ─────────────────────────────────────────────────────────────────────────────
# ASME B16.5 (Pipe Flanges and Flanged Fittings) — same Standard-scoped
# pattern. Material composition/spec data reuses MaterialGroup /
# MaterialGroupDesignation / MaterialGroupSpec (Table 1A); PT ratings reuse
# PressureTemperatureRating (Tables 2-1.1..2-3.19 / II-2-1.1..II-2-3.19),
# with class_section fixed to CLASS_SECTION_STANDARD since B16.5 has no
# Standard/Special class distinction (a B16.34-only concept).
#
#     Standard
#       ├── DrillingTemplate              (Tables 7/10/13/15/17/19/21 + II-)
#       ├── FlangeDimension               (Tables 8/11/14/16/18/20/22 + II-)
#       └── FlangeBoltingRecommendation   (Table 1C, static 7 rows)
#
# NOT extracted (see extract_asme_b16_5.py docstring for why): Table 4/5
# facing dimensions, Table 1B bolting list, Table 6 reducing flanges, and
# Table 9/12 flanged-fitting dimensions.
# ─────────────────────────────────────────────────────────────────────────────
class DrillingTemplate(models.Model):
    """Tables 7/10/13/15/17/19/21 (+ II- inch variants) — bolt circle/hole
    drilling dimensions and stud/machine bolt lengths, by class and NPS."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='drilling_templates')
    class_number = models.SmallIntegerField(help_text="150,300,400,600,900,1500,2500")
    unit = models.CharField(max_length=2, choices=c.LENGTH_UNIT_CHOICES)
    nps = models.CharField(max_length=8, help_text="e.g. '1-1/4'")
    outside_diameter_o = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    bolt_circle_w = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    bolt_hole_diameter = models.CharField(max_length=16, blank=True, default='', help_text="e.g. '5/8' (inch, printed as a fraction)")
    num_bolts = models.SmallIntegerField(null=True, blank=True)
    bolt_diameter = models.CharField(max_length=16, blank=True, default='', help_text="e.g. '1/2' (inch, printed as a fraction)")
    # The 3 "Length of Bolts, L" columns — labels vary by class (raised-face
    # height 2mm vs 7mm; 3rd column is Machine Bolts, Ring Joint, or M&F/T&G
    # depending on class), so kept generic rather than fixed-semantic fields.
    bolt_length_1 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    bolt_length_2 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    bolt_length_3 = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='', help_text="e.g. referral to another class's table for this NPS")

    class Meta:
        db_table = 'valve_standards_b165_drilling_template'
        indexes = [
            models.Index(fields=['class_number', 'unit'], name='idx_b165_drilling_class_unit'),
        ]
        ordering = ['class_number', 'unit', 'id']


class FlangeDimension(models.Model):
    """Tables 8/11/14/16/18/20/22 (+ II- inch variants) — flange body
    dimensions by class and NPS. Column count/labels vary by class (14 vs 15
    columns; some classes add a counterbore dimension Q), so everything
    beyond the universally-present outside diameter is kept as an ordered
    raw `values` list rather than fixed, possibly-mislabeled field names."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='flange_dimensions')
    class_number = models.SmallIntegerField(help_text="150,300,400,600,900,1500,2500")
    unit = models.CharField(max_length=2, choices=c.LENGTH_UNIT_CHOICES)
    nps = models.CharField(max_length=8, help_text="e.g. '1-1/4'")
    outside_diameter_o = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    values = models.JSONField(null=True, blank=True, help_text="Ordered raw dimension cells beyond O, as printed")
    note = models.CharField(max_length=255, blank=True, default='', help_text="e.g. referral to another class's table for this NPS")

    class Meta:
        db_table = 'valve_standards_b165_flange_dimension'
        indexes = [
            models.Index(fields=['class_number', 'unit'], name='idx_b165_flange_dim_class_unit'),
        ]
        ordering = ['class_number', 'unit', 'id']


class FlangeBoltingRecommendation(models.Model):
    """Table 1C — flange bolting dimensional recommendations (static, 7 rows,
    no class/NPS/unit breakdown)."""
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE, related_name='flange_bolting_recommendations')
    product = models.CharField(max_length=64, help_text="e.g. 'Stud bolts', 'Nuts smaller than 3/4 in.'")
    carbon_steel = models.CharField(max_length=255)
    alloy_steel = models.CharField(max_length=255)

    class Meta:
        db_table = 'valve_standards_b165_flange_bolting_recommendation'
