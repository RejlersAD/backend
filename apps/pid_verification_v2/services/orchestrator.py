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
import re
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
        # Deliberately does NOT include a bare 'fluid' alias — that matched
        # 'FLUID PHASE' (a free-text phase description like "Vapor" or
        # "Multi Phase") as a false substring hit, which then got composited
        # into every line_tag instead of the real short SERVICE CODE (e.g.
        # "FL"), so the reconstructed tag could never match the P&ID's
        # actual composite tags (confirmed live — 0/347 line_list matches
        # until this fix). 'service code'/'fluid code' explicitly target the
        # short-code column; see _BLOCKED_COLUMN_SUBSTRINGS below for the
        # extra guard against 'phase'-named description columns.
        'service':  ['service code', 'fluid code', 'service', 'commodity'],
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


# Soft-coded: a 'line_tag' value is treated as an already-composite pipeline
# designation (e.g. 6"-CD-AC3N-8183) — and therefore left untouched — if it
# contains an inch mark or any letter. Real-world Line List exports often
# split the designation into separate Size / Service Code / Spec / Line
# Number columns instead, in which case the 'line_tag' column only holds the
# bare numeric sequence (e.g. "8183"), which will never match the composite
# tags extracted from the P&ID drawing itself.
COMPOSITE_LINE_TAG_MARKER_RE = re.compile(r'["\u201d]|[A-Za-z]')

# Column headers containing any of these substrings are NEVER mapped onto
# the given canonical field, even if one of its aliases would otherwise
# substring-match \u2014 these are description/free-text columns that happen to
# share a word with a short-code column's alias (e.g. 'FLUID PHASE' vs the
# 'service' field's 'fluid code' alias). "Prefer short-code columns over
# description columns" (line_list/service) is enforced here explicitly
# rather than relying on alias specificity alone.
_BLOCKED_COLUMN_SUBSTRINGS = {
    ('line_list', 'service'): ('phase', 'description', 'desc'),
}

# A genuine service/fluid code is 1-4 letters (FL, SG, CD, HC, ...) — used
# to validate _build_composite_line_tag()'s 'service' value before using it.
_LOOKS_LIKE_SERVICE_CODE_RE = re.compile(r'^[A-Za-z]{1,4}$')


