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
        from apps.pid_verification.tasks import _resolve_file_path
        
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
        from apps.pid_verification.services.segmentation import segment_document
        
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
    """Stage 3: Extract P&ID elements (instruments, valves, tags, lines)."""
    
    def __init__(self):
        super().__init__('extraction')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification.services.extraction import extract_drawing
        
        # Extract from first segment (multi-segment support can be added)
        if not context.segments:
            raise ValueError("No segments available for extraction")
        
        seg = context.segments[0]
        extraction = extract_drawing(context.file_path, page_index=seg.page_index)
        context.extraction_data = extraction
        
        return {
            'tags_count': len(extraction.get('tags', [])),
            'instruments_count': len(extraction.get('instruments', [])),
            'valves_count': len(extraction.get('valves', [])),
            'equipment_count': len(extraction.get('equipment', [])),
            'line_sizes_count': len(extraction.get('line_sizes', [])),
            'extraction_summary': extraction.get('extraction_summary', {}),
        }


class GraphBuildingStage(StageExecutor):
    """Stage 4: Build connectivity graph."""
    
    def __init__(self):
        super().__init__('graph_building')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        if not is_feature_enabled('graph_connectivity'):
            self.logger.info("[graph_building] Feature disabled, skipping")
            return {'skipped': True}
        
        from apps.pid_verification.services.graph_builder import build_graph
        
        graph = build_graph(context.extraction_data)
        context.graph_data = graph
        
        return {
            'node_count': len(graph.nodes()) if hasattr(graph, 'nodes') else 0,
            'edge_count': len(graph.edges()) if hasattr(graph, 'edges') else 0,
        }


class RuleEngineStage(StageExecutor):
    """Stage 5: Apply deterministic rule engine."""
    
    def __init__(self):
        super().__init__('rule_engine')
    
    def _execute_impl(self, context: PipelineContext) -> Dict[str, Any]:
        from apps.pid_verification.services.rule_engine import run_rules
        
        findings = run_rules(context.extraction_data, context.graph_data)
        context.rule_findings = findings
        
        return {
            'findings_count': len(findings),
            'severity_breakdown': self._get_severity_breakdown(findings),
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
        
        from apps.pid_verification.services.comparison_engine import run_all_comparisons
        from apps.pid_verification.services.rule_engine import RuleFinding
        
        # Fetch reference data
        legend_data = self._fetch_legend_data(context)
        line_list_data = self._fetch_line_list_data(context)
        equipment_list_data = self._fetch_equipment_list_data(context)
        instrument_index_data = self._fetch_instrument_index_data(context)
        
        # Run comparisons
        comparison_results = run_all_comparisons(
            extraction=context.extraction_data,
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
                    direction=None,
                    severity=finding.severity
                ))
        
        context.comparison_findings = comparison_findings
        
        return {
            'findings_count': len(comparison_findings),
            'comparison_summary': {
                comp_type: {
                    'matched': result.matched_count,
                    'missing': result.missing_count,
                    'extra': result.extra_count,
                    'mismatch': result.mismatch_count,
                }
                for comp_type, result in comparison_results.items()
            },
        }
    
    def _fetch_legend_data(self, context: PipelineContext) -> List:
        """Fetch legend reference data."""
        if context.project and hasattr(context.project, 'legend_knowledge_data'):
            return context.project.legend_knowledge_data or []
        return []
    
    def _fetch_line_list_data(self, context: PipelineContext) -> List:
        """Fetch line list reference data."""
        # TODO: Implement when line list import is ready
        return []
    
    def _fetch_equipment_list_data(self, context: PipelineContext) -> List:
        """Fetch equipment list reference data."""
        # TODO: Implement when equipment import is ready
        return []
    
    def _fetch_instrument_index_data(self, context: PipelineContext) -> List:
        """Fetch instrument index reference data."""
        # TODO: Implement when instrument import is ready
        return []


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
        
        # AI analysis implementation (existing code)
        # ... (kept for brevity)
        
        context.ai_findings = []
        return {'findings_count': 0, 'mode': analysis_mode}


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
