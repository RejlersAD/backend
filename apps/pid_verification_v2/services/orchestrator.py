"""
P&ID Verification V2 — Processing Orchestrator
================================================

Redesigned processing pipeline with configuration-driven architecture.
Each processing stage is isolated, monitored, and can fail gracefully.

**Key Features**:
- Configuration-driven (uses processing_config.py)
- Real-time progress tracking
- Comprehensive error handling
- Stage-level recovery
- Performance monitoring

Author: RADAI Team
Last Updated: 2026-07-24
"""

import logging
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from .processing_config import (
    PROCESSING_STAGES,
    get_stage_config,
    is_feature_enabled,
    LOGGING_CONFIG,
    ERROR_RECOVERY,
)

logger = logging.getLogger('pidv.orchestrator')


# ===========================================================================
# SOFT-CODED: Reference data column-name aliases → canonical comparison keys
# ===========================================================================
# `PIDVReferenceData.parsed_data` rows come from arbitrary source column
# headers (Excel/CSV headers, or PDF-extracted table headers). The
# `services.comparison_engine` compare_with_* functions expect canonical
# keys (e.g. 'line_tag', 'size', 'tag', 'type'). This alias map lets us
# normalize any recognizable header variant onto those canonical keys.
REFERENCE_FIELD_ALIASES = {
    'line_list': {
        'line_tag': ['line no', 'line number', 'line tag', 'line id', 'lineno', 'linenumber'],
        'size':     ['size', 'nominal size', 'nps', 'pipe size', 'linesize'],
        'service':  ['service', 'fluid service', 'commodity', 'fluid'],
        'spec':     ['spec', 'pipe spec', 'material spec', 'pipe class', 'piping class'],
    },
    'equipment_list': {
        'tag':         ['tag', 'tag no', 'tag number', 'equipment tag', 'equipment no', 'equipment number'],
        'type':        ['type', 'equipment type'],
        'description': ['description', 'equipment description', 'name', 'equipment name'],
        'service':     ['service', 'duty'],
    },
    'instrument_index': {
        'tag':     ['tag', 'tag no', 'tag number', 'instrument tag', 'instrument no', 'instrument number'],
        'type':    ['type', 'instrument type'],
        'service': ['service'],
        'range':   ['range', 'operating range'],
    },
}


