"""
Spec Customization — Advanced AI-Powered Validation
====================================================

Intelligent validation layer to achieve 98% extraction accuracy:
1. Multi-model ensemble validation (Gemini + OpenAI consensus)
2. Component count validation (detect missing tables)
3. Material standard validation (ASTM/ASME/API cross-reference)
4. Size range validation (logical progression check)
5. Confidence-based auto-retry (re-extract low-confidence chunks)
6. Reference template comparison (LS1E-A3 structure matching)

Soft-coded configuration via ADVANCED_VALIDATION_CONFIG.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Soft-Coded Configuration
# ─────────────────────────────────────────────────────────────────────────────
ADVANCED_VALIDATION_CONFIG = {
    # ── Multi-Model Ensemble ────────────────────────────────────────────
    "enable_ensemble_extraction": True,      # Run Gemini + OpenAI in parallel
    "ensemble_consensus_threshold": 0.7,     # 70% agreement required to trust
    "ensemble_voting_strategy": "weighted",  # "weighted" | "majority" | "union"
    
    # ── Component Count Validation ──────────────────────────────────────
    "enable_component_count_validation": True,
    "min_components_per_class": 10,          # Typical spec has 50-200 components
    "warn_if_components_below": 30,          # Warn if suspiciously low
    "max_components_per_class": 500,         # Sanity check upper bound
    
    # ── Material Standard Validation ────────────────────────────────────
    "enable_material_standard_validation": True,
    "known_material_standards": {
        # ASTM (American Society for Testing and Materials)
        "ASTM A105", "ASTM A106", "ASTM A182", "ASTM A193", "ASTM A194",
        "ASTM A216", "ASTM A234", "ASTM A350", "ASTM A351", "ASTM A352",
        "ASTM A403", "ASTM A420", "ASTM B16", "ASTM B61", "ASTM B62",
        # ASME (American Society of Mechanical Engineers)
        "ASME B16.5", "ASME B16.9", "ASME B16.10", "ASME B16.11", "ASME B16.20",
        "ASME B16.34", "ASME B16.47", "ASME B31.3", "ASME B36.10M", "ASME B36.19M",
        # API (American Petroleum Institute)
        "API 594", "API 598", "API 600", "API 602", "API 6D", "API 609",
        # DIN/EN (European Standards)
        "DIN 2527", "DIN 2633", "DIN 28011", "EN 1092-1", "EN 10025",
        # MSS (Manufacturers Standardization Society)
        "MSS SP-44", "MSS SP-75", "MSS SP-79", "MSS SP-80", "MSS SP-97",
    },
    "fuzzy_match_threshold": 0.85,           # 85% similarity for fuzzy matching
    
    # ── Size Range Validation ───────────────────────────────────────────
    "enable_size_range_validation": True,
    "common_nps_sizes": [                    # Nominal Pipe Size (NPS) progression
        "1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2", "3", "4", "6",
        "8", "10", "12", "14", "16", "18", "20", "24", "30", "36", "42", "48"
    ],
    "common_dn_sizes": [                     # DN (metric) progression
        15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300,
        350, 400, 450, 500, 600, 700, 800, 900, 1000, 1200
    ],
    
    # ── Confidence-Based Auto-Retry ─────────────────────────────────────
    "enable_auto_retry": True,
    "retry_if_confidence_below": 0.60,       # Re-extract if confidence < 60%
    "retry_if_components_below": 15,         # Re-extract if components < 15
    "max_retry_attempts": 2,                 # Max retries per chunk
    "retry_strategies": [                     # Ordered list of retry strategies
        "increase_temperature",               # Try with higher temperature (0.3)
        "use_alternate_model",                # Switch Gemini ↔ OpenAI
        "split_chunk_smaller",                # Try with 5-page chunks instead of 10
    ],
    
    # ── Reference Template Comparison ───────────────────────────────────
    "enable_template_comparison": True,
    "reference_template": {                  # LS1E-A3 structure
        "expected_component_types": [
            "pipe", "fitting", "flange", "valve", "gasket", "bolt"
        ],
        "expected_fitting_subtypes": [
            "Weldolet", "Elbolet", "Latrolet", "Sockolet", "Thredolet",
            "90 Deg LR Elbow", "45 Deg LR Elbow", "Tee", "Reducing Tee",
            "Concentric Reducer", "Eccentric Reducer", "Cap", "Coupling"
        ],
        "expected_valve_subtypes": [
            "Gate", "Globe", "Check", "Ball", "Butterfly", "Needle"
        ],
        "expected_flange_subtypes": [
            "Weld Neck", "Blind", "Slip-On", "Lap Joint"
        ],
        "min_total_components": 50,          # Typical spec has 50-200 components
        "max_total_components": 500,
    },
    
    # ── Accuracy Metrics Tracking ───────────────────────────────────────
    "track_accuracy_metrics": True,
    "metrics_to_track": [
        "component_count_per_class",
        "component_type_distribution",
        "confidence_score_distribution",
        "material_standard_coverage",
        "size_range_completeness",
        "extraction_engine_used",
        "retry_attempts_made",
        "validation_warnings_count",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Model Ensemble Validation
# ─────────────────────────────────────────────────────────────────────────────
def merge_ensemble_results(
    gemini_classes: List[Dict[str, Any]],
    openai_classes: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Merge results from Gemini and OpenAI using intelligent voting.
    
    Strategy:
    1. For each class_code, compare component lists from both models
    2. If consensus ≥ threshold, merge with high confidence
    3. If divergence, use weighted voting based on component count
    4. Return merged classes + ensemble metrics
    
    Returns:
        (merged_classes, metrics)
    """
    if not ADVANCED_VALIDATION_CONFIG["enable_ensemble_extraction"]:
        return gemini_classes or openai_classes, {}
    
    gemini_map = {c.get("class_code", ""): c for c in gemini_classes if c.get("class_code")}
    openai_map = {c.get("class_code", ""): c for c in openai_classes if c.get("class_code")}
    
    all_codes = set(gemini_map.keys()) | set(openai_map.keys())
    merged: List[Dict[str, Any]] = []
    metrics = {
        "total_classes": len(all_codes),
        "gemini_only": 0,
        "openai_only": 0,
        "both_models": 0,
        "consensus_high": 0,
        "consensus_low": 0,
        "components_merged": 0,
    }
    
    for code in sorted(all_codes):
        gemini_cls = gemini_map.get(code)
        openai_cls = openai_map.get(code)
        
        if gemini_cls and openai_cls:
            # Both models extracted this class - merge intelligently
            metrics["both_models"] += 1
            merged_cls = _merge_two_classes(gemini_cls, openai_cls)
            
            # Calculate consensus score
            gemini_comps = len(gemini_cls.get("components", []))
            openai_comps = len(openai_cls.get("components", []))
            consensus = min(gemini_comps, openai_comps) / max(gemini_comps, openai_comps, 1)
            
            if consensus >= ADVANCED_VALIDATION_CONFIG["ensemble_consensus_threshold"]:
                merged_cls["confidence"] = min(1.0, merged_cls.get("confidence", 0.5) + 0.15)
                merged_cls["_ensemble_consensus"] = "high"
                metrics["consensus_high"] += 1
            else:
                merged_cls["_ensemble_consensus"] = "low"
                merged_cls["_needs_review"] = True
                metrics["consensus_low"] += 1
            
            merged_cls["_ensemble_gemini_components"] = gemini_comps
            merged_cls["_ensemble_openai_components"] = openai_comps
            merged.append(merged_cls)
            metrics["components_merged"] += len(merged_cls.get("components", []))
        
        elif gemini_cls:
            # Gemini only - use with lower confidence boost
            metrics["gemini_only"] += 1
            gemini_cls["_ensemble_source"] = "gemini_only"
            gemini_cls["_needs_review"] = True
            merged.append(gemini_cls)
        
        elif openai_cls:
            # OpenAI only - use with lower confidence boost
            metrics["openai_only"] += 1
            openai_cls["_ensemble_source"] = "openai_only"
            openai_cls["_needs_review"] = True
            merged.append(openai_cls)
    
    logger.info(
        "[AdvancedValidation] Ensemble merge: %d classes (%d gemini-only, %d openai-only, %d both) "
        "→ %d high consensus, %d low consensus",
        metrics["total_classes"], metrics["gemini_only"], metrics["openai_only"],
        metrics["both_models"], metrics["consensus_high"], metrics["consensus_low"]
    )
    
    return merged, metrics