def _build_composite_line_tag(mapped: Dict[str, Any]) -> None:
    """
    Reconstruct the full composite pipeline designation for line_list rows
    whose source document stores Size / Service Code / Spec / Line Number as
    separate columns rather than one pre-composed tag column, so the result
    lines up with the composite tags read off the actual P&ID drawing
    (e.g. Size="6", Service Code="CD", Spec="AC3N", Line Number="8183" ->
    line_tag = '6"-CD-AC3N-8183').
    """
    tag = str(mapped.get('line_tag') or '').strip()
    if not tag or COMPOSITE_LINE_TAG_MARKER_RE.search(tag):
        return  # empty, or already looks like a composite designation

    size = str(mapped.get('size') or '').strip()
    service = str(mapped.get('service') or '').strip()
    spec = str(mapped.get('spec') or '').strip()
    if not (size and service):
        return  # not enough info to rebuild a composite tag
    if not _LOOKS_LIKE_SERVICE_CODE_RE.match(service):
        # Validation gate: a genuine service/fluid code is short (2-4
        # letters, e.g. FL, SG, CD) — if whatever landed in 'service' looks
        # like a sentence/description instead (spaces, lowercase words,
        # too long), building a composite tag from it would only ever
        # produce a value that can never match the P&ID's real tags. Log
        # and skip rather than emit a tag guaranteed to create a false
        # "missing" finding.
        logger.warning(
            "[LineListNormalize] 'service' value %r doesn't look like a short "
            "code — skipping composite line_tag for this row (raw tag=%r)",
            service, tag,
        )
        return

    parts = [f'{size}"', service]
    if spec:
        parts.append(spec)
    parts.append(tag)

    mapped['raw_line_number'] = tag
    mapped['line_tag'] = '-'.join(parts)


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
            blocked = _BLOCKED_COLUMN_SUBSTRINGS.get((data_type, canonical), ())
            if any(b in cleaned_col for b in blocked):
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
        if data_type == 'line_list':
            _build_composite_line_tag(mapped)
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

    # Reference symbol pictures (LegendSymbolImage, via apps.pid_checker_v2),
    # loaded ONCE per document run by the caller (tasks.py) — before either
    # the inline orchestrator.execute() call or the per-page fan-out chord —
    # and reused for every page here, instead of every page/task re-reading
    # and re-encoding the same images from storage. Always fetched fresh
    # from the DB by the caller (never a hardcoded count), so this scales
    # to any library size without a code change; see legend_bridge.py.
    symbol_images: List[Dict[str, Any]] = field(default_factory=list)

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

        # Hybrid extraction: Tesseract (if installed) + AI Vision (if a BYOK
        # key is present in this run's context) — see services/extraction.py.
        extraction_api_key = context.user_context.get('claude_api_key') or context.user_context.get('openai_api_key')
        extraction_provider = 'claude' if context.user_context.get('claude_api_key') else 'openai'

        for seg in context.segments:
            extraction = extract_drawing(
                context.file_path, page_index=seg.page_index,
                api_key=extraction_api_key, provider=extraction_provider,
            )
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
        
        from apps.pid_verification_v2.services.comparison_engine import run_all_comparisons, _normalize_tag
        from apps.pid_verification_v2.services.rule_engine import RuleFinding
        
        # Reference data (legend / line list / equipment list / instrument
        # index) is document/project-level, not per-page — fetch once.
        legend_data = self._fetch_legend_data(context)
        line_list_data = self._fetch_line_list_data(context)
        equipment_list_data = self._fetch_equipment_list_data(context)
        instrument_index_data = self._fetch_instrument_index_data(context)

        # ── Aggregate P&ID-extracted items across ALL pages/segments so every
        # comparison runs ONCE at the document level instead of once per page.
        # Running per-page previously meant the same discrepancy (e.g. an
        # equipment tag referenced/mentioned on several pages but genuinely
        # absent from the register) was flagged as a duplicate 'critical'
        # finding on every page that mentioned it, instead of a single
        # document-wide fact. We still track the first page each tag was
        # extracted on so findings can be attributed back to the right
        # drawing for the Drawing Layout marker view.
        all_symbols: List[Dict[str, Any]] = []
        all_lines: List[Dict[str, Any]] = []
        all_equipment: List[Dict[str, Any]] = []
        all_instruments: List[Dict[str, Any]] = []
        tag_to_drawing: Dict[str, Dict[str, str]] = {
            'legend': {}, 'linelist': {}, 'equipment': {}, 'instrument': {},
        }

        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {})

            for s in extraction.get('symbols', []):
                all_symbols.append(s)
                key = (s.get('symbol_type') or '').strip().upper()
                if key:
                    tag_to_drawing['legend'].setdefault(key, seg.drawing_id)

            for l in extraction.get('line_tags', []):
                all_lines.append(l)
                key = _normalize_tag(l.get('text', ''))
                if key:
                    tag_to_drawing['linelist'].setdefault(key, seg.drawing_id)

            for eq in extraction.get('equipment', []):
                all_equipment.append(eq)
                key = _normalize_tag(eq.get('tag', ''))
                if key:
                    tag_to_drawing['equipment'].setdefault(key, seg.drawing_id)

            for ins in extraction.get('instruments', []):
                all_instruments.append(ins)
                key = _normalize_tag(ins.get('tag', ''))
                if key:
                    tag_to_drawing['instrument'].setdefault(key, seg.drawing_id)

        aggregated_extraction = {
            'symbols': all_symbols,
            'line_tags': all_lines,
            'equipment': all_equipment,
            'instruments': all_instruments,
        }

        # Run each comparison type ONCE for the whole document.
        comparison_results = run_all_comparisons(
            extraction=aggregated_extraction,
            legend_data=legend_data,
            line_list_data=line_list_data,
            equipment_list_data=equipment_list_data,
            instrument_index_data=instrument_index_data,
            # Smart AI value comparison — same Claude key already used for
            # this run's Vision analysis (context.user_context), reused here
            # so a mismatch the naive fuzzy-match threshold can't confidently
            # call (units, formatting, ranges) gets a real judgment instead
            # of a false positive. Omitted (None) falls back to the exact
            # original deterministic behavior when no key is available.
            ai_api_key=context.user_context.get('claude_api_key'),
        )

        # Fallback bucket (first page) for document-level findings with no
        # natural page association — e.g. a reference-list item ('missing')
        # that's absent from the P&ID entirely.
        first_drawing_id = context.segments[0].drawing_id if context.segments else None

        all_findings: List[Any] = []
        aggregate_summary: Dict[str, Any] = {}
        findings_by_drawing: Dict[str, List[Any]] = {seg.drawing_id: [] for seg in context.segments}

        for comp_type, result in comparison_results.items():
            rule_prefix = {
                'legend': 'LGN',
                'linelist': 'LSZ',
                'equipment': 'EQP',
                'instrument': 'IMS'
            }.get(comp_type, 'CMP')

            for finding in result.findings:
                category_suffix = {
                    'missing': '001',
                    'extra': '002',
                    'mismatch': '003'
                }.get(finding.category, '999')

                rule_id = f'{rule_prefix}-{category_suffix}'

                rule_finding = RuleFinding(
                    category=comp_type,
                    rule_id=rule_id,
                    issue_observed=finding.issue_observed,
                    action_required=f'Review and resolve {finding.category} discrepancy',
                    evidence=finding.evidence,
                    direction='N/A',
                    severity=finding.severity
                )

                # Attribute the finding back to the page the tag actually
                # appears on (extra/mismatch originate from a real
                # P&ID-extracted item); 'missing' findings have no natural
                # page, so fall back to the first page only.
                key = (finding.item_id or '').strip().upper() if comp_type == 'legend' else _normalize_tag(finding.item_id)
                drawing_id = tag_to_drawing[comp_type].get(key) or first_drawing_id

                if drawing_id and drawing_id in findings_by_drawing:
                    findings_by_drawing[drawing_id].append(rule_finding)
                all_findings.append(rule_finding)

            agg = aggregate_summary.setdefault(
                comp_type, {'matched': 0, 'missing': 0, 'extra': 0, 'mismatch': 0}
            )
            agg['matched']  += result.matched_count
            agg['missing']  += result.missing_count
            agg['extra']    += result.extra_count
            agg['mismatch'] += result.mismatch_count

        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            seg_bucket['comparison_findings'] = findings_by_drawing.get(seg.drawing_id, [])
            # Document-wide tally (comparisons now run once per document, not
            # once per page) — same summary object content on every page,
            # copied per-segment to avoid shared mutable references.
            seg_bucket['comparison_summary'] = {k: dict(v) for k, v in aggregate_summary.items()}

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