def _normalize_reference_rows(data_type: str, raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map arbitrary source column headers (from Excel/CSV/PDF-extracted
    reference data tables) onto the canonical field names expected by
    `services.comparison_engine` (e.g. 'line_tag', 'size', 'tag', 'type').

    Matching is case-insensitive, ignores punctuation/whitespace, and uses
    substring matching for aliases of 4+ characters (so real-world headers
    like 'EQPT. TAG No.' or 'LINE NUMBER' still resolve), while short
    aliases (<=3 chars, e.g. 'no', 'to') require an exact match to avoid
    false positives. Longer aliases are checked first so the most specific
    match wins.

    Rows with no recognizable key column (line_tag / tag) are dropped,
    since the comparison engine cannot match them against P&ID items.
    """
    import re

    def _clean(value: Any) -> str:
        return re.sub(r'[^a-z0-9]', '', str(value).lower())

    aliases = REFERENCE_FIELD_ALIASES.get(data_type, {})
    alias_entries = sorted(
        (
            (_clean(alias), canonical)
            for canonical, alias_list in aliases.items()
            for alias in alias_list
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    key_field = 'line_tag' if data_type == 'line_list' else 'tag'

    def _match_column(cleaned_col: str) -> Optional[str]:
        for cleaned_alias, canonical in alias_entries:
            if not cleaned_alias:
                continue
            if len(cleaned_alias) <= 3:
                if cleaned_col == cleaned_alias:
                    return canonical
            elif cleaned_alias in cleaned_col:
                return canonical
        return None

    normalized: List[Dict[str, Any]] = []
    for row in raw_rows:
        mapped: Dict[str, Any] = {}
        for col_name, value in row.items():
            if value in (None, ''):
                continue
            canonical = _match_column(_clean(col_name))
            if canonical and canonical not in mapped:
                mapped[canonical] = value
        if mapped.get(key_field):
            normalized.append(mapped)

    return normalized


# ===========================================================================
# STAGE EXECUTION RESULT
# ===========================================================================

@dataclass
class StageResult:
    """Result of a processing stage execution."""
    stage_id: str
    success: bool
    duration_seconds: float
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    """Context shared across all pipeline stages."""
    document_id: str
    document: Any  # PIDVDocument instance
    project: Any  # PIDVProject instance
    user_context: Dict[str, Any] = field(default_factory=dict)  # BYOK, settings
    
    # Accumulated results from stages
    file_path: Optional[str] = None
    segments: List[Any] = field(default_factory=list)
    extraction_data: Dict[str, Any] = field(default_factory=dict)
    graph_data: Any = None
    rule_findings: List[Any] = field(default_factory=list)
    comparison_findings: List[Any] = field(default_factory=list)
    ai_findings: List[Any] = field(default_factory=list)

    # Per-segment (multi-page) results, keyed by `drawing_id`. Each stage that
    # is page-scoped (extraction / graph / rule engine / comparison / AI)
    # loops over ALL `segments` (one per PDF page) and stores its per-page
    # output here, e.g.:
    #   segment_data[drawing_id] = {
    #       'extraction': {...}, 'extraction_summary': {...}, 'graph': <Graph>,
    #       'rule_findings': [...], 'comparison_findings': [...],
    #       'comparison_summary': {...}, 'ai_findings': [...],
    #   }
    # This lets `tasks.py` persist one `PIDVDrawing` row per page instead of
    # collapsing a multi-page P&ID document into a single drawing.
    segment_data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Performance tracking
    stage_results: List[StageResult] = field(default_factory=list)
    start_time: datetime = field(default_factory=timezone.now)
    
    def add_result(self, result: StageResult):
        """Add a stage result to the pipeline context."""
        self.stage_results.append(result)
        
    def get_total_duration(self) -> float:
        """Get total pipeline duration in seconds."""
        return (timezone.now() - self.start_time).total_seconds()
    
    def get_stage_result(self, stage_id: str) -> Optional[StageResult]:
        """Get result for a specific stage."""
        for result in self.stage_results:
            if result.stage_id == stage_id:
                return result
        return None
    
    def has_critical_failure(self) -> bool:
        """Check if any critical stage failed."""
        for result in self.stage_results:
            stage_config = get_stage_config(result.stage_id)
            if stage_config.critical and not result.success:
                return True
        return False


# ===========================================================================
# STAGE EXECUTORS
# ===========================================================================

class StageExecutor:
    """Base class for stage executors."""
    
    def __init__(self, stage_id: str):
        self.stage_id = stage_id
        self.config = get_stage_config(stage_id)
        self.logger = logging.getLogger(f'pidv.stage.{stage_id}')
    
    def execute(self, context: PipelineContext) -> StageResult:
        """
        Execute the stage with timeout, retry, and error handling.
        
        Returns:
            StageResult with success/failure status and data
        """
        start_time = time.time()
        retries = 0
        last_error = None
        
        while retries <= self.config.retry_count:
            try:
                self.logger.info(
                    f"[{self.stage_id}] Starting (attempt {retries + 1}/{self.config.retry_count + 1})"
                )
                
                # Execute the actual stage logic
                data = self._execute_impl(context)
                
                duration = time.time() - start_time
                result = StageResult(
                    stage_id=self.stage_id,
                    success=True,
                    duration_seconds=duration,
                    data=data,
                )
                
                self.logger.info(
                    f"[{self.stage_id}] Completed successfully in {duration:.2f}s"
                )
                
                return result
                
            except SoftTimeLimitExceeded:
                # Never retry a stage after the Celery soft time-limit fires —
                # the task is almost out of its time budget, so sleeping and
                # re-running the same (now proven slow) stage would only
                # guarantee a hard SIGKILL with no chance to fail cleanly.
                # Propagate immediately so the caller (process_pid_document)
                # can mark the document FAILED and let Celery retry the whole
                # task fresh instead.
                self.logger.error(f"[{self.stage_id}] Soft time limit exceeded — aborting stage retries")
                raise
                
            except Exception as exc:
                retries += 1
                last_error = str(exc)
                
                self.logger.error(
                    f"[{self.stage_id}] Failed (attempt {retries}/{self.config.retry_count + 1}): {exc}",
                    exc_info=True
                )
                
                if retries <= self.config.retry_count:
                    retry_delay = ERROR_RECOVERY['retry_delay_multiplier'] ** retries
                    retry_delay = min(retry_delay, ERROR_RECOVERY['max_retry_delay'])
                    self.logger.info(f"[{self.stage_id}] Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
        
        # All retries exhausted
        duration = time.time() - start_time
        result = StageResult(
            stage_id=self.stage_id,
            success=False,
            duration_seconds=duration,
            error=last_error,
        )
        
        return result
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        """
        Implement stage-specific logic in subclasses.
        
        Returns:
            Dict with stage output data
        """
        raise NotImplementedError("Subclasses must implement _execute_impl")


# ===========================================================================
# CONCRETE STAGE EXECUTORS
# ===========================================================================

class FileValidationStage(StageExecutor):
    """Stage 1: Validate file format and integrity."""
    
    def __init__(self):
        super().__init__('file_validation')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification_v2.tasks import _resolve_file_path
        
        # Resolve file path (handles both local and S3)
        file_path = _resolve_file_path(context.document)
        context.file_path = file_path
        
        # Basic validation
        import os
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise ValueError("File is empty")
        
        return {
            'file_path': file_path,
            'file_size_bytes': file_size,
            'validated': True,
        }


class SegmentationStage(StageExecutor):
    """Stage 2: Segment multi-page PDF into individual drawings."""
    
    def __init__(self):
        super().__init__('segmentation')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification_v2.services.segmentation import segment_document
        
        segments = segment_document(str(context.document_id), context.file_path)
        context.segments = segments
        
        return {
            'segment_count': len(segments),
            'segments': [
                {
                    'drawing_id': s.drawing_id,
                    'title': s.title,
                    'page_index': s.page_index,
                }
                for s in segments
            ],
        }


class ExtractionStage(StageExecutor):
    """Stage 3: Extract P&ID elements (instruments, valves, tags, lines) — for EVERY page/segment."""
    
    def __init__(self):
        super().__init__('extraction')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification_v2.services.extraction import extract_drawing
        
        if not context.segments:
            raise ValueError("No segments available for extraction")

        # Soft-coded: keys summed across all pages for the aggregate stage report.
        totals = {
            'tags': 0, 'instruments': 0, 'valves': 0, 'equipment': 0,
            'line_sizes': 0, 'notes': 0, 'holds': 0, 'line_tags': 0,
        }

        for seg in context.segments:
            extraction = extract_drawing(context.file_path, page_index=seg.page_index)
            raw_text = extraction.get('raw_text', '') or ''
            extraction_summary = {
                'tags': len(extraction.get('tags', [])),
                'instruments': len(extraction.get('instruments', [])),
                'valves': len(extraction.get('valves', [])),
                'equipment': len(extraction.get('equipment', [])),
                'line_sizes': len(extraction.get('line_sizes', [])),
                'notes': len(extraction.get('notes', [])),
                'holds': len(extraction.get('holds', [])),
                'raw_text_length': len(raw_text),
                'no_text_detected': len(raw_text.strip()) == 0,
                'line_tags': len(extraction.get('line_tags', [])),
                'line_tags_multi_angle': sum(
                    1 for lt in extraction.get('line_tags', []) if lt.get('multi_angle')
                ),
            }

            context.segment_data.setdefault(seg.drawing_id, {})
            context.segment_data[seg.drawing_id]['extraction'] = extraction
            context.segment_data[seg.drawing_id]['extraction_summary'] = extraction_summary

            for key in ('tags', 'instruments', 'valves', 'equipment', 'line_sizes', 'notes', 'holds', 'line_tags'):
                totals[key] += extraction_summary[key]

        # Backward-compat: expose the first page's extraction as the
        # "primary" one for any single-drawing consumers of this context.
        first_id = context.segments[0].drawing_id
        context.extraction_data = context.segment_data[first_id]['extraction']
        
        return {
            'tags_count': totals['tags'],
            'instruments_count': totals['instruments'],
            'valves_count': totals['valves'],
            'equipment_count': totals['equipment'],
            'line_sizes_count': totals['line_sizes'],
            'pages_processed': len(context.segments),
        }


class GraphBuildingStage(StageExecutor):
    """Stage 4: Build connectivity graph — for EVERY page/segment."""
    
    def __init__(self):
        super().__init__('graph_building')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        if not is_feature_enabled('graph_connectivity'):
            self.logger.info("[graph_building] Feature disabled, skipping")
            return {'skipped': True}
        
        from apps.pid_verification_v2.services.graph_builder import build_graph
        
        node_total = 0
        edge_total = 0
        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {})
            graph = build_graph(extraction)
            seg_bucket['graph'] = graph
            node_total += len(graph.nodes()) if hasattr(graph, 'nodes') else 0
            edge_total += len(graph.edges()) if hasattr(graph, 'edges') else 0

        if context.segments:
            context.graph_data = context.segment_data[context.segments[0].drawing_id].get('graph')
        
        return {
            'node_count': node_total,
            'edge_count': edge_total,
        }


class RuleEngineStage(StageExecutor):
    """Stage 5: Apply deterministic rule engine — for EVERY page/segment."""
    
    def __init__(self):
        super().__init__('rule_engine')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification_v2.services.rule_engine import run_rules
        
        all_findings = []
        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {})
            graph = seg_bucket.get('graph')
            findings = run_rules(extraction, graph)
            seg_bucket['rule_findings'] = findings
            all_findings.extend(findings)

        context.rule_findings = all_findings  # aggregate — backward-compat
        
        return {
            'findings_count': len(all_findings),
            'severity_breakdown': self._get_severity_breakdown(all_findings),
        }
    
    def _get_severity_breakdown(self, findings: List) -> Dict[str, int]:
        """Get count of findings by severity."""
        breakdown = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        for f in findings:
            severity = getattr(f, 'severity', 'medium').lower()
            breakdown[severity] = breakdown.get(severity, 0) + 1
        return breakdown


class ComparisonEngineStage(StageExecutor):
    """Stage 6: V2 Comparison engine (cross-document comparison)."""
    
    def __init__(self):
        super().__init__('comparison_engine')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        if not is_feature_enabled('v2_comparison_engine'):
            self.logger.info("[comparison_engine] Feature disabled, skipping")
            return {'skipped': True}
        
        from apps.pid_verification_v2.services.comparison_engine import run_all_comparisons
        from apps.pid_verification_v2.services.rule_engine import RuleFinding
        
        # Reference data (legend / line list / equipment list / instrument
        # index) is document/project-level, not per-page — fetch once.
        legend_data = self._fetch_legend_data(context)
        line_list_data = self._fetch_line_list_data(context)
        equipment_list_data = self._fetch_equipment_list_data(context)
        instrument_index_data = self._fetch_instrument_index_data(context)
        
        all_findings = []
        aggregate_summary: Dict[str, Any] = {}

        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {})

            # Run comparisons for this page
            comparison_results = run_all_comparisons(
                extraction=extraction,
                legend_data=legend_data,
                line_list_data=line_list_data,
                equipment_list_data=equipment_list_data,
                instrument_index_data=instrument_index_data
            )
            
            # Convert to findings
            comparison_findings = []
            for comp_type, result in comparison_results.items():
                for finding in result.findings:
                    rule_prefix = {
                        'legend': 'LGN',
                        'linelist': 'LSZ',
                        'equipment': 'EQP',
                        'instrument': 'IMS'
                    }.get(comp_type, 'CMP')
                    
                    category_suffix = {
                        'missing': '001',
                        'extra': '002',
                        'mismatch': '003'
                    }.get(finding.category, '999')
                    
                    rule_id = f'{rule_prefix}-{category_suffix}'
                    
                    comparison_findings.append(RuleFinding(
                        category=comp_type,
                        rule_id=rule_id,
                        issue_observed=finding.issue_observed,
                        action_required=f'Review and resolve {finding.category} discrepancy',
                        evidence=finding.evidence,
                        direction='N/A',
                        severity=finding.severity
                    ))

            seg_bucket['comparison_findings'] = comparison_findings
            seg_bucket['comparison_summary'] = {
                comp_type: {
                    'matched': result.matched_count,
                    'missing': result.missing_count,
                    'extra': result.extra_count,
                    'mismatch': result.mismatch_count,
                }
                for comp_type, result in comparison_results.items()
            }
            all_findings.extend(comparison_findings)

            for comp_type, result in comparison_results.items():
                agg = aggregate_summary.setdefault(
                    comp_type, {'matched': 0, 'missing': 0, 'extra': 0, 'mismatch': 0}
                )
                agg['matched']  += result.matched_count
                agg['missing']  += result.missing_count
                agg['extra']    += result.extra_count
                agg['mismatch'] += result.mismatch_count
        
        context.comparison_findings = all_findings  # aggregate — backward-compat
        
        return {
            'findings_count': len(all_findings),
            'comparison_summary': aggregate_summary,
        }
    
    def _fetch_legend_data(self, context: PipelineContext) -> List:
        """Fetch legend reference data."""
        if context.project and hasattr(context.project, 'legend_knowledge_data'):
            return context.project.legend_knowledge_data or []
        return []
    
    def _fetch_line_list_data(self, context: PipelineContext) -> List:
        """Fetch line list reference data."""
        return self._fetch_reference_data(context, 'line_list')
    
    def _fetch_equipment_list_data(self, context: PipelineContext) -> List:
        """Fetch equipment list reference data."""
        return self._fetch_reference_data(context, 'equipment_list')
    
    def _fetch_instrument_index_data(self, context: PipelineContext) -> List:
        """Fetch instrument index reference data."""
        return self._fetch_reference_data(context, 'instrument_index')
    
    def _fetch_reference_data(self, context: PipelineContext, data_type: str) -> List:
        """
        Fetch the most recently completed PIDVReferenceData record of the
        given `data_type` for this project and return its parsed rows,
        normalized to the canonical field names expected by the
        `services.comparison_engine` compare_with_* functions.
        """
        if not context.project:
            return []
        
        from apps.pid_verification_v2.models import PIDVReferenceData
        
        ref = (
            PIDVReferenceData.objects
            .filter(
                project=context.project,
                data_type=data_type,
                status=PIDVReferenceData.Status.COMPLETED,
            )
            .exclude(parsed_data__isnull=True)
            .order_by('-created_at')
            .first()
        )
        if not ref or not ref.parsed_data:
            return []
        
        return _normalize_reference_rows(data_type, ref.parsed_data)


class AIAnalysisStage(StageExecutor):
    """Stage 7: AI analysis (BYOK - optional)."""
    
    def __init__(self):
        super().__init__('ai_analysis')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        if not is_feature_enabled('byok_ai_analysis'):
            return {'skipped': True, 'reason': 'Feature disabled'}
        
        analysis_mode = context.user_context.get('analysis_mode', 'standard')
        if analysis_mode == 'standard':
            return {'skipped': True, 'reason': 'Standard mode selected'}

        from apps.pid_verification_v2.services.ai_analysis import (
            run_openai_analysis,
            run_claude_analysis,
            run_hybrid_analysis,
            to_rule_findings,
        )

        openai_key = context.user_context.get('openai_api_key')
        claude_key = context.user_context.get('claude_api_key')

        all_ai_findings = []
        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {}) or {}
            drawing_data = {
                'instruments': extraction.get('instruments', []),
                'valves':      extraction.get('valves', []),
                'equipment':   extraction.get('equipment', []),
                'tags':        extraction.get('tags', []),
                'line_tags':   extraction.get('line_tags', []),
                'line_sizes':  extraction.get('line_sizes', []),
                'notes':       extraction.get('notes', []),
            }

            raw_findings: List[Dict[str, Any]] = []
            try:
                if analysis_mode == 'enhanced_openai' and openai_key:
                    raw_findings = run_openai_analysis(drawing_data, openai_key)
                elif analysis_mode == 'deep_claude' and claude_key:
                    raw_findings = run_claude_analysis(drawing_data, claude_key)
                elif analysis_mode == 'hybrid' and openai_key and claude_key:
                    raw_findings = run_hybrid_analysis(drawing_data, openai_key, claude_key)
                else:
                    self.logger.warning(
                        "[ai_analysis] mode=%s requested but required API key(s) missing — skipping",
                        analysis_mode,
                    )
                    return {'skipped': True, 'reason': 'Missing API key for selected mode'}
            except Exception as exc:
                # Non-critical: log and skip AI findings for THIS page only,
                # so one bad page doesn't abort AI analysis for the rest of
                # a multi-page document.
                self.logger.error(
                    "[ai_analysis] mode=%s failed for drawing_id=%s: %s",
                    analysis_mode, seg.drawing_id, exc, exc_info=True,
                )
                continue

            seg_findings = to_rule_findings(raw_findings)
            seg_bucket['ai_findings'] = seg_findings
            all_ai_findings.extend(seg_findings)

        context.ai_findings = all_ai_findings  # aggregate — backward-compat

        return {'findings_count': len(all_ai_findings), 'mode': analysis_mode}


class ReportGenerationStage(StageExecutor):
    """Stage 8: Generate reports (Excel, PDF)."""
    
    def __init__(self):
        super().__init__('report_generation')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        # Report generation implementation (existing code)
        # ... (kept for brevity)
        
        return {
            'excel_generated': False,
            'pdf_generated': False,
        }


# ===========================================================================
# PIPELINE ORCHESTRATOR
# ===========================================================================

class PipelineOrchestrator:
    """Orchestrates the entire processing pipeline."""
    
    def __init__(self):
        self.logger = logging.getLogger('pidv.orchestrator')
        self.stages = [
            FileValidationStage(),
            SegmentationStage(),
            ExtractionStage(),
            GraphBuildingStage(),
            RuleEngineStage(),
            ComparisonEngineStage(),
            AIAnalysisStage(),
            ReportGenerationStage(),
        ]
    
    def execute(self, context: PipelineContext) -> PipelineContext:
        """
        Execute the full pipeline.
        
        Returns:
            Updated context with all results
        """
        self.logger.info(f"[Pipeline] Starting for document_id={context.document_id}")
        
        for stage_executor in self.stages:
            stage_config = stage_executor.config
            
            # Execute stage
            result = stage_executor.execute(context)
            context.add_result(result)
            
            # Check for critical failure
            if not result.success:
                if stage_config.critical:
                    self.logger.error(
                        f"[Pipeline] Critical stage {stage_config.id} failed. Aborting pipeline."
                    )
                    break
                else:
                    self.logger.warning(
                        f"[Pipeline] Non-critical stage {stage_config.id} failed. Continuing pipeline."
                    )
        
        total_duration = context.get_total_duration()
        self.logger.info(f"[Pipeline] Completed in {total_duration:.2f}s")
        
        return context


# ===========================================================================
# PROGRESS TRACKING
# ===========================================================================

def update_processing_progress(document, stage_id: str, progress: int):
    """Update processing progress in database for real-time frontend tracking."""
    metadata = document.metadata or {}
    metadata['current_stage'] = stage_id
    metadata['progress_percent'] = progress
    metadata['last_update'] = timezone.now().isoformat()
    document.metadata = metadata
    document.save(update_fields=['metadata'])
