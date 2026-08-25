from django.db import migrations


STANDARD_WORKFLOW_CODE = 'STANDARD_5_STAGE'
PROCESS_TEMPLATE_CODE = 'PROCESS_ENGINEERING_V1'


def seed_configuration(apps, schema_editor):
    WorkflowTemplate = apps.get_model('planning_intelligence', 'WorkflowTemplate')
    WorkflowStage = apps.get_model('planning_intelligence', 'WorkflowStage')
    DependencyTemplate = apps.get_model('planning_intelligence', 'EngineeringDependencyTemplate')
    DependencyRule = apps.get_model('planning_intelligence', 'EngineeringDependencyRule')
    PlanningProject = apps.get_model('planning_intelligence', 'PlanningProject')
    ProjectConfiguration = apps.get_model('planning_intelligence', 'ProjectScheduleConfiguration')

    workflow, _ = WorkflowTemplate.objects.get_or_create(
        project=None, code=STANDARD_WORKFLOW_CODE, version=1,
        defaults={
            'name': 'Standard Five-Stage Deliverable Workflow',
            'description': 'Primavera-style IFR, Company Review, IFA, Company Approval, and Final Issue workflow.',
            'status': 'active', 'is_system': True, 'is_default': True,
        },
    )
    stages = [
        (1, 'IFR', 'IFR', 10, 'Discipline Engineer', '', 30),
        (2, 'COMPANY_REVIEW', 'COMPANY REVIEW', 10, 'COMPANY', 'FS', 20),
        (3, 'IFA', 'IFA', 5, 'Discipline Engineer', 'FS', 25),
        (4, 'COMPANY_APPROVAL', 'COMPANY APPROVAL', 5, 'COMPANY', 'FS', 15),
        (5, 'FINAL_ISSUE', 'FINAL ISSUE', 1, 'Document Control', 'FS', 10),
    ]
    for sequence, code, name, duration, party, relationship, weight in stages:
        WorkflowStage.objects.get_or_create(
            template=workflow, sequence=sequence,
            defaults={
                'code': code, 'name': name,
                'activity_name_template': '{deliverable} - {stage}',
                'duration_days': duration, 'responsible_party': party,
                'activity_type': 'task', 'relationship_to_previous': relationship,
                'lag_days': 0, 'progress_weight': weight, 'is_release_gate': True,
            },
        )

    dependency, _ = DependencyTemplate.objects.get_or_create(
        project=None, code=PROCESS_TEMPLATE_CODE, version=1,
        defaults={
            'name': 'Process Engineering Dependency Network', 'discipline': 'process',
            'description': 'Digitized Process deliverable network supplied by engineering; project clones retain confirmation flags.',
            'status': 'active', 'is_system': True, 'is_default': True,
        },
    )
    edges = [
        ('CONTRACT_AWARD', 'Contract Award / Project Start', 'MILESTONE', 'PROCESS_STUDY_INSTRUCTION', 'Process Study / Process Design Instruction'),
        ('PROCESS_STUDY_INSTRUCTION', 'Process Study / Process Design Instruction', 'FINAL_ISSUE', 'PROCESS_BLOCK_DIAGRAM', 'Process Block Diagram'),
        ('PROCESS_BLOCK_DIAGRAM', 'Process Block Diagram', 'FINAL_ISSUE', 'PROCESS_CALCULATION_SIMULATION', 'Process Calculation and Simulation'),
        ('PROCESS_CALCULATION_SIMULATION', 'Process Calculation and Simulation', 'FINAL_ISSUE', 'HEAT_MASS_BALANCE', 'Heat and Mass Balance'),
        ('HEAT_MASS_BALANCE', 'Heat and Mass Balance', 'FINAL_ISSUE', 'PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'PROCESS_CONTROL_PHILOSOPHY', 'Process Control and Operating Philosophy'),
        ('PROCESS_CONTROL_PHILOSOPHY', 'Process Control and Operating Philosophy', 'FINAL_ISSUE', 'OTHER_PROCESS_PHILOSOPHIES', 'Other Process Philosophies'),
        ('OTHER_PROCESS_PHILOSOPHIES', 'Other Process Philosophies', 'FINAL_ISSUE', 'COMMISSIONING_PHILOSOPHY', 'Pre-commissioning / Commissioning Philosophy'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'UTILITY_BALANCE_DIAGRAM', 'Utility Balance and Utility Flow Diagram'),
        ('UTILITY_BALANCE_DIAGRAM', 'Utility Balance and Utility Flow Diagram', 'FINAL_ISSUE', 'SAFEGUARDING_MEMORANDUM', 'Safeguarding Memorandum'),
        ('SAFEGUARDING_MEMORANDUM', 'Safeguarding Memorandum', 'FINAL_ISSUE', 'RELIEF_LOAD_CALCULATION', 'Relief Load Calculation'),
        ('RELIEF_LOAD_CALCULATION', 'Relief Load Calculation', 'FINAL_ISSUE', 'RELIEF_FLARE_SYSTEM', 'Relief and Flare System'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'PIDS', 'P&IDs'),
        ('PIDS', 'P&IDs', 'FINAL_ISSUE', 'CAUSE_EFFECT_DIAGRAMS', 'Process Cause and Effect Diagrams'),
        ('CAUSE_EFFECT_DIAGRAMS', 'Process Cause and Effect Diagrams', 'FINAL_ISSUE', 'HAZOP', 'HAZOP'),
        ('HAZOP', 'HAZOP', 'FINAL_ISSUE', 'SIL_ASSESSMENT', 'SIL Assessment'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'LINE_SIZE_CALCULATIONS', 'Line Size Calculations'),
        ('LINE_SIZE_CALCULATIONS', 'Line Size Calculations', 'FINAL_ISSUE', 'LINE_LIST', 'Line List / Line Schedule'),
        ('LINE_LIST', 'Line List / Line Schedule', 'FINAL_ISSUE', 'TIE_IN_LIST', 'Tie-in List'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'PROCESS_EQUIPMENT_LIST', 'Process Equipment List'),
        ('PROCESS_EQUIPMENT_LIST', 'Process Equipment List', 'FINAL_ISSUE', 'EQUIPMENT_PROCESS_DATA_SHEETS', 'Equipment Process Data Sheets'),
        ('EQUIPMENT_PROCESS_DATA_SHEETS', 'Equipment Process Data Sheets', 'FINAL_ISSUE', 'INSTRUMENT_PROCESS_DATA_SHEETS', 'Instrument Process Data Sheets'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'PROCESS_DESCRIPTION', 'Process Description'),
        ('PROCESS_DESCRIPTION', 'Process Description', 'FINAL_ISSUE', 'SPECIAL_PIPING_LIST', 'Special Piping List'),
        ('PROCESS_FLOW_DIAGRAM', 'Process Flow Diagram', 'FINAL_ISSUE', 'MATERIAL_SELECTION_DIAGRAMS', 'Material Selection / Flow Diagrams'),
        ('MATERIAL_SELECTION_DIAGRAMS', 'Material Selection / Flow Diagrams', 'FINAL_ISSUE', 'MATERIAL_SELECTION_REPORT', 'Material Selection Report'),
    ]
    for sequence, edge in enumerate(edges, 1):
        predecessor_code, predecessor_name, predecessor_stage, successor_code, successor_name = edge
        DependencyRule.objects.get_or_create(
            template=dependency, predecessor_code=predecessor_code,
            predecessor_stage_code=predecessor_stage, successor_code=successor_code,
            successor_stage_code='IFR', relationship_type='FS',
            defaults={
                'sequence': sequence, 'predecessor_name': predecessor_name,
                'successor_name': successor_name, 'lag_days': 0,
                'rationale': 'Engineer-supplied Process flow; release stage requires project confirmation.',
                'source_reference': 'Process engineering workflow diagram',
                'requires_confirmation': True,
            },
        )

    for project in PlanningProject.objects.filter(is_deleted=False):
        ProjectConfiguration.objects.get_or_create(
            project=project,
            defaults={
                'workflow_template': workflow, 'dependency_template': dependency,
                'standard_task_count': 5, 'configuration_version': 1,
                'settings': {'final_issue_mode': 'task', 'date_authority': 'cpm'},
            },
        )


def remove_seed_configuration(apps, schema_editor):
    WorkflowTemplate = apps.get_model('planning_intelligence', 'WorkflowTemplate')
    DependencyTemplate = apps.get_model('planning_intelligence', 'EngineeringDependencyTemplate')
    WorkflowTemplate.objects.filter(project=None, code=STANDARD_WORKFLOW_CODE, version=1).delete()
    DependencyTemplate.objects.filter(project=None, code=PROCESS_TEMPLATE_CODE, version=1).delete()


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0013_workflow_configuration')]
    operations = [migrations.RunPython(seed_configuration, remove_seed_configuration)]