# ── Smart page filter (Vision cost control) ───────────────────────────────
# Cover sheets, index/contents pages, and legend/symbol-key pages rarely
# have real P&ID content worth an expensive per-page Vision call. Skipping
# them is what makes per-page Vision practical at 35-50+ pages instead of
# paying for every single page regardless of content.
_LOW_VALUE_TITLE_KEYWORDS = ('legend', 'index', 'cover', 'contents', 'symbol key', 'abbreviation', 'revision history')
_MIN_EXTRACTED_ITEMS_FOR_VISION = 1  # a page with zero extracted tags/instruments/valves/equipment/line_tags is almost never worth Vision


def _page_worth_vision(extraction_summary: Dict[str, Any], drawing_title: str) -> bool:
    """True if this page looks like real P&ID content worth a Vision call."""
    title_lower = (drawing_title or '').lower()
    if any(kw in title_lower for kw in _LOW_VALUE_TITLE_KEYWORDS):
        return False
    total_items = sum(
        extraction_summary.get(k, 0)
        for k in ('tags', 'instruments', 'valves', 'equipment', 'line_tags')
    )
    return total_items >= _MIN_EXTRACTED_ITEMS_FOR_VISION


class AIAnalysisStage(StageExecutor):
    """Stage 7: AI analysis (BYOK - optional).

    Claude modes (deep_claude/hybrid) now send the ACTUAL rendered page
    image (not just OCR'd text) and, when reference symbol pictures are
    available (context.symbol_images — loaded ONCE per document run by the
    caller, see tasks.py), the same Vision call also does symbol
    recognition — see services/ai_analysis.py and services/legend_bridge.py.
    Runs per page, filtered by _page_worth_vision() so low-value pages
    (covers/index/legend sheets, near-empty pages) don't cost a Vision call.
    """

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
            run_hybrid_analysis,
            to_rule_findings,
        )
        from apps.pid_verification_v2.services.legend_bridge import (
            run_page_vision_analysis, SYMBOL_BATCH_SIZE,
        )

        openai_key = context.user_context.get('openai_api_key')
        claude_key = context.user_context.get('claude_api_key')
        symbol_images = context.symbol_images or []
        needs_page_image = analysis_mode in ('deep_claude', 'hybrid') and claude_key

        pdf_bytes = None
        if needs_page_image and context.file_path:
            try:
                with open(context.file_path, 'rb') as fh:
                    pdf_bytes = fh.read()
            except Exception:
                self.logger.warning("[ai_analysis] Could not read file_path for Vision", exc_info=True)

        all_ai_findings = []
        pages_analyzed = 0
        pages_skipped_by_filter = 0

        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {}) or {}
            extraction_summary = seg_bucket.get('extraction_summary', {}) or {}
            drawing_data = {
                'instruments': extraction.get('instruments', []),
                'valves':      extraction.get('valves', []),
                'equipment':   extraction.get('equipment', []),
                'tags':        extraction.get('tags', []),
                'line_tags':   extraction.get('line_tags', []),
                'line_sizes':  extraction.get('line_sizes', []),
                'notes':       extraction.get('notes', []),
            }
            seg_bucket.setdefault('ai_symbols', [])

            if not _page_worth_vision(extraction_summary, seg.title):
                self.logger.info(
                    "[ai_analysis] Skipping low-value page drawing_id=%s (filtered — no Vision call)",
                    seg.drawing_id,
                )
                pages_skipped_by_filter += 1
                seg_bucket['ai_findings'] = []
                continue

            page_image_b64 = None
            if needs_page_image and pdf_bytes is not None:
                try:
                    from apps.pid_checker_v2.services.vision_extractor import (
                        _render_single_page, _prepare_image_b64, VISION_OVERVIEW_MAX_DIMENSION_PX,
                    )
                    page_img = _render_single_page(pdf_bytes, seg.page_index)
                    page_image_b64 = _prepare_image_b64(page_img, VISION_OVERVIEW_MAX_DIMENSION_PX)
                except Exception:
                    self.logger.warning(
                        "[ai_analysis] Could not render page image for drawing_id=%s",
                        seg.drawing_id, exc_info=True,
                    )

            raw_findings: List[Dict[str, Any]] = []
            symbols: List[Dict[str, Any]] = []
            try:
                if analysis_mode == 'enhanced_openai' and openai_key:
                    result = run_openai_analysis(drawing_data, openai_key)
                    raw_findings = result['findings']
                elif analysis_mode == 'deep_claude' and claude_key and page_image_b64:
                    result = run_page_vision_analysis(
                        drawing_data, claude_key, page_image_b64, symbol_images=symbol_images,
                    )
                    if result:
                        raw_findings = result['findings']
                        symbols = result['symbols']
                elif analysis_mode == 'hybrid' and openai_key and claude_key:
                    result = run_hybrid_analysis(
                        drawing_data, openai_key, claude_key,
                        page_image_b64=page_image_b64,
                        symbol_images=symbol_images[:SYMBOL_BATCH_SIZE],
                    )
                    raw_findings = result['findings']
                    symbols = result['symbols']
                else:
                    self.logger.warning(
                        "[ai_analysis] mode=%s requested but required API key/page image "
                        "missing for drawing_id=%s — skipping this page",
                        analysis_mode, seg.drawing_id,
                    )
                    seg_bucket['ai_findings'] = []
                    continue
            except Exception as exc:
                # Non-critical: log and skip AI findings for THIS page only,
                # so one bad/slow page can't abort AI analysis for the rest
                # of a multi-page document.
                self.logger.error(
                    "[ai_analysis] mode=%s failed for drawing_id=%s: %s",
                    analysis_mode, seg.drawing_id, exc, exc_info=True,
                )
                seg_bucket['ai_findings'] = []
                continue

            seg_findings = to_rule_findings(raw_findings)
            seg_bucket['ai_findings'] = seg_findings
            seg_bucket['ai_symbols'] = symbols
            all_ai_findings.extend(seg_findings)
            pages_analyzed += 1

        context.ai_findings = all_ai_findings  # aggregate — backward-compat

        return {
            'findings_count': len(all_ai_findings),
            'mode': analysis_mode,
            'pages_analyzed': pages_analyzed,
            'pages_skipped_by_filter': pages_skipped_by_filter,
        }


