"""
Loader for the ASME B16.5-2017 reference dataset.

Unlike load_asme_b16_34 / load_asme_b31_3, the source JSON is NOT bundled
under this app's data/ directory — per project instruction, the extracted
supporting file lives next to the source PDF instead. Point --source at it
(or set the ASME_B16_5_DATA_PATH env var); there is no on-disk default here.

Usage:
    python manage.py load_asme_b16_5 --source /path/to/asme_b16_5_extracted.json
    python manage.py load_asme_b16_5 --source ... --reload
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.valve_standards import constants as c
from apps.valve_standards.models import (
    Standard,
    MaterialGroup,
    MaterialGroupDesignation,
    MaterialGroupSpec,
    PressureTemperatureRating,
    DrillingTemplate,
    FlangeDimension,
    FlangeBoltingRecommendation,
)


class Command(BaseCommand):
    help = "Load the ASME B16.5 extracted reference dataset into the valve_standards tables."

    def add_arguments(self, parser):
        parser.add_argument(
            '--source', type=str, default=None,
            help=f"Path to the extracted JSON (falls back to ${c.B16_5_SOURCE_DATA_ENV_VAR} env var).",
        )
        parser.add_argument('--reload', action='store_true', help="Delete existing rows for this Standard first, then reload.")

    def handle(self, *args, **options):
        source = options['source'] or os.environ.get(c.B16_5_SOURCE_DATA_ENV_VAR)
        if not source:
            raise CommandError(
                f"No source file given. Pass --source /path/to/asme_b16_5_extracted.json "
                f"or set ${c.B16_5_SOURCE_DATA_ENV_VAR}."
            )
        data_path = Path(source)
        if not data_path.exists():
            raise CommandError(f"Source file not found: {data_path}")

        with open(data_path, encoding='utf-8') as f:
            data = json.load(f)

        standard, created = Standard.objects.get_or_create(
            code=c.B16_5_STANDARD_CODE,
            defaults={'title': c.B16_5_STANDARD_TITLE, 'edition_year': c.B16_5_STANDARD_EDITION_YEAR},
        )

        if not created and not options['reload']:
            existing = MaterialGroup.objects.filter(standard=standard).count()
            if existing:
                self.stdout.write(self.style.WARNING(
                    f"{standard.code} already loaded ({existing} material groups). Use --reload to wipe and reload."
                ))
                return

        if options['reload']:
            self.stdout.write("Reloading — deleting existing rows for this Standard...")
            MaterialGroup.objects.filter(standard=standard).delete()  # cascades designations/specs/ratings
            DrillingTemplate.objects.filter(standard=standard).delete()
            FlangeDimension.objects.filter(standard=standard).delete()
            FlangeBoltingRecommendation.objects.filter(standard=standard).delete()

        with transaction.atomic():
            group_map, n_desig, n_spec = self._load_material_groups(standard, data['material_groups'])
            n_ratings = self._load_pt_ratings(group_map, data['pt_ratings'])
            n_drilling = self._load_drilling_templates(standard, data['drilling_templates'])
            n_flange = self._load_flange_dimensions(standard, data['flange_dimensions'])
            n_bolting = self._load_bolting_recommendations(standard, data['flange_bolting_recommendations'])

        self.stdout.write(self.style.SUCCESS(
            "Loaded ASME B16.5 reference data:\n"
            f"  material_groups                {len(group_map)}\n"
            f"  material_group_designations     {n_desig}\n"
            f"  material_group_specs            {n_spec}\n"
            f"  pt_ratings                      {n_ratings}\n"
            f"  drilling_templates              {n_drilling}\n"
            f"  flange_dimensions               {n_flange}\n"
            f"  flange_bolting_recommendations  {n_bolting}"
        ))

    # ── material groups / designations / specs (Table 1A) ──────────────────
    def _load_material_groups(self, standard, rows):
        group_map: dict[str, MaterialGroup] = {}
        seq_by_group: dict[str, int] = {}
        spec_seq: dict[tuple[str, str], int] = {}
        designations = []
        specs = []

        for row in rows:
            group_no = row['group_no']
            if group_no not in group_map:
                family = int(group_no.split('.')[0])
                group_map[group_no], _ = MaterialGroup.objects.get_or_create(
                    standard=standard, group_no=group_no,
                    defaults={'family': family, 'family_name': c.FAMILY_NAMES.get(family, '')},
                )
            group = group_map[group_no]

            seq_by_group[group_no] = seq_by_group.get(group_no, 0) + 1
            designations.append(MaterialGroupDesignation(
                material_group=group, seq=seq_by_group[group_no], composition=row['composition'][:64],
            ))

            for product_form, raw_spec in (
                (c.PRODUCT_FORM_FORGING, row.get('forging_spec')),
                (c.PRODUCT_FORM_CASTING, row.get('casting_spec')),
                (c.PRODUCT_FORM_PLATE, row.get('plate_spec')),
            ):
                if not raw_spec:
                    continue
                key = (group_no, product_form)
                spec_seq[key] = spec_seq.get(key, 0) + 1
                spec_no, _, grade = raw_spec.partition(' ')
                specs.append(MaterialGroupSpec(
                    material_group=group, product_form=product_form, seq=spec_seq[key],
                    spec_no=spec_no[:32], grade=grade[:32],
                ))

        MaterialGroupDesignation.objects.bulk_create(designations, batch_size=c.BULK_CREATE_BATCH_SIZE, ignore_conflicts=True)
        MaterialGroupSpec.objects.bulk_create(specs, batch_size=c.BULK_CREATE_BATCH_SIZE, ignore_conflicts=True)
        return group_map, len(designations), len(specs)

    # ── pressure-temperature ratings (Tables 2-x.x / II-2-x.x) ──────────────
    def _load_pt_ratings(self, group_map, rows):
        objs = []
        for r in rows:
            group = group_map.get(r['group_no'])
            if not group:
                continue
            objs.append(PressureTemperatureRating(
                material_group=group,
                class_section=c.CLASS_SECTION_STANDARD,  # B16.5 has no Standard/Special split (B16.34-only concept)
                class_type='Standard',
                class_number=r['class_number'],
                temp_label=r['temp_label'][:32],
                temp_unit=r['temp_unit'],
                pressure=r['pressure'],
                pressure_unit=r['pressure_unit'],
            ))
        PressureTemperatureRating.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    # ── drilling templates (Tables 7/10/13/15/17/19/21 + II-) ───────────────
    def _load_drilling_templates(self, standard, rows):
        objs = [
            DrillingTemplate(
                standard=standard, class_number=r['class_number'], unit=r['unit'], nps=r['nps'][:8],
                outside_diameter_o=r.get('outside_diameter_o'), bolt_circle_w=r.get('bolt_circle_w'),
                bolt_hole_diameter=(r.get('bolt_hole_diameter') or '')[:16],
                num_bolts=r.get('num_bolts'), bolt_diameter=(r.get('bolt_diameter') or '')[:16],
                bolt_length_1=r.get('bolt_length_1'), bolt_length_2=r.get('bolt_length_2'),
                bolt_length_3=r.get('bolt_length_3'), note=(r.get('note') or '')[:255],
            )
            for r in rows
        ]
        DrillingTemplate.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    # ── flange dimensions (Tables 8/11/14/16/18/20/22 + II-) ────────────────
    def _load_flange_dimensions(self, standard, rows):
        objs = [
            FlangeDimension(
                standard=standard, class_number=r['class_number'], unit=r['unit'], nps=r['nps'][:8],
                outside_diameter_o=r.get('outside_diameter_o'), values=r.get('values'),
                note=(r.get('note') or '')[:255],
            )
            for r in rows
        ]
        FlangeDimension.objects.bulk_create(objs, batch_size=c.BULK_CREATE_BATCH_SIZE)
        return len(objs)

    # ── flange bolting recommendations (Table 1C, static) ───────────────────
    def _load_bolting_recommendations(self, standard, rows):
        objs = [
            FlangeBoltingRecommendation(
                standard=standard, product=r['product'], carbon_steel=r['carbon_steel'], alloy_steel=r['alloy_steel'],
            )
            for r in rows
        ]
        FlangeBoltingRecommendation.objects.bulk_create(objs)
        return len(objs)