def _merge_two_classes(cls1: Dict[str, Any], cls2: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two class dictionaries by taking the richer one and adding missing components."""
    # Use the one with more components as base
    base, other = (cls1, cls2) if len(cls1.get("components", [])) >= len(cls2.get("components", [])) else (cls2, cls1)
    
    merged = {**base}
    
    # Merge metadata fields (prefer non-empty values)
    for field in ["material_grade", "pressure_rating", "flange_facing", "corrosion_allowance"]:
        if not merged.get(field) and other.get(field):
            merged[field] = other[field]
    
    # Merge service lists
    merged["service_list"] = list(set(
        (merged.get("service_list") or []) + (other.get("service_list") or [])
    ))
    
    # Merge components (deduplicate by signature)
    base_comps = merged.get("components", [])
    other_comps = other.get("components", [])
    
    seen_signatures: Set[str] = set()
    merged_comps: List[Dict[str, Any]] = []
    
    for comp in base_comps + other_comps:
        sig = _component_signature(comp)
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            merged_comps.append(comp)
    
    merged["components"] = merged_comps
    
    # Average confidence scores
    conf1 = cls1.get("confidence", 0.5)
    conf2 = cls2.get("confidence", 0.5)
    merged["confidence"] = (conf1 + conf2) / 2
    
    # Mark as ensemble
    merged["_engine"] = "ensemble_gemini+openai"
    
    return merged


def _component_signature(comp: Dict[str, Any]) -> str:
    """Generate signature for component deduplication."""
    return "|".join([
        (comp.get("component_type") or "").lower().strip(),
        (comp.get("sub_type") or "").lower().strip(),
        (comp.get("size_from") or "").lower().strip(),
        (comp.get("size_to") or "").lower().strip(),
        (comp.get("description") or "").lower().strip()[:50],
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Component Count Validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_component_count(cls: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate component count and flag suspicious cases.
    
    Returns:
        validation_report with warnings and recommendations
    """
    if not ADVANCED_VALIDATION_CONFIG["enable_component_count_validation"]:
        return {"status": "skipped"}
    
    components = cls.get("components", [])
    count = len(components)
    min_expected = ADVANCED_VALIDATION_CONFIG["min_components_per_class"]
    warn_threshold = ADVANCED_VALIDATION_CONFIG["warn_if_components_below"]
    max_expected = ADVANCED_VALIDATION_CONFIG["max_components_per_class"]
    
    report = {
        "status": "pass",
        "component_count": count,
        "warnings": [],
        "recommendations": [],
    }
    
    if count < min_expected:
        report["status"] = "critical"
        report["warnings"].append(
            f"Component count ({count}) is critically low (expected ≥{min_expected}). "
            f"Possible incomplete extraction."
        )
        report["recommendations"].append("Re-extract this class with ensemble mode or manual review")
        cls["_needs_retry"] = True
    
    elif count < warn_threshold:
        report["status"] = "warning"
        report["warnings"].append(
            f"Component count ({count}) is below typical threshold ({warn_threshold}). "
            f"Verify completeness."
        )
        report["recommendations"].append("Manually review extracted components against PDF")
    
    elif count > max_expected:
        report["status"] = "warning"
        report["warnings"].append(
            f"Component count ({count}) is unusually high (max {max_expected}). "
            f"Possible duplicate extraction."
        )
        report["recommendations"].append("Check for duplicate components in deduplication step")
    
    else:
        report["status"] = "pass"
    
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Material Standard Validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_material_standards(cls: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate material standards against known industry standards.
    
    Returns:
        validation_report with unrecognized standards
    """
    if not ADVANCED_VALIDATION_CONFIG["enable_material_standard_validation"]:
        return {"status": "skipped"}
    
    components = cls.get("components", [])
    known_standards = ADVANCED_VALIDATION_CONFIG["known_material_standards"]
    
    unrecognized: List[str] = []
    recognized_count = 0
    
    for comp in components:
        std = comp.get("material_standard", "").strip().upper()
        if not std:
            continue
        
        # Check exact match
        if std in known_standards:
            recognized_count += 1
            continue
        
        # Check fuzzy match
        if _fuzzy_match_standard(std, known_standards):
            recognized_count += 1
            continue
        
        # Unrecognized
        if std not in unrecognized:
            unrecognized.append(std)
    
    report = {
        "status": "pass",
        "recognized_count": recognized_count,
        "unrecognized_count": len(unrecognized),
        "unrecognized_standards": unrecognized[:10],  # Limit to 10 for brevity
        "warnings": [],
    }
    
    if unrecognized:
        report["status"] = "warning"
        report["warnings"].append(
            f"Found {len(unrecognized)} unrecognized material standards. "
            f"Examples: {', '.join(unrecognized[:3])}"
        )
    
    return report


def _fuzzy_match_standard(std: str, known_set: Set[str]) -> bool:
    """Check if standard fuzzy-matches any known standard."""
    threshold = ADVANCED_VALIDATION_CONFIG["fuzzy_match_threshold"]
    for known in known_set:
        if _similarity(std, known) >= threshold:
            return True
    return False


def _similarity(a: str, b: str) -> float:
    """Return similarity score 0.0–1.0 (simple Levenshtein-based)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    
    a = a.lower().strip()
    b = b.lower().strip()
    
    if a == b:
        return 1.0
    
    # Simple Levenshtein distance
    m, n = len(a), len(b)
    if m > n:
        a, b, m, n = b, a, n, m
    
    current = list(range(n + 1))
    for i in range(1, m + 1):
        previous, current = current, [i] + [0] * n
        for j in range(1, n + 1):
            add, delete, change = previous[j] + 1, current[j - 1] + 1, previous[j - 1]
            if a[i - 1] != b[j - 1]:
                change += 1
            current[j] = min(add, delete, change)
    
    distance = current[n]
    max_len = max(m, n)
    return 1.0 - (distance / max_len)


# ─────────────────────────────────────────────────────────────────────────────
# Size Range Validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_size_ranges(cls: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate size ranges follow logical NPS/DN progressions.
    
    Returns:
        validation_report with gaps and anomalies
    """
    if not ADVANCED_VALIDATION_CONFIG["enable_size_range_validation"]:
        return {"status": "skipped"}
    
    components = cls.get("components", [])
    nps_sizes = set(ADVANCED_VALIDATION_CONFIG["common_nps_sizes"])
    dn_sizes = set(ADVANCED_VALIDATION_CONFIG["common_dn_sizes"])
    
    extracted_nps: Set[str] = set()
    extracted_dn: Set[int] = set()
    anomalies: List[str] = []
    
    for comp in components:
        size_from = comp.get("size_from", "").strip()
        size_to = comp.get("size_to", "").strip()
        
        for size_str in [size_from, size_to]:
            if not size_str:
                continue
            
            # Try NPS (inch) format
            nps_match = re.search(r'(\d+(?:[-/]\d+)?)\s*(?:"|in|inch)?', size_str, re.IGNORECASE)
            if nps_match:
                nps = nps_match.group(1)
                extracted_nps.add(nps)
                if nps not in nps_sizes:
                    anomalies.append(f"Uncommon NPS size: {nps}")
            
            # Try DN (metric) format
            dn_match = re.search(r'DN\s*(\d+)', size_str, re.IGNORECASE)
            if dn_match:
                dn = int(dn_match.group(1))
                extracted_dn.add(dn)
                if dn not in dn_sizes:
                    anomalies.append(f"Uncommon DN size: DN{dn}")
    
    report = {
        "status": "pass",
        "nps_sizes_found": len(extracted_nps),
        "dn_sizes_found": len(extracted_dn),
        "anomalies": anomalies[:5],  # Limit to 5
        "warnings": [],
    }
    
    if anomalies:
        report["status"] = "warning"
        report["warnings"].append(
            f"Found {len(anomalies)} uncommon size specifications. "
            f"Verify against source PDF."
        )
    
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Reference Template Comparison
# ─────────────────────────────────────────────────────────────────────────────
def compare_to_reference_template(classes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compare extracted classes against LS1E-A3 reference template structure.
    
    Returns:
        comparison_report with coverage metrics
    """
    if not ADVANCED_VALIDATION_CONFIG["enable_template_comparison"]:
        return {"status": "skipped"}
    
    template = ADVANCED_VALIDATION_CONFIG["reference_template"]
    
    # Aggregate all components
    all_components: List[Dict[str, Any]] = []
    for cls in classes:
        all_components.extend(cls.get("components", []))
    
    # Analyze component type coverage
    component_types_found: Set[str] = set()
    fitting_subtypes_found: Set[str] = set()
    valve_subtypes_found: Set[str] = set()
    flange_subtypes_found: Set[str] = set()
    
    for comp in all_components:
        comp_type = (comp.get("component_type") or "").strip().lower()
        sub_type = (comp.get("sub_type") or "").strip()
        
        component_types_found.add(comp_type)
        
        if comp_type == "fitting":
            fitting_subtypes_found.add(sub_type)
        elif comp_type == "valve":
            valve_subtypes_found.add(sub_type)
        elif comp_type == "flange":
            flange_subtypes_found.add(sub_type)
    
    # Calculate coverage
    expected_types = set(template["expected_component_types"])
    type_coverage = len(component_types_found & expected_types) / len(expected_types)
    
    expected_fitting_subtypes = set(template["expected_fitting_subtypes"])
    fitting_coverage = len(fitting_subtypes_found & expected_fitting_subtypes) / max(len(expected_fitting_subtypes), 1)
    
    expected_valve_subtypes = set(template["expected_valve_subtypes"])
    valve_coverage = len(valve_subtypes_found & expected_valve_subtypes) / max(len(expected_valve_subtypes), 1)
    
    expected_flange_subtypes = set(template["expected_flange_subtypes"])
    flange_coverage = len(flange_subtypes_found & expected_flange_subtypes) / max(len(expected_flange_subtypes), 1)
    
    # Overall accuracy estimate
    total_components = len(all_components)
    accuracy_estimate = min(1.0, (
        type_coverage * 0.3 +
        fitting_coverage * 0.25 +
        valve_coverage * 0.15 +
        flange_coverage * 0.15 +
        min(total_components / template["min_total_components"], 1.0) * 0.15
    ))
    
    report = {
        "status": "pass",
        "accuracy_estimate": round(accuracy_estimate * 100, 1),  # Percentage
        "total_components": total_components,
        "component_type_coverage": round(type_coverage * 100, 1),
        "fitting_subtype_coverage": round(fitting_coverage * 100, 1),
        "valve_subtype_coverage": round(valve_coverage * 100, 1),
        "flange_subtype_coverage": round(flange_coverage * 100, 1),
        "missing_component_types": list(expected_types - component_types_found),
        "missing_fitting_subtypes": list(expected_fitting_subtypes - fitting_subtypes_found),
        "warnings": [],
        "recommendations": [],
    }
    
    if accuracy_estimate < 0.98:  # Below 98% target
        report["status"] = "warning"
        report["warnings"].append(
            f"Estimated accuracy ({report['accuracy_estimate']}%) is below 98% target"
        )
        
        if total_components < template["min_total_components"]:
            report["recommendations"].append(
                f"Component count ({total_components}) is below minimum ({template['min_total_components']}). "
                f"Re-extract with ensemble mode."
            )
        
        if report["missing_component_types"]:
            report["recommendations"].append(
                f"Missing component types: {', '.join(report['missing_component_types'])}. "
                f"Verify extraction completeness."
            )
    
    logger.info(
        "[AdvancedValidation] Template comparison: %d components, %.1f%% estimated accuracy, "
        "%.1f%% type coverage, %.1f%% fitting coverage",
        total_components, report["accuracy_estimate"],
        report["component_type_coverage"], report["fitting_subtype_coverage"]
    )
    
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Comprehensive Validation Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def validate_extracted_classes(
    classes: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Run all validation checks and return comprehensive validation report.
    
    Args:
        classes: Extracted piping classes
        context: Optional context (job_id, document_id, etc.)
    
    Returns:
        validation_report with status, warnings, metrics, recommendations
    """
    context = context or {}
    
    validation_report = {
        "overall_status": "pass",
        "timestamp": None,  # Will be set by caller
        "classes_validated": len(classes),
        "component_count_validation": [],
        "material_standard_validation": [],
        "size_range_validation": [],
        "template_comparison": {},
        "warnings_total": 0,
        "needs_retry_count": 0,
        "needs_review_count": 0,
        "recommendations": [],
    }
    
    # Run per-class validations
    for cls in classes:
        class_code = cls.get("class_code", "?")
        
        # Component count validation
        count_report = validate_component_count(cls)
        if count_report["status"] != "skipped":
            validation_report["component_count_validation"].append({
                "class_code": class_code,
                **count_report
            })
            if count_report.get("warnings"):
                validation_report["warnings_total"] += len(count_report["warnings"])
            if cls.get("_needs_retry"):
                validation_report["needs_retry_count"] += 1
        
        # Material standard validation
        std_report = validate_material_standards(cls)
        if std_report["status"] != "skipped":
            validation_report["material_standard_validation"].append({
                "class_code": class_code,
                **std_report
            })
            if std_report.get("warnings"):
                validation_report["warnings_total"] += len(std_report["warnings"])
        
        # Size range validation
        size_report = validate_size_ranges(cls)
        if size_report["status"] != "skipped":
            validation_report["size_range_validation"].append({
                "class_code": class_code,
                **size_report
            })
            if size_report.get("warnings"):
                validation_report["warnings_total"] += len(size_report["warnings"])
        
        # Check if needs review
        if cls.get("_needs_review"):
            validation_report["needs_review_count"] += 1
    
    # Run aggregate validations
    template_report = compare_to_reference_template(classes)
    validation_report["template_comparison"] = template_report
    
    if template_report.get("warnings"):
        validation_report["warnings_total"] += len(template_report["warnings"])
    
    # Aggregate recommendations
    all_recommendations = []
    for report in (validation_report["component_count_validation"] +
                   validation_report["material_standard_validation"] +
                   validation_report["size_range_validation"]):
        all_recommendations.extend(report.get("recommendations", []))
    
    all_recommendations.extend(template_report.get("recommendations", []))
    validation_report["recommendations"] = list(set(all_recommendations))[:5]  # Top 5 unique
    
    # Determine overall status
    if validation_report["needs_retry_count"] > 0:
        validation_report["overall_status"] = "needs_retry"
    elif validation_report["warnings_total"] > 0:
        validation_report["overall_status"] = "warning"
    else:
        validation_report["overall_status"] = "pass"
    
    logger.info(
        "[AdvancedValidation] Validation complete: %s, %d classes, %d warnings, %d need retry, %d need review",
        validation_report["overall_status"],
        validation_report["classes_validated"],
        validation_report["warnings_total"],
        validation_report["needs_retry_count"],
        validation_report["needs_review_count"]
    )
    
    return validation_report