class LegendSymbolBridgeStage(StageExecutor):
    """Stage 7b: pid_checker_v2 Legend Sheets + Symbol Images bridge.

    Connects V2's automatic pipeline to apps.pid_checker_v2's legend lookup
    tables (text matching, e.g. "FL" -> "FLARE GAS") and manually-uploaded
    reference symbol pictures (visual recognition via the same Vision call
    pid_checker_v2's own "Identify Symbols" feature uses). Results are
    persisted as PIDVComparisonFinding rows so they show up alongside the
    rest of V2's findings. See services/legend_bridge.py for the matching
    logic. Best-effort and additive — never blocks the pipeline (critical=False).
    """

    def __init__(self):
        super().__init__('legend_symbol_bridge')

    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from .legend_bridge import get_legend_lookup_fields, match_text_against_legend, cross_reference
        from apps.pid_verification_v2.models import PIDVComparisonFinding

        doc = context.document
        user = getattr(doc, 'uploaded_by', None)
        fields = get_legend_lookup_fields(user)

        # Symbol vision now runs PER PAGE inside AIAnalysisStage (which
        # already sends the real page image to Claude for findings — the
        # SAME call also does symbol recognition, see
        # legend_bridge.run_page_vision_analysis). This stage no longer
        # makes its own Vision call; it just reads AIAnalysisStage's
        # per-page results (seg_bucket['ai_symbols']) and cross-references
        # them against this page's own text matches — giving real page
        # attribution "for free" instead of the earlier whole-document,
        # unattributed symbol list.
        #
        # NOTE: reprocess idempotency (clearing this document's previous
        # bridge findings) is handled ONCE, document-wide, in tasks.py
        # BEFORE this stage runs for any page — not here. This stage may
        # run once per page under the large-document parallel fan-out
        # (process_pid_page, one Celery task per page); deleting here would
        # wipe out the previous page's freshly-written findings on every
        # subsequent page's task.
        total_text_matches = 0
        total_linked = total_text_only = total_symbol_only = 0
        symbol_vision_pages = 0
        findings_to_create: List[Any] = []

        for seg in context.segments:
            seg_bucket = context.segment_data.setdefault(seg.drawing_id, {})
            extraction = seg_bucket.get('extraction', {}) or {}
            page_text_matches = match_text_against_legend(extraction, fields)
            total_text_matches += len(page_text_matches)

            page_symbols = seg_bucket.get('ai_symbols') or []
            if page_symbols:
                symbol_vision_pages += 1
            symbol_result = {'symbols': page_symbols} if page_symbols else None

            xref = cross_reference(page_text_matches, symbol_result)
            total_linked += len(xref['linked'])
            total_text_only += len(xref['text_only'])
            total_symbol_only += len(xref['symbol_only'])

            if context.project is None:
                continue

            for item in xref['linked']:
                findings_to_create.append(PIDVComparisonFinding(
                    project=context.project,
                    finding_type=PIDVComparisonFinding.FindingType.SYMBOL_LEGEND_MATCH,
                    severity=PIDVComparisonFinding.Severity.LOW,
                    title=f"Confirmed: {item['tag']} → {item['description']}",
                    description=(
                        f"Text tag '{item['tag']}' (code '{item['code']}' in "
                        f"{item['field_label']}, section '{item['section']}') matches legend "
                        f"entry '{item['description']}', and Vision independently identified a "
                        f"matching symbol ('{item['symbol']['symbol_type']}') on this drawing."
                    ),
                    evidence={
                        'document_id': str(context.document_id),
                        'drawing_id': seg.drawing_id,
                        'text_match': {k: v for k, v in item.items() if k != 'symbol'},
                        'symbol': item['symbol'],
                    },
                    ai_confidence=90.0,
                    location_info={'drawing_id': seg.drawing_id, 'symbol_location': item['symbol'].get('location')},
                ))

            for item in xref['text_only']:
                findings_to_create.append(PIDVComparisonFinding(
                    project=context.project,
                    finding_type=PIDVComparisonFinding.FindingType.SYMBOL_LEGEND_MATCH,
                    severity=PIDVComparisonFinding.Severity.LOW,
                    title=f"Legend text match: {item['tag']} → {item['description']}",
                    description=(
                        f"Text tag '{item['tag']}' (code '{item['code']}' in "
                        f"{item['field_label']}, section '{item['section']}') matches legend "
                        f"entry '{item['description']}'. No corresponding symbol was visually "
                        f"confirmed."
                    ),
                    evidence={'document_id': str(context.document_id), 'drawing_id': seg.drawing_id, 'text_match': item},
                    ai_confidence=55.0,
                    location_info={'drawing_id': seg.drawing_id},
                ))

            for sym in xref['symbol_only']:
                findings_to_create.append(PIDVComparisonFinding(
                    project=context.project,
                    finding_type=PIDVComparisonFinding.FindingType.SYMBOL_LEGEND_MATCH,
                    severity=PIDVComparisonFinding.Severity.LOW,
                    title=f"Symbol identified: {sym['symbol_type']}",
                    description=(
                        f"Vision identified a '{sym['symbol_type']}' symbol on this drawing "
                        f"({sym.get('confidence', 'low')} confidence). No matching legend text "
                        f"tag was found."
                    ),
                    evidence={'document_id': str(context.document_id), 'drawing_id': seg.drawing_id, 'symbol': sym},
                    ai_confidence={'high': 85.0, 'medium': 60.0, 'low': 35.0}.get(sym.get('confidence'), 35.0),
                    location_info={'drawing_id': seg.drawing_id, 'symbol_location': sym.get('location')},
                ))

        if findings_to_create:
            PIDVComparisonFinding.objects.bulk_create(findings_to_create, batch_size=200)

        return {
            'legend_fields_used': len(fields),
            'text_matches': total_text_matches,
            'symbol_vision_pages': symbol_vision_pages,
            'linked_count': total_linked,
            'text_only_count': total_text_only,
            'symbol_only_count': total_symbol_only,
        }


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
            LegendSymbolBridgeStage(),
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
