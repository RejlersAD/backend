"""
PFD Analysis Service — Advanced 4-Pass AI Engine
Multi-stage GPT-4o Vision analysis with reference document text extraction,
visual inventory scan, deep systematic checks, and gap analysis.
"""
import os
import re
import base64
import io
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
from django.conf import settings
from django.core.files.storage import default_storage
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image


# ---------------------------------------------------------------------------
# CAG helpers
# ---------------------------------------------------------------------------

def _pfd_inventory_contains(inventory_set: Set[str], token: str) -> bool:
    """True if *token* is a substring of any item in inventory_set."""
    token_up = token.upper()
    return any(token_up in item for item in inventory_set)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json(text: str) -> dict:
    """Parse JSON from a model response that may be wrapped in markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class PFDAnalysisService:
    """
    Advanced 4-Pass AI-Powered PFD Verification Service.

    Pass 1 — Visual Inventory Scan:
        Examine the PFD image and extract every visible element (equipment tags,
        stream numbers, title block fields, notes, utility headers, battery limits).

    Pass 2 — Reference Document Content Extraction:
        Read the text of each uploaded reference document (PDF / text) using
        PyMuPDF, then build a structured context snippet for the AI.

    Pass 3 — Deep Systematic Analysis (40+ checks):
        Comprehensive issue identification against expanded engineering checklist,
        enriched with inventory from Pass 1 and reference context from Pass 2.

    Pass 4 — Gap & Missing Element Review:
        Second-eye sweep focused exclusively on what is ABSENT or INCOMPLETE in
        the PFD (missing streams, unlabeled equipment, absent utility headers,
        incomplete title block, missing general notes, etc.).

    All passes are merged, deduplicated and renumbered before returning.
    """

    # -----------------------------------------------------------------------
    # Configuration
    # -----------------------------------------------------------------------
    MODEL = "gpt-4o"
    TEMPERATURE = 0.1
    MAX_TOKENS = 16000
    # Characters of extracted reference-doc text fed to the AI per document
    REF_DOC_TEXT_LIMIT = 3000
    # Resolution multiplier for PDF→image rendering (2.5 × 72 Dpi = ~180 DPI)
    PDF_ZOOM = 2.5

    # Expanded engineering categories
    VALID_CATEGORIES = {
        "Equipment", "Streams", "Control", "Documentation",
        "Safety", "Material Balance", "Utilities", "Process Design", "Other"
    }
    VALID_SEVERITIES = {"critical", "major", "minor", "observation"}

    # -----------------------------------------------------------------------
    # System persona (used in every API call)
    # -----------------------------------------------------------------------
    _SYSTEM_PERSONA = (
        "You are a Principal Process Engineer with 20+ years of experience in "
        "oil & gas PFD review, HAZOP facilitation, and process design basis. "
        "You follow IEC, ISO 10628, and project-specific engineering standards. "
        "You respond only in valid JSON."
    )

    def __init__(self):
        api_key = (
            getattr(settings, "OPENAI_API_KEY", None)
            or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        self.client = OpenAI(api_key=api_key)
        print(f"[PFD_ANALYSIS] Initialized — model: {self.MODEL}, passes: 4")

    # -----------------------------------------------------------------------
    # Public API (unchanged contract)
    # -----------------------------------------------------------------------

    def analyze_pfd_document(
        self,
        pfd_file,
        reference_documents: Dict[str, Any] = None,
        drawing_metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        Run 4-pass analysis and return the merged result dict.

        Args:
            pfd_file: file-like object or path to the PFD PDF.
            reference_documents: {doc_type: file_storage_path} mapping.
            drawing_metadata: {drawing_number, revision, title, project_name}.

        Returns:
            {drawing_info, issues, summary, analysis_metadata}
        """
        try:
            fname = getattr(pfd_file, "name", "Unknown")
            print(f"[PFD_ANALYSIS] ▶ Starting 4-pass analysis — file: {fname}")

            # ── Render PFD pages ────────────────────────────────────────────
            pfd_images = self._render_pdf_pages(pfd_file)
            print(f"[PFD_ANALYSIS] Rendered {len(pfd_images)} page(s) at {self.PDF_ZOOM}× zoom")

            # ── Pass 1: Visual inventory ────────────────────────────────────
            inventory = self._pass1_visual_inventory(pfd_images)
            print(f"[PFD_ANALYSIS] Pass 1 complete — inventory extracted")

            # ── Pass 2: Reference document text extraction ──────────────────
            ref_context = self._pass2_extract_reference_content(reference_documents or {})
            print(f"[PFD_ANALYSIS] Pass 2 complete — ref context: {len(ref_context)} chars")

            # ── Pass 3: Deep systematic analysis ───────────────────────────
            issues_p3 = self._pass3_deep_systematic_check(
                pfd_images, inventory, ref_context, drawing_metadata or {}
            )
            print(f"[PFD_ANALYSIS] Pass 3 complete — {len(issues_p3)} raw issues")

            # ── Pass 4: Gap & missing element review ────────────────────────
            issues_p4 = self._pass4_gap_analysis(
                pfd_images, inventory, ref_context, drawing_metadata or {}
            )
            print(f"[PFD_ANALYSIS] Pass 4 complete — {len(issues_p4)} gap issues")

            # ── Merge, deduplicate, renumber ────────────────────────────────
            all_issues = self._merge_and_deduplicate(issues_p3, issues_p4)
            print(f"[PFD_ANALYSIS] Final merged issues: {len(all_issues)}")
            # ── CAG post-filter: remove hallucinated findings ─────────────────
            all_issues = self._cag_post_filter(all_issues, inventory)
            print(f"[PFD_ANALYSIS] After CAG post-filter: {len(all_issues)} issues")
            # ── Build drawing_info from inventory ───────────────────────────
            drawing_info = self._extract_drawing_info(inventory, drawing_metadata or {})

            # ── Summary ─────────────────────────────────────────────────────
            summary = self._generate_summary(all_issues)
            summary["passes_run"] = 4
            summary["inventory_equipment_count"] = len(
                inventory.get("equipment_tags", [])
            )
            summary["inventory_stream_count"] = len(
                inventory.get("stream_numbers", [])
            )

            return {
                "drawing_info": drawing_info,
                "issues": all_issues,
                "summary": summary,
                "analysis_metadata": {
                    "engine": "4-pass multi-stage",
                    "model": self.MODEL,
                    "pages_analyzed": len(pfd_images),
                    "reference_docs_used": len([
                        v for v in (reference_documents or {}).values()
                        if v and v != "null"
                    ]),
                    "timestamp": datetime.now().isoformat(),
                },
            }

        except Exception as e:
            print(f"[PFD_ANALYSIS ERROR] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise

    # -----------------------------------------------------------------------
    # Pass 1 — Visual Inventory Scan
    # -----------------------------------------------------------------------

    def _pass1_visual_inventory(self, pfd_images: List[str]) -> dict:
        """
        Ask the model to catalogue everything it can see on the PFD.
        Returns a structured inventory dict.
        """
        prompt = """Examine these PFD drawing image(s) in detail.

Extract and list EVERY visible element you can identify. Be exhaustive — do NOT skip anything.

Return a JSON object with EXACTLY these keys:
{
  "equipment_tags": ["list of every equipment tag visible, e.g. V-101, P-101A/B, E-201"],
  "stream_numbers": ["list of every stream number visible, e.g. 1, 2A, S-101"],
  "title_block": {
    "drawing_number": "...",
    "revision": "...",
    "project_name": "...",
    "client_name": "...",
    "date": "...",
    "document_title": "...",
    "approved_by": "...",
    "checked_by": "..."
  },
  "utility_headers": ["steam", "cooling water", etc. — every utility header line shown],
  "battery_limits": ["description of every battery limit / tie-in marker visible"],
  "process_units": ["names/labels of every process unit or section shown"],
  "notes": ["full text of every general note on the drawing"],
  "legend_items": ["list of any legend or symbol key entries visible"],
  "stream_data_table": true_or_false,
  "operating_conditions_shown": true_or_false,
  "control_elements": ["list any PFD-level control loops or symbols visible"],
  "safety_elements": ["any relief/vent/flare line or symbol visible"],
  "phase_indicators": ["any L/V/G phase labels or separators shown"],
  "missing_title_block_fields": ["list any title block field that appears BLANK or NOT FILLED IN"]
}"""

        try:
            result = self._vision_call(prompt, pfd_images, max_tokens=4000)
            if isinstance(result, dict):
                return result
        except Exception as e:
            print(f"[PFD_ANALYSIS] Pass 1 error: {e}")
        return {}

    # -----------------------------------------------------------------------
    # Pass 2 — Reference Document Text Extraction
    # -----------------------------------------------------------------------

    def _pass2_extract_reference_content(
        self, reference_docs: Dict[str, Any]
    ) -> str:
        """
        Read the text content from each uploaded reference document.
        Returns a single formatted context string for the AI.
        """
        LABELS = {
            "bfd": "Block Flow Diagram (BFD)",
            "process_description": "Process Description",
            "process_design_basis": "Process Design Basis",
            "operation_control_philosophy": "Operation & Control Philosophy",
            "scope_of_work": "Scope of Work",
            "legends_symbols": "Legends and Symbols",
            "equipment_data_sheet": "Equipment Data Sheet",
            "other_documents": "Other Reference Documents",
        }

        parts = []
        for doc_type, doc_path in reference_docs.items():
            if not doc_path or doc_path == "null":
                continue
            label = LABELS.get(doc_type, doc_type.replace("_", " ").title())
            text = self._read_document_text(doc_path)
            if text:
                snippet = text[: self.REF_DOC_TEXT_LIMIT]
                parts.append(f"=== {label} ===\n{snippet}\n")
            else:
                parts.append(f"=== {label} ===\n[File available but text could not be extracted]\n")

        return "\n".join(parts) if parts else ""

    def _read_document_text(self, storage_path: str) -> str:
        """Extract plain text from a PDF stored in Django's default_storage."""
        try:
            with default_storage.open(storage_path, "rb") as f:
                raw = f.read()

            # Try PyMuPDF text extraction
            doc = fitz.open(stream=raw, filetype="pdf")
            pages_text = []
            for page in doc:
                pages_text.append(page.get_text("text"))
            doc.close()
            text = "\n".join(pages_text).strip()
            if text:
                return text
        except Exception as e:
            print(f"[PFD_ANALYSIS] ref doc read error ({storage_path}): {e}")
        return ""

    # -----------------------------------------------------------------------
    # Pass 3 — Deep Systematic Analysis (40+ checks)
    # -----------------------------------------------------------------------

    _CHECKS_TEXT = """
MANDATORY VERIFICATION CHECKS — examine the PFD images and apply ALL 42 checks below.
Report an issue for EVERY check that fails or cannot be confirmed. Be specific.

=== TITLE BLOCK & DOCUMENTATION (Checks 1–7) ===
1.  Drawing number visible and matches the provided metadata (if any).
2.  Revision number / letter is shown and not blank.
3.  Project name is shown in the title block.
4.  Client/Owner name is shown in the title block.
5.  Document title correctly describes the process shown.
6.  "Approved by" and "Checked by" fields are not blank.
7.  Issue date or revision date is present.

=== EQUIPMENT (Checks 8–15) ===
8.  Every major equipment item (vessel, column, reactor, heat exchanger, pump,
    compressor, fired heater, filter, dryer) has a unique, legible tag number
    following the project tagging philosophy.
9.  Equipment tag numbers on PFD are consistent with the Equipment Data Sheet
    (if provided).
10. Parallel trains (A/B sparing) are clearly labeled.
11. Equipment type symbols are consistent and follow legend / ISO 10628-2.
12. No extra unlabeled equipment symbols exist on the drawing.
13. Equipment service descriptions (names) are shown next to each tag.
14. Heat exchangers clearly show shell-side vs tube-side process streams.
15. Pumps and compressors show suction and discharge directions.

=== PROCESS STREAMS (Checks 16–22) ===
16. All process stream lines carry a unique stream number (no two identical numbers).
17. Flow direction arrows are present on every process stream line.
18. Feed streams entering the battery limit are labeled and numbered.
19. Product / export streams leaving the battery limit are labeled and numbered.
20. Recycle streams are clearly marked and numbered.
21. Off-page / tie-in connectors carry matching reference numbers on both sides.
22. Stream data table is present or stream conditions (T, P, flow) are annotated.

=== MATERIAL BALANCE & PROCESS LOGIC (Checks 23–28) ===
23. Mass balance is visually consistent — every stream entering equipment must
    leave via at least one outlet stream (no orphan inlets or outlets).
24. Phase changes (L→V, V→L) are logically represented at separation or
    heat-exchange equipment.
25. Bypass lines shown are process-justified and numbered.
26. Process sequence from feed to product follows a logical engineering pathway.
27. No unexplained stream splits or merges without an equipment item.
28. All recycle and bypass streams connect back to an appropriate point.

=== UTILITIES (Checks 29–32) ===
29. Utility supply lines (steam, cooling water, fuel gas, nitrogen, instrument
    air, potable water, etc.) are shown where process equipment requires them.
30. Utility header labels are present and legible.
31. No utility stream is shown feeding into a process stream without a valid
    process reason.
32. Utility return / condensate lines are shown if applicable.

=== SAFETY & ENVIRONMENT (Checks 33–36) ===
33. Relief valve or pressure protection symbol is shown on pressurized vessels /
    equipment where required at PFD level.
34. Vent / flare connections are shown at PFD level where required by process safety.
35. Emergency depressurization or drain-to-safe-location is indicated if required.
36. Chemical injection points for corrosion inhibitor / neutralizer are noted if
    applicable to the process.

=== CONTROL & INSTRUMENTATION (Checks 37–39) ===
37. Only PFD-level control loops are shown (no detailed P&ID valve tags, no
    nozzle callouts, no hook-up details).
38. Key process control loops (level, pressure, flow, temperature) relevant to
    major equipment are indicated at PFD level.
39. No extraneous P&ID-level instrumentation (FT, LT numbers, etc.) appears.

=== NOTES & LEGENDS (Checks 40–42) ===
40. A "General Notes" section or note list is present; each note is numbered and
    referenced to elements on the drawing.
41. A legend / symbol key is present or referenced.
42. Notes are technically consistent with the process depicted on the PFD.
"""

    def _pass3_deep_systematic_check(
        self,
        pfd_images: List[str],
        inventory: dict,
        ref_context: str,
        drawing_metadata: dict,
    ) -> List[dict]:
        """Run 42-check deep systematic analysis."""

        inventory_block = ""
        if inventory:
            inventory_block = (
                "\n=== PFD INVENTORY (from visual scan) ===\n"
                + json.dumps(inventory, indent=2)
                + "\n"
            )

        cag_block = self._build_cag_context(inventory)

        metadata_block = ""
        if drawing_metadata:
            metadata_block = (
                "\n=== DRAWING METADATA PROVIDED BY USER ===\n"
                f"- Drawing Number : {drawing_metadata.get('drawing_number','Not provided')}\n"
                f"- Revision       : {drawing_metadata.get('revision','Not provided')}\n"
                f"- Title          : {drawing_metadata.get('title','Not provided')}\n"
                f"- Project        : {drawing_metadata.get('project_name','Not provided')}\n"
            )

        ref_block = ""
        if ref_context:
            ref_block = (
                "\n=== REFERENCE DOCUMENT CONTENT ===\n"
                + ref_context
                + "\n=== END REFERENCE DOCUMENTS ===\n"
            )

        prompt = f"""You are performing a deep systematic PFD verification.

{inventory_block}
{metadata_block}
{ref_block}
{cag_block}

{self._CHECKS_TEXT}

RULES:
- Report an issue for EVERY check that FAILS or cannot be confirmed from the image.
- Each issue must reference the specific element (tag number, stream number,
  title block field) by name.
- Do NOT invent equipment or streams not visible on the PFD.
- Do NOT report image quality issues.
- Severity guide:
    critical  = missing safety element, undefined battery limit, orphan process stream
    major     = missing tag, missing stream number, missing stream direction arrow,
                missing utility that is clearly required, inconsistency with ref docs
    minor     = missing note reference, blank title block field, unlabeled bypass
    observation = improvement suggestion, best-practice recommendation

EVIDENCE FIELD — MANDATORY WRITING STANDARD:
Every finding MUST include an "evidence" field following this three-part VISUAL → GAP → STANDARD structure:
  VISUAL:   Describe exactly what IS drawn — element name/tag, position on drawing (quadrant/zone),
            visible connections, labels present.
  GAP:      State precisely what annotation, element, or connection is absent or non-compliant.
  STANDARD: Name the governing standard and clause, e.g. ISO 10628-2:2012 §5.3, IEC 61511-1:2016 §10,
            API 14C:2017, ASME VIII UG-135.  Never write 'per applicable standard' — name it explicitly.

Examples of GOOD evidence:
  "VISUAL: Condenser E-101 symbol is drawn in Top-Right zone connected to column C-101 overhead.
  GAP: No cooling water supply and return utility connection lines are shown at E-101 shell-side.
  STANDARD: ISO 10628-2:2012 §5.3 requires all utility connections to be shown on the PFD."

  "VISUAL: Separator V-201 is visible in the Middle-Center zone with a feed inlet stream.
  GAP: No liquid-draw bottom outlet stream is shown leaving V-201; only the vapour overhead stream is present.
  STANDARD: ISO 10628-2:2012 §5.3 — all process outlet streams from vessels must be shown;
  engineering practice requires both vapour overhead and liquid bottom streams on a separator."

Return ONLY a valid JSON object:
{{
  "issues": [
    {{
      "serial_number": 1,
      "issue_found": "Specific finding referencing element name/number",
      "action_required": "Specific corrective action",
      "evidence": "VISUAL: [what is drawn]. GAP: [what is missing]. STANDARD: [standard and clause]",
      "severity": "critical|major|minor|observation",
      "category": "Equipment|Streams|Control|Documentation|Safety|Material Balance|Utilities|Process Design|Other",
      "check_number": 1,
      "approval": "Pending",
      "remark": "Pending"
    }}
  ]
}}"""

        try:
            result = self._vision_call(prompt, pfd_images, max_tokens=self.MAX_TOKENS)
            return result.get("issues", [])
        except Exception as e:
            print(f"[PFD_ANALYSIS] Pass 3 error: {e}")
            return []

    # -----------------------------------------------------------------------
    # Pass 4 — Gap & Missing Element Review
    # -----------------------------------------------------------------------

    def _pass4_gap_analysis(
        self,
        pfd_images: List[str],
        inventory: dict,
        ref_context: str,
        drawing_metadata: dict,
    ) -> List[dict]:
        """Second-eye sweep focused on absent or incomplete elements."""

        inventory_block = ""
        if inventory:
            inventory_block = (
                "\n=== CONFIRMED VISIBLE INVENTORY ===\n"
                + json.dumps(inventory, indent=2)
                + "\n"
            )

        ref_block = ""
        if ref_context:
            ref_block = (
                "\n=== REFERENCE DOCUMENT CONTENT ===\n"
                + ref_context[:6000]
                + "\n"
            )

        cag_block = self._build_cag_context(inventory)

        prompt = f"""You are performing a PFD Gap Analysis — your sole focus is on what is
ABSENT, INCOMPLETE, or INCONSISTENT.

{inventory_block}
{ref_block}
{cag_block}

Examine the PFD image(s) and answer these gap questions. Report an issue for
every gap you find:

GAP-1.  Are there any equipment items in the reference Equipment Data Sheet that
        are NOT shown on the PFD? (List each missing tag.)
GAP-2.  Are there streams implied by the process but not numbered on the PFD?
GAP-3.  Are there equipment items on the PFD with NO connecting process streams?
GAP-4.  Does the title block have any BLANK fields (drawing number, revision, project,
        client, date, approved-by, checked-by)?
GAP-5.  Are there utility connections that appear required by the equipment type but
        are NOT shown (e.g., cooling water to a condenser, steam to a reboiler)?
GAP-6.  Is a stream data table referenced but NOT present on the drawing?
GAP-7.  Are there process notes referenced in the drawing but the note text is missing?
GAP-8.  Are there battery limit markers without matching stream numbers or labels?
GAP-9.  Is the legends / symbol key absent or referenced but not present?
GAP-10. Are there equipment items where phase (L/V/G) cannot be determined from
        the inlet/outlet streams and no phase indicator is shown?
GAP-11. If a separator, flash vessel, or distillation column is shown, are both
        vapour-overhead and liquid-bottom streams present?
GAP-12. Are any general notes section entirely absent when notes would be expected?
GAP-13. Are control loop descriptions absent for major process control points?
GAP-14. Are there any feed or product streams not labeled at the battery limit?
GAP-15. Based on the process description (if provided), is any major process step
        such as pre-treatment, separation, heat recovery, or product treating MISSING
        from the PFD?

RULES:
- Only report gaps you can confirm from the image or from a discrepancy with
  the reference documents.
- Each gap issue must identify the specific missing element by name/number.
- Do NOT duplicate issues that are clearly already captured in a prior check.
- If no gap is found for a question, do NOT force an issue.

EVIDENCE FIELD — MANDATORY WRITING STANDARD:
Every finding MUST include an "evidence" field following this three-part VISUAL → GAP → STANDARD structure:
  VISUAL:   Describe exactly what IS drawn — element name/tag, position on drawing (quadrant/zone),
            visible connections, labels present.
  GAP:      State precisely what annotation, element, or connection is absent or non-compliant.
  STANDARD: Name the governing standard and clause, e.g. ISO 10628-2:2012 §5.3, IEC 61508, API 14C:2017.
            Never write 'per applicable standard' — name it explicitly.

Return ONLY a valid JSON object:
{{
  "issues": [
    {{
      "serial_number": 1,
      "issue_found": "Specific missing element description",
      "action_required": "Specific corrective action",
      "evidence": "VISUAL: [what is drawn]. GAP: [what is missing/incomplete]. STANDARD: [standard and clause]",
      "severity": "critical|major|minor|observation",
      "category": "Equipment|Streams|Control|Documentation|Safety|Material Balance|Utilities|Process Design|Other",
      "gap_check": "GAP-1",
      "approval": "Pending",
      "remark": "Pending"
    }}
  ]
}}"""

        try:
            result = self._vision_call(prompt, pfd_images, max_tokens=8000)
            return result.get("issues", [])
        except Exception as e:
            print(f"[PFD_ANALYSIS] Pass 4 error: {e}")
            return []

    # -----------------------------------------------------------------------
    # CAG — Context-Augmented Generation
    # -----------------------------------------------------------------------

    def _build_cag_context(self, inventory: dict) -> str:
        """
        Build a CAG boundary block from Pass 1 inventory to inject into
        Passes 3 and 4. Forces the model to only reference elements that
        were visually confirmed on the drawing, preventing hallucination of
        equipment tags, stream numbers, and notes that do not exist.
        """
        sep = '═' * 68
        equipment_tags = inventory.get("equipment_tags", [])
        stream_numbers = [str(s) for s in inventory.get("stream_numbers", [])]
        notes = inventory.get("notes", [])
        utility_headers = inventory.get("utility_headers", [])
        battery_limits = inventory.get("battery_limits", [])
        process_units = inventory.get("process_units", [])

        lines = [
            sep,
            'CAG CONTEXT — CONTEXT-AUGMENTED GENERATION BOUNDARY',
            sep,
            '',
            'You are operating in CAG (Context-Augmented Generation) mode.',
            'ONLY generate findings for elements confirmed by the inventory below',
            'OR elements you visually confirm RIGHT NOW on the attached drawing image.',
            'Do NOT use training-data memory to invent elements not present here.',
            '',
            '── CONFIRMED EQUIPMENT TAGS ON THIS PFD ──',
        ]

        if equipment_tags:
            for tag in sorted(equipment_tags)[:60]:
                lines.append(f'  {tag}')
            lines.append(f'  (Total: {len(equipment_tags)} equipment tags from visual scan)')
            lines.append('')
            lines.append(
                'RULE ➜ Every equipment finding MUST reference one of the tags above '
                'OR a tag you can READ directly on the image.'
            )
            lines.append('Do NOT invent equipment tags not listed here.')
        else:
            lines.append('  (No equipment tags extracted — rely entirely on visual scan.)')

        lines += [
            '',
            '── CONFIRMED STREAM NUMBERS ON THIS PFD ──',
        ]
        if stream_numbers:
            for sn in sorted(stream_numbers)[:60]:
                lines.append(f'  {sn}')
            lines.append(f'  (Total: {len(stream_numbers)} stream numbers from visual scan)')
            lines.append('')
            lines.append(
                'RULE ➜ Every stream finding MUST reference one of the numbers above '
                'OR a number you can READ directly on the image.'
            )
            lines.append('Do NOT invent stream numbers not listed here.')
        else:
            lines.append('  (No stream numbers extracted — rely on visual scan.)')

        lines += [
            '',
            '── UTILITY HEADERS VISIBLE ON THIS PFD ──',
        ]
        if utility_headers:
            for uh in utility_headers:
                lines.append(f'  {uh}')
            lines.append(
                'RULE ➜ Only report missing utility connections for equipment listed '
                'in the confirmed equipment tags above.'
            )
        else:
            lines.append('  (No utility headers detected by visual scan.)')

        lines += [
            '',
            '── BATTERY LIMITS / TIE-INS ──',
        ]
        if battery_limits:
            for bl in battery_limits:
                lines.append(f'  {bl}')
        else:
            lines.append('  (No battery limits extracted — rely on visual scan.)')

        lines += [
            '',
            '── PROCESS UNITS / SECTIONS ──',
        ]
        if process_units:
            for pu in process_units:
                lines.append(f'  {pu}')
        else:
            lines.append('  (No process unit labels extracted — rely on visual scan.)')

        lines += [
            '',
            '── NOTES INVENTORY ──',
        ]
        if notes:
            lines.append(f'Visual scan detected {len(notes)} note(s) on this drawing:')
            for n in notes:
                lines.append(f'  {n}')
            lines.append('')
            lines.append(
                'RULE ➜ Only generate notes-related findings for the notes listed above.'
            )
        else:
            lines += [
                '  Visual scan detected ZERO general notes on this drawing.',
                '',
                '⚠ CRITICAL RULE: Because NO notes were found by visual scan, you MUST NOT',
                '  generate any finding about missing or incorrect general notes.',
                '  Do NOT invent a General Notes section that does not exist on this drawing.',
                '  If you cannot visually confirm a NOTES section in the image, skip those checks.',
            ]

        lines += [
            '',
            'CAG SUMMARY RULES (apply to EVERY finding):',
            '  1. Equipment refs  → must match confirmed equipment tags above or visible on image.',
            '  2. Stream refs     → must match confirmed stream numbers above or visible on image.',
            '  3. Notes findings  → skip entirely if ZERO notes detected by visual scan (see above).',
            '  4. Utility gaps    → only for equipment confirmed in the tag list above.',
            '  5. Never fabricate tags, stream numbers, or note references from training memory.',
            '  6. If uncertain whether an element exists — it is NOT a finding.',
            sep,
        ]
        return '\n'.join(lines)

    def _cag_post_filter(self, issues: List[dict], inventory: dict) -> List[dict]:
        """
        Post-processing CAG filter: removes or demotes findings referencing
        elements not confirmed in the Pass 1 inventory.

        Conservative:
        - Removes notes-related findings when inventory shows no notes.
        - Demotes major/critical findings that reference specific equipment or
          stream numbers not found in inventory to 'observation'.
        - Keeps all observation-severity findings unchanged.
        """
        equipment_set: Set[str] = {
            t.upper().strip() for t in inventory.get("equipment_tags", [])
        }
        stream_set: Set[str] = {
            str(s).upper().strip() for s in inventory.get("stream_numbers", [])
        }
        no_notes = not inventory.get("notes", [])

        # Build partial-match fragments for equipment tags (e.g. "V-101" from "V-101A/B")
        eq_fragments: Set[str] = set()
        for tag in equipment_set:
            parts = tag.split('-')
            if len(parts) >= 2:
                eq_fragments.add('-'.join(parts[:2]))
                eq_fragments.add('-'.join(parts[:3]))

        def matches_inventory(text: str) -> bool:
            text_up = text.upper()
            for tag in equipment_set:
                if tag in text_up:
                    return True
            for frag in eq_fragments:
                if frag in text_up:
                    return True
            for sn in stream_set:
                if re.search(r'\b' + re.escape(sn) + r'\b', text_up):
                    return True
            return False

        NOTES_KEYWORDS = (
            "general note", "notes section", "note list",
            "note reference", "general notes", "notes are absent",
        )
        CHECKABLE_CATEGORIES = {"equipment", "streams", "material balance", "utilities"}

        kept, removed_count = [], 0
        for issue in issues:
            issue_text = issue.get("issue_found", "")
            evidence = issue.get("evidence", "")
            severity = (issue.get("severity") or "observation").lower()
            category = (issue.get("category") or "").lower()

            # Rule 1: notes-related findings when no notes exist → remove
            if no_notes and any(kw in issue_text.lower() for kw in NOTES_KEYWORDS):
                removed_count += 1
                continue

            # Rule 2: for checkable categories with known inventory, verify references
            if category in CHECKABLE_CATEGORIES and (equipment_set or stream_set):
                if not matches_inventory(issue_text) and not matches_inventory(evidence):
                    # Visual evidence phrase overrides the filter
                    visual_phrases = (
                        'visually confirm', 'visible on', 'can see', 'visible in',
                        'drawing shows', 'shown on', 'present on',
                    )
                    if any(ph in evidence.lower() for ph in visual_phrases):
                        kept.append(issue)
                        continue
                    # Observation severity: keep as-is
                    if severity == 'observation':
                        kept.append(issue)
                        continue
                    # Demote to observation with CAG annotation
                    issue = dict(issue)
                    issue['severity'] = 'observation'
                    issue['evidence'] = (
                        (issue.get('evidence') or '') +
                        ' [CAG: referenced element could not be confirmed in visual'
                        ' inventory — finding demoted to observation]'
                    ).strip()
                    kept.append(issue)
                    continue

            kept.append(issue)

        if removed_count:
            print(f'[CAG-PFD] Post-filter removed {removed_count} hallucinated note findings')
        return kept

    # -----------------------------------------------------------------------
    # Merge & deduplicate
    # -----------------------------------------------------------------------

    def _merge_and_deduplicate(
        self, issues_p3: List[dict], issues_p4: List[dict]
    ) -> List[dict]:
        """
        Merge Pass-3 and Pass-4 issues, remove near-duplicates, renumber.
        Deduplication: if two issues share the same first 60 chars of 'issue_found',
        keep the one with the higher severity.
        """
        SEVERITY_RANK = {"critical": 4, "major": 3, "minor": 2, "observation": 1}
        combined = issues_p3 + issues_p4

        # Normalise fields
        seen: Dict[str, dict] = {}
        for issue in combined:
            # Normalise severity / category
            sev = issue.get("severity", "observation").lower()
            if sev not in self.VALID_SEVERITIES:
                sev = "observation"
            issue["severity"] = sev

            cat = issue.get("category", "Other")
            if cat not in self.VALID_CATEGORIES:
                cat = "Other"
            issue["category"] = cat

            issue.setdefault("approval", "Pending")
            issue.setdefault("remark", "Pending")

            # Dedup key = first 60 chars of issue_found (case-insensitive)
            key = issue.get("issue_found", "")[:60].lower().strip()
            if key in seen:
                existing_rank = SEVERITY_RANK.get(seen[key]["severity"], 1)
                new_rank = SEVERITY_RANK.get(sev, 1)
                if new_rank > existing_rank:
                    seen[key] = issue
            else:
                seen[key] = issue

        # Sort: critical first, then major, minor, observation
        ordered = sorted(
            seen.values(),
            key=lambda x: SEVERITY_RANK.get(x.get("severity", "observation"), 1),
            reverse=True,
        )

        # Renumber
        for idx, issue in enumerate(ordered, start=1):
            issue["serial_number"] = idx
            # Remove internal pass metadata keys before returning
            issue.pop("check_number", None)
            issue.pop("gap_check", None)

        return ordered

    # -----------------------------------------------------------------------
    # Draw info helper
    # -----------------------------------------------------------------------

    def _extract_drawing_info(self, inventory: dict, metadata: dict) -> dict:
        tb = inventory.get("title_block", {}) if inventory else {}
        return {
            "drawing_number": (
                tb.get("drawing_number")
                or metadata.get("drawing_number")
                or ""
            ),
            "revision": (
                tb.get("revision")
                or metadata.get("revision")
                or ""
            ),
            "project_name": (
                tb.get("project_name")
                or metadata.get("project_name")
                or ""
            ),
            "client_name": tb.get("client_name") or "",
        }

    # -----------------------------------------------------------------------
    # Low-level OpenAI vision call
    # -----------------------------------------------------------------------

    def _vision_call(
        self,
        prompt: str,
        images_base64: List[str],
        max_tokens: int = 8000,
    ) -> dict:
        """Single call to GPT-4o Vision; returns parsed JSON dict."""
        content = [{"type": "text", "text": prompt}]
        for img in images_base64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img}",
                    "detail": "high",
                },
            })

        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {"role": "system", "content": self._SYSTEM_PERSONA},
                {"role": "user", "content": content},
            ],
            max_tokens=max_tokens,
            temperature=self.TEMPERATURE,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        return _safe_json(raw)

    # -----------------------------------------------------------------------
    # PDF rendering
    # -----------------------------------------------------------------------

    def _render_pdf_pages(self, pdf_file) -> List[str]:
        """Render every PDF page to a high-resolution base64 PNG."""
        if hasattr(pdf_file, "read"):
            pdf_bytes = pdf_file.read()
            if hasattr(pdf_file, "seek"):
                pdf_file.seek(0)
        else:
            with open(pdf_file, "rb") as f:
                pdf_bytes = f.read()

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        mat = fitz.Matrix(self.PDF_ZOOM, self.PDF_ZOOM)

        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))

        doc.close()
        return images

    # Keep legacy name as alias for backwards compatibility
    def _extract_pdf_pages(self, pdf_file) -> List[str]:
        return self._render_pdf_pages(pdf_file)

    # -----------------------------------------------------------------------
    # Summary helpers (public — used by views)
    # -----------------------------------------------------------------------

    def _generate_summary(self, issues: List[dict]) -> dict:
        summary = {
            "total_issues": len(issues),
            "critical_count": 0,
            "major_count": 0,
            "minor_count": 0,
            "observation_count": 0,
        }
        for issue in issues:
            sev = issue.get("severity", "observation").lower()
            if sev == "critical":
                summary["critical_count"] += 1
            elif sev == "major":
                summary["major_count"] += 1
            elif sev == "minor":
                summary["minor_count"] += 1
            else:
                summary["observation_count"] += 1
        return summary

    def generate_report_summary(self, issues: List[dict]) -> dict:
        """Generate approval-status summary (used by views after bulk updates)."""
        summary = {"approved_count": 0, "ignored_count": 0, "pending_count": 0}
        for issue in issues:
            st = issue.get("status", "pending").lower()
            if st == "approved":
                summary["approved_count"] += 1
            elif st == "ignored":
                summary["ignored_count"] += 1
            else:
                summary["pending_count"] += 1
        return summary
