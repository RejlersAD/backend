"""
Idempotent loader for the ASME B16.34 reference dataset.

Usage:
    python manage.py load_asme_b16_34            # load once (no-op if already loaded)
    python manage.py load_asme_b16_34 --reload    # wipe + reload this Standard's rows

Source file: apps/valve_standards/data/consolidated_asme_b16_34.json (bundled
in-repo — produced by the one-off PDF extraction pipeline, not a runtime
dependency on any external path).
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.valve_standards import constants as c
from apps.valve_standards.models import (
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
)


class Command(BaseCommand):
    help = "Load the ASME B16.34 consolidated reference dataset into the valve_standards tables."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reload', action='store_true',
            help="Delete existing rows for this Standard first, then reload from scratch.",
        )

    def handle(self, *args, **options):
        data_path = Path(__file__).resolve().parents[2] / c.SEED_DATA_RELATIVE_PATH
        if not data_path.exists():
            self.stderr.write(self.style.ERROR(f"Seed file not found: {data_path}"))
            return

        with open(data_path, encoding='utf-8') as f:
            data = json.load(f)

        standard, created = Standard.objects.get_or_create(
            code=c.DEFAULT_STANDARD_CODE,
            defaults={
                'title': c.DEFAULT_STANDARD_TITLE,
                'edition_year': c.DEFAULT_STANDARD_EDITION_YEAR,
            },
        )

        if not created and not options['reload']:
            existing = MaterialGroup.objects.filter(standard=standard).count()
            if existing:
                self.stdout.write(self.style.WARNING(
                    f"{standard.code} already loaded ({existing} material groups). "
                    "Use --reload to wipe and reload."
                ))
                return

        if options['reload']:
            self.stdout.write("Reloading — deleting existing rows for this Standard...")
            MaterialGroup.objects.filter(standard=standard).delete()  # cascades designations/specs/ratings
            Group4BoltingMaterial.objects.filter(standard=standard).delete()
            WallThicknessByDiameter.objects.filter(standard=standard).delete()
            WallThicknessSocketweldThreaded.objects.filter(standard=standard).delete()
            NpsToInsideDiameter.objects.filter(standard=standard).delete()
            ReferenceStandardCitation.objects.filter(standard=standard).delete()

        with transaction.atomic():
            group_map = self._load_material_groups(standard, data['material_groups'])
            n_designations = self._load_designations(group_map, data['material_group_designations'])
            n_specs = self._load_specs(group_map, data['material_group_specs'])
            n_bolting = self._load_bolting(standard, data['group4_bolting_materials'])
            n_ratings = self._load_ratings(group_map, data['pt_ratings'])
            n_wtbd = self._load_wall_thickness_by_diameter(standard, data['wall_thickness_by_diameter'])
            n_wtst = self._load_wall_thickness_socketweld(standard, data['wall_thickness_socketweld_threaded'])
            n_nps = self._load_nps_to_id(standard, data['nps_to_inside_diameter'])
            n_refs = self._load_reference_standards(standard, data['reference_standards'])

        self.stdout.write(self.style.SUCCESS(
            "Loaded ASME B16.34 reference data:\n"
            f"  material_groups                     {len(group_map)}\n"
            f"  material_group_designations          {n_designations}\n"
            f"  material_group_specs                 {n_specs}\n"
            f"  group4_bolting_materials              {n_bolting}\n"
            f"  pt_ratings                           {n_ratings}\n"
            f"  wall_thickness_by_diameter           {n_wtbd}\n"
            f"  wall_thickness_socketweld_threaded    {n_wtst}\n"
            f"  nps_to_inside_diameter                {n_nps}\n"
            f"  reference_standards                  {n_refs}"
        ))

    # ── per-section loaders ──────────────────────────────────────────────
    def _load_material_groups(self, standard, rows):
        group_map = {}
        for row in rows:
            obj, _ = MaterialGroup.objects.get_or_create(
                group_no=row['group_no'],
                defaults={
                    'standard': standard,
                    'family': row['group_family'],
                    'family_name': row['group_family_name'],
                },
            )
            group_map[row['group_no']] = obj
        return group_map

    def _load_designations(self, group_map, rows):
        objs = [
            MaterialGroupDesignation(
                material_group=group_map[r['group_no']], seq=r['seq'], composition=r['composition'],
            )
            for r in rows
        ]
        MaterialGroupDesignation.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE, ignore_conflicts=True)
        return len(objs)

    def _load_specs(self, group_map, rows):
        objs = [
            MaterialGroupSpec(
                material_group=group_map[r['group_no']],
                product_form=r['product_form'],
                seq=r['seq'],
                spec_no=r['spec_no'],
                grade=r.get('grade', ''),
            )
            for r in rows
        ]
        MaterialGroupSpec.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE, ignore_conflicts=True)
        return len(objs)

    def _load_bolting(self, standard, rows):
        objs = [
            Group4BoltingMaterial(
                standard=standard, seq=r['seq'], spec_no=r['spec_no'],
                grade=r.get('grade', ''), notes=r.get('notes', []),
            )
            for r in rows
        ]
        Group4BoltingMaterial.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE, ignore_conflicts=True)
        return len(objs)

    def _load_ratings(self, group_map, rows):
        objs = [
            PressureTemperatureRating(
                material_group=group_map[r['group_no']],
                class_section=r['class_section'],
                class_type=r['class_type'],
                class_number=r['class_number'],
                temp_label=r['temp_label'],
                temp_unit=r['temp_unit'],
                pressure=r['pressure'],
                pressure_unit=r['pressure_unit'],
            )
            for r in rows
        ]
        PressureTemperatureRating.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    def _load_wall_thickness_by_diameter(self, standard, rows):
        objs = [
            WallThicknessByDiameter(
                standard=standard, unit=r['unit'], inside_dia_d=r['inside_dia_d'],
                class_number=r['class_number'], min_wall_thickness_tm=r['min_wall_thickness_tm'],
            )
            for r in rows
        ]
        WallThicknessByDiameter.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    def _load_wall_thickness_socketweld(self, standard, rows):
        objs = [
            WallThicknessSocketweldThreaded(
                standard=standard, nps=r['nps'], class_group=r['class_group'],
                mm=r['mm'], inch=r['in_'],
            )
            for r in rows
        ]
        WallThicknessSocketweldThreaded.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    def _load_nps_to_id(self, standard, rows):
        objs = [
            NpsToInsideDiameter(
                standard=standard, nps=r['nps'], dn=r['dn'], class_number=r['class_number'],
                mm=r['mm'], inch=r['in_'],
            )
            for r in rows
        ]
        NpsToInsideDiameter.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    def _load_reference_standards(self, standard, rows):
        objs = [ReferenceStandardCitation(standard=standard, citation=r['citation']) for r in rows]
        ReferenceStandardCitation.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)
