"""
Idempotent loader for the ASME B31.3 (Process Piping) reference dataset.

Usage:
    python manage.py load_asme_b31_3            # load once (no-op if already loaded)
    python manage.py load_asme_b31_3 --reload    # wipe + reload this Standard's rows

Source file: apps/valve_standards/data/consolidated_asme_b31_3.json (bundled
in-repo — produced by the one-off PDF extraction pipeline).
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.valve_standards import constants as c
from apps.valve_standards.models import (
    Standard,
    MaterialAllowableStress,
    MaterialAllowableStressPoint,
    HighPressureAllowableStress,
    HighPressureAllowableStressPoint,
    CastingQualityFactor,
    WeldJointQualityFactor,
    ThermalExpansionCoefficient,
    ModulusOfElasticity,
)


class Command(BaseCommand):
    help = "Load the ASME B31.3 consolidated reference dataset into the valve_standards tables."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reload', action='store_true',
            help="Delete existing rows for this Standard first, then reload from scratch.",
        )

    def handle(self, *args, **options):
        data_path = Path(__file__).resolve().parents[2] / c.B31_3_SEED_DATA_RELATIVE_PATH
        if not data_path.exists():
            self.stderr.write(self.style.ERROR(f"Seed file not found: {data_path}"))
            return

        with open(data_path, encoding='utf-8') as f:
            data = json.load(f)

        standard, created = Standard.objects.get_or_create(
            code=c.B31_3_STANDARD_CODE,
            defaults={'title': c.B31_3_STANDARD_TITLE, 'edition_year': c.B31_3_STANDARD_EDITION_YEAR},
        )

        if not created and not options['reload']:
            existing = MaterialAllowableStress.objects.filter(standard=standard).count()
            if existing:
                self.stdout.write(self.style.WARNING(
                    f"{standard.code} already loaded ({existing} material stress rows). "
                    "Use --reload to wipe and reload."
                ))
                return

        if options['reload']:
            self.stdout.write("Reloading — deleting existing rows for this Standard...")
            MaterialAllowableStress.objects.filter(standard=standard).delete()  # cascades points
            HighPressureAllowableStress.objects.filter(standard=standard).delete()
            CastingQualityFactor.objects.filter(standard=standard).delete()
            WeldJointQualityFactor.objects.filter(standard=standard).delete()
            ThermalExpansionCoefficient.objects.filter(standard=standard).delete()
            ModulusOfElasticity.objects.filter(standard=standard).delete()

        with transaction.atomic():
            n_mas, n_mas_pts = self._load_material_allowable_stress(standard, data['material_allowable_stress'])
            n_hpas, n_hpas_pts = self._load_high_pressure_allowable_stress(standard, data['high_pressure_allowable_stress'])
            n_ec = self._load_casting_quality_factor(standard, data['casting_quality_factor'])
            n_ej = self._load_weld_joint_quality_factor(standard, data['weld_joint_quality_factor'])
            n_c1 = self._load_thermal_expansion(standard, data['thermal_expansion_coefficient'])
            n_c6 = self._load_modulus(standard, data['modulus_of_elasticity'])

        self.stdout.write(self.style.SUCCESS(
            "Loaded ASME B31.3 reference data:\n"
            f"  material_allowable_stress            {n_mas} rows / {n_mas_pts} temp points\n"
            f"  high_pressure_allowable_stress        {n_hpas} rows / {n_hpas_pts} temp points\n"
            f"  casting_quality_factor                {n_ec}\n"
            f"  weld_joint_quality_factor             {n_ej}\n"
            f"  thermal_expansion_coefficient         {n_c1}\n"
            f"  modulus_of_elasticity                 {n_c6}"
        ))

    def _load_material_allowable_stress(self, standard, rows):
        parents = [
            MaterialAllowableStress(
                standard=standard, category=r.get('category') or '', composition=r.get('composition', ''),
                product_form=r.get('product_form', ''), spec_no=r['spec_no'], type_grade=r.get('type_grade', ''),
                uns_no=r.get('uns_no', ''), class_condition=r.get('class_condition', ''), size=r.get('size', ''),
                p_no=r.get('p_no', ''), notes=r.get('notes', ''), min_temp=r.get('min_temp', ''),
                tensile_ksi=r.get('tensile_ksi'), yield_ksi=r.get('yield_ksi'),
            )
            for r in rows
        ]
        created = MaterialAllowableStress.objects.bulk_create(parents, batch_size=c.BULK_CREATE_BATCH_SIZE)
        pts = [
            MaterialAllowableStressPoint(material=obj, temp_f=p['temp_f'], allowable_stress_ksi=p['allowable_stress_ksi'])
            for obj, r in zip(created, rows) for p in r['points']
        ]
        MaterialAllowableStressPoint.objects.bulk_create(pts, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(rows), len(pts)

    def _load_high_pressure_allowable_stress(self, standard, rows):
        parents = [
            HighPressureAllowableStress(
                standard=standard, category=r.get('category') or '', composition=r.get('composition', ''),
                product_form=r.get('product_form', ''), spec_no=r['spec_no'], type_grade=r.get('type_grade', ''),
                uns_no=r.get('uns_no', ''), p_no=r.get('p_no', ''), notes=r.get('notes', ''),
                tensile_ksi=r.get('tensile_ksi'), yield_ksi=r.get('yield_ksi'),
            )
            for r in rows
        ]
        created = HighPressureAllowableStress.objects.bulk_create(parents, batch_size=c.BULK_CREATE_BATCH_SIZE)
        pts = [
            HighPressureAllowableStressPoint(material=obj, temp_f=p['temp_f'], allowable_stress_ksi=p['allowable_stress_ksi'])
            for obj, r in zip(created, rows) for p in r['points']
        ]
        HighPressureAllowableStressPoint.objects.bulk_create(pts, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(rows), len(pts)

    def _load_casting_quality_factor(self, standard, rows):
        objs = [
            CastingQualityFactor(standard=standard, category=r.get('category') or '', spec_no=r['spec_no'],
                                  description=r['description'], ec=r['ec'], notes=r.get('notes', ''))
            for r in rows
        ]
        CastingQualityFactor.objects.bulk_create(objs)
        return len(objs)

    def _load_weld_joint_quality_factor(self, standard, rows):
        objs = [
            WeldJointQualityFactor(standard=standard, category=r.get('category') or '', spec_no=r['spec_no'],
                                    class_or_type=r.get('class_or_type', ''), description=r['description'],
                                    ej=r['ej'], notes=r.get('notes', ''))
            for r in rows
        ]
        WeldJointQualityFactor.objects.bulk_create(objs)
        return len(objs)

    def _load_thermal_expansion(self, standard, rows):
        objs = [
            ThermalExpansionCoefficient(standard=standard, material=r['material'],
                                         coefficient_1e6_in_per_in_f=r['coefficient_1e6_in_per_in_f'],
                                         linear_expansion_in_per_100ft=r['linear_expansion_in_per_100ft'])
            for r in rows
        ]
        ThermalExpansionCoefficient.objects.bulk_create(objs)
        return len(objs)

    def _load_modulus(self, standard, rows):
        objs = [ModulusOfElasticity(standard=standard, material=r['material'], modulus_1e6_psi=r['modulus_1e6_psi'])
                for r in rows]
        ModulusOfElasticity.objects.bulk_create(objs)
        return len(objs)
