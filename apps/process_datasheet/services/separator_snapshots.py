"""Immutable, checksummed separator snapshots, distinct from HMB workbooks."""
import json
import math

from .separator_sizing import CRITERIA, INPUTS, PIPES, VERSION, calculate

FORMAT = "radai.separator.snapshot"
FORMAT_VERSION = 1
EXPORT_KIND = "separator_snapshot"
MAX_BYTES = 2 * 1024 * 1024


def without_separator_snapshots(queryset):
    from django.db.models import Q
    return queryset.filter(Q(metadata__export_kind__isnull=True) | ~Q(metadata__export_kind=EXPORT_KIND))


def calculator_schema():
    return {
        "version": VERSION,
        "inputs": {key: {"label": spec[0], "unit": spec[1]} for key, spec in INPUTS.items()},
        "criteria": {key: {"label": spec[0], "unit": spec[1], "default": spec[2]} for key, spec in CRITERIA.items()},
        "nozzle_table": [{"nps": nps, "id_mm": diameter} for nps, diameter in PIPES],
    }


def text(value, name, maximum=255):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name}: provide nonempty text of at most {maximum} characters.")
    return value


def validate_basis(basis):
    if not isinstance(basis, dict) or set(basis) != {"request", "sources", "excluded", "overrides", "metadata"}:
        raise ValueError("Provide the complete separator input/provenance basis.")
    metadata = basis["metadata"]
    if not isinstance(metadata, dict) or not {"tag", "service", "stream"} <= set(metadata) or not set(metadata) <= {"tag", "service", "stream", "document"}:
        raise ValueError("Equipment tag, service and stream are required.")
    for key in ("tag", "service", "stream"):
        val = metadata[key]
        text(val, f"metadata.{key}")
    document = metadata.get("document", {})
    permitted_document = {"project", "client", "documentNo", "revision", "date", "preparedChecked",
                          "quantity", "designCode", "sizingBasis", "inletDevice", "mistEliminator",
                          "liquidOutlets", "otherInternals"}
    if not isinstance(document, dict) or not set(document) <= permitted_document or any(
            not isinstance(value, str) or len(value) > 512 for value in document.values()):
        raise ValueError("Document metadata must contain supported text fields of at most 512 characters.")
    if "quantity" in document and (not document["quantity"].isdigit() or not 1 <= int(document["quantity"]) <= 1000):
        raise ValueError("Document quantity must be an integer between 1 and 1000.")
    sources = basis["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
        raise ValueError("Snapshot provenance must contain 1 to 100 source cases.")
    ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Invalid source case provenance.")
        for field in ("id", "name", "fileName", "sheetName", "streamName"):
            text(source.get(field), f"source.{field}")
        if source["id"] in ids:
            raise ValueError("Source case IDs must be unique.")
        ids.add(source["id"])
        mapping = source.get("mapping")
        if not isinstance(mapping, dict) or set(mapping) != set(INPUTS):
            raise ValueError("Source case property mapping is incomplete.")
        for key, mapped in mapping.items():
            if not isinstance(mapped, dict) or set(mapped) != {"value", "source"}:
                raise ValueError(f"mapping.{key}: invalid property provenance.")
            val = mapped["value"]
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)):
                raise ValueError(f"mapping.{key}: mapped value must be finite or null.")
            original = mapped["source"]
            if original is not None:
                if not isinstance(original, dict) or set(original) != {"property", "section", "unit", "value", "edited"}:
                    raise ValueError(f"mapping.{key}: source evidence is incomplete.")
                text(original["property"], "source property")
                text(original["unit"], "source unit")
                if not isinstance(original["section"], str) or len(original["section"]) > 255 or not isinstance(original["edited"], bool):
                    raise ValueError("Source phase or reviewed-edit flag is invalid.")
                raw = original["value"]
                if isinstance(raw, bool) or raw is not None and not isinstance(raw, (int, float, str)):
                    raise ValueError("Source value must be a scalar.")
    excluded = basis["excluded"]
    overrides = basis["overrides"]
    if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded) or len(set(excluded)) != len(excluded) or not set(excluded) <= ids:
        raise ValueError("Excluded cases must refer to unique source cases.")
    if not isinstance(overrides, dict):
        raise ValueError("Sizing overrides must be an object.")
    permitted = {f"{source['id']}:{key}" for source in sources for key in INPUTS}
    if not set(overrides) <= permitted:
        raise ValueError("Overrides must refer to saved source case properties.")
    for val in overrides.values():
        if not isinstance(val, str) or len(val) > 100 or val.strip() and not math.isfinite(float(val)):
            raise ValueError("Override entries must be finite numeric text or blank.")
    request = basis["request"]
    if not isinstance(request, dict) or not isinstance(request.get("cases"), list):
        raise ValueError("Calculation request is missing.")
    expected = []
    for source in sources:
        if source["id"] in excluded:
            continue
        inputs = {}
        for key, mapped in source["mapping"].items():
            override = overrides.get(f"{source['id']}:{key}")
            inputs[key] = mapped["value"] if override is None else float(override) if override.strip() else None
        expected.append({"id": source["id"], "name": source["name"], "inputs": inputs})
    if request["cases"] != expected:
        raise ValueError("Used inputs do not match source provenance and explicit sizing overrides.")


def build_snapshot(basis, calculator_version, saved_at):
    if calculator_version != VERSION:
        raise ValueError("Calculator version changed. Confirm and recalculate before saving.")
    validate_basis(basis)
    result = calculate(basis["request"])
    snapshot = {"format": FORMAT, "format_version": FORMAT_VERSION,
                "calculator_version": VERSION, "saved_at": saved_at,
                "basis": basis, "schema": calculator_schema(), "result": result}
    content = json.dumps(snapshot, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(content) > MAX_BYTES:
        raise ValueError("Separator snapshot exceeds the 2 MB limit.")
    return snapshot, content


def decode_snapshot(content):
    if len(content) > MAX_BYTES:
        raise ValueError("Saved snapshot exceeds the 2 MB limit.")
    def invalid_constant(value):
        raise ValueError(f"Invalid numeric constant in saved snapshot: {value}.")
    snapshot = json.loads(content, parse_constant=invalid_constant)
    if not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT or snapshot.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported separator snapshot format.")
    text(snapshot.get("calculator_version"), "Saved calculator version")
    text(snapshot.get("saved_at"), "Saved timestamp")
    validate_basis(snapshot.get("basis"))
    if not isinstance(snapshot.get("result"), dict) or not isinstance(snapshot.get("schema"), dict):
        raise ValueError("Saved calculation results or schema are missing.")
    if snapshot["result"].get("version") != snapshot["calculator_version"] or snapshot["schema"].get("version") != snapshot["calculator_version"]:
        raise ValueError("Saved calculator version does not match the result/schema.")
    return snapshot
