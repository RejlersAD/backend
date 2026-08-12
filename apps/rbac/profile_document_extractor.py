"""Local text/OCR extraction for user profile documents.

Identity documents contain sensitive data, so extraction intentionally runs
inside RADAI and does not send file contents to a third-party AI service.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path


DATE_VALUE = r"(?:\d{1,2}[\s./-]\d{1,2}[\s./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"


def _ocr_image(image, document_type: str = "") -> str:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    image = ImageOps.exif_transpose(image).convert("RGB")
    # Enlarging small card photos significantly improves Tesseract accuracy.
    if image.width < 1800:
        scale = 1800 / max(image.width, 1)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    grayscale = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(2.2)
    grayscale = grayscale.filter(ImageFilter.SHARPEN)

    results = [pytesseract.image_to_string(grayscale, config="--psm 11")]
    # Security documents have fine background patterns. A conservative binary
    # threshold removes most of that pattern while retaining the black labels.
    thresholded = grayscale.point(lambda pixel: 0 if pixel < 115 else 255, mode="1")
    results.append(pytesseract.image_to_string(thresholded, config="--psm 11"))
    width, height = grayscale.size

    def read_zone(box, psm=7, threshold=100, numeric_only=True) -> str:
        zone = ImageOps.autocontrast(ImageOps.grayscale(image.crop(box)), cutoff=1)
        target_width = 1800
        target_height = max(250, int(zone.height * target_width / max(zone.width, 1)))
        zone = zone.resize((target_width, target_height))
        zone = ImageEnhance.Contrast(zone).enhance(2)
        if threshold is not None:
            zone = zone.point(lambda pixel: 0 if pixel < threshold else 255, mode="1")
        whitelist = " -c tessedit_char_whitelist=0123456789-/" if numeric_only else ""
        return pytesseract.image_to_string(zone, config=f"--psm {psm}{whitelist}").strip()

    if document_type == "emirates_id":
        # Front-side Emirates IDs have stable zones for the ID number and dates.
        # OCRing these zones together avoids portraits, emblems and Arabic text
        # disrupting page segmentation while preserving label-based parsing.
        zones = [
            grayscale.crop((int(width * .34), int(height * .25), int(width * .78), int(height * .43))),
            grayscale.crop((int(width * .28), int(height * .66), int(width * .78), int(height * .94))),
        ]
        zone_width = max(zone.width for zone in zones)
        zone_height = sum(zone.height for zone in zones) + 40
        combined = Image.new("L", (zone_width, zone_height), 255)
        y_offset = 0
        for zone in zones:
            combined.paste(zone, (0, y_offset))
            y_offset += zone.height + 40
        combined = combined.point(lambda pixel: 0 if pixel < 130 else 255, mode="1")
        results.append(pytesseract.image_to_string(combined, config="--psm 6"))

        # Ratio-based zones support different resolutions of the standard
        # front-side card while excluding the security pattern and portraits.
        id_value = read_zone((
            int(width * .379), int(height * .331),
            int(width * .652), int(height * .403),
        ))
        issue_value = read_zone((
            int(width * .371), int(height * .762),
            int(width * .576), int(height * .812),
        ))
        expiry_value = read_zone((
            int(width * .371), int(height * .856),
            int(width * .576), int(height * .906),
        ))
        if id_value:
            results.append(f"ID Number: {id_value}")
        if issue_value:
            results.append(f"Issuing Date: {issue_value}")
        if expiry_value:
            results.append(f"Expiry Date: {expiry_value}")

    if document_type == "driving_license":
        # Common UAE licence photos include a small border/background around
        # the card. These focused zones isolate the bold machine-readable data.
        number_value = read_zone((
            int(width * .51), int(height * .43),
            int(width * .72), int(height * .51),
        ), psm=13)
        issue_value = read_zone((
            int(width * .49), int(height * .74),
            int(width * .75), int(height * .82),
        ), psm=13)
        expiry_value = read_zone((
            int(width * .49), int(height * .81),
            int(width * .75), int(height * .89),
        ), psm=13, threshold=None)
        place_value = read_zone((
            int(width * .40), int(height * .88),
            int(width * .69), int(height * .97),
        ), psm=6, threshold=80, numeric_only=False)
        if number_value:
            results.append(f"License No: {number_value}")
        if issue_value:
            results.append(f"Issue Date: {issue_value}")
        if expiry_value:
            results.append(f"Expiry Date: {expiry_value}")
        if place_value:
            results.append(f"Place of Issue: {place_value}")

    return "\n".join(results)


def extract_text(
    file_bytes: bytes,
    filename: str,
    content_type: str = "",
    document_type: str = "",
) -> str:
    suffix = Path(filename or "").suffix.lower()
    is_pdf = suffix == ".pdf" or content_type == "application/pdf"

    if is_pdf:
        import fitz

        text_parts = []
        with fitz.open(stream=file_bytes, filetype="pdf") as document:
            for page in list(document)[:3]:
                embedded = page.get_text("text").strip()
                if embedded:
                    text_parts.append(embedded)
                # Scanned pages typically have little or no embedded text.
                if len(embedded) < 80:
                    from PIL import Image

                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                    text_parts.append(_ocr_image(image, document_type))
        return "\n".join(text_parts)

    from PIL import Image

    with Image.open(io.BytesIO(file_bytes)) as image:
        return _ocr_image(image, document_type)


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return " ".join(match.group(1).strip(" :|-\t").split())
    return ""


def _iso_date(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"\s+", " ", value.strip().replace(",", ""))
    formats = (
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
        "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y",
    )
    for date_format in formats:
        try:
            parsed = datetime.strptime(cleaned, date_format).date()
            # Avoid accepting implausible OCR results.
            if 1940 <= parsed.year <= 2100:
                return parsed.isoformat()
        except ValueError:
            continue
    return ""


def _passport_mrz_metadata(text: str) -> dict:
    """Parse passport number and expiry from an ICAO TD3 MRZ second line."""
    for raw_line in text.splitlines():
        line = raw_line.upper().replace("£", "E").replace(" ", "")
        line = re.sub(r"[^A-Z0-9<]", "", line)
        match = re.search(
            r"([A-Z][A-Z0-9<]{8})\d([A-Z]{3})(\d{6})\d([MF<])(\d{6})\d",
            line,
        )
        if not match:
            continue
        passport_number = match.group(1).replace("<", "").strip()
        expiry_digits = match.group(5)
        try:
            year = int(expiry_digits[:2])
            # Passport expiry dates in current documents are in this century.
            expiry = datetime.strptime(f"20{year:02d}{expiry_digits[2:]}", "%Y%m%d").date()
        except ValueError:
            continue
        return {
            "document_number": passport_number,
            "expiry_date": expiry.isoformat(),
        }
    return {}


def extract_profile_document_metadata(
    file_bytes: bytes,
    filename: str,
    content_type: str = "",
    document_type: str = "",
) -> dict:
    text = extract_text(file_bytes, filename, content_type, document_type)
    normalized = re.sub(r"[ \t]+", " ", text)

    number_patterns = [
        rf"(?:document|passport|licen[cs]e|identity|id|card|policy)\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9 /\-]{{4,30}})",
        r"\b(784[- ]?\d{4}[- ]?\d{7}[- ]?\d)\b",
    ]
    if document_type == "emirates_id":
        number_patterns.reverse()

    issue_raw = _first_match(normalized, [
        rf"(?:date\s+of\s+issue|issue\s+date|issuing\s+date|issued\s+on|issuance\s+date)[^\d]{{0,80}}({DATE_VALUE})",
    ])
    expiry_raw = _first_match(normalized, [
        rf"(?:date\s+of\s+expiry|expiry\s+date|expiration\s+date|expires(?:\s+on)?|valid\s+until|valid\s+to)[^\d]{{0,80}}({DATE_VALUE})",
    ])
    authority = _first_match(normalized, [
        r"(?:issuing\s+authority|issued\s+by|authority)\s*[:\-]?\s*([^\n\r]{3,80})",
    ])

    lowered = normalized.lower()
    if not authority:
        if "roads and transport authority" in lowered or re.search(r"\brta\b", lowered):
            authority = "Roads and Transport Authority (RTA)"
        elif document_type == "driving_license":
            emirate_authorities = {
                "sharjah": "Sharjah Licensing Authority",
                "dubai": "Roads and Transport Authority (RTA)",
                "abu dhabi": "Abu Dhabi Police",
                "ajman": "Ajman Police",
                "fujairah": "Fujairah Police",
                "ras al khaimah": "Ras Al Khaimah Police",
                "umm al quwain": "Umm Al Quwain Police",
            }
            authority = next(
                (label for emirate, label in emirate_authorities.items() if emirate in lowered),
                "",
            )
        elif "federal authority for identity" in lowered or "identity citizenship customs" in lowered:
            authority = "Federal Authority for Identity, Citizenship, Customs and Port Security"
        elif document_type == "emirates_id":
            authority = "Federal Authority for Identity, Citizenship, Customs and Port Security"
        elif document_type == "visa" and ("united arab emirates" in lowered or "uae" in lowered):
            authority = "United Arab Emirates Government"

    metadata = {
        "document_number": _first_match(normalized, number_patterns),
        "issuing_authority": authority,
        "issue_date": _iso_date(issue_raw),
        "expiry_date": _iso_date(expiry_raw),
    }

    if document_type == "passport":
        from datetime import date, timedelta

        # MRZ data has fixed positions and check digits, making it more reliable
        # than labels on a multilingual/scanned passport page.
        metadata.update(_passport_mrz_metadata(text))

        if re.search(
            r"MAIN\s+DEPARTMENT\s+FOR\s+IMMIGRATION\s+AND\s+NATIONALITY\s+AFFAIRS",
            normalized,
            flags=re.IGNORECASE,
        ):
            metadata["issuing_authority"] = (
                "Main Department for Immigration and Nationality Affairs"
            )
        elif len(metadata.get("issuing_authority", "")) < 5:
            metadata["issuing_authority"] = ""

        # OCR often emits both date labels before their values. Select the most
        # recent printed date before the MRZ expiry as the issue date.
        expiry_iso = metadata.get("expiry_date")
        if expiry_iso:
            expiry = date.fromisoformat(expiry_iso)
            candidates = []
            for raw_date in re.findall(DATE_VALUE, normalized, flags=re.IGNORECASE):
                iso_value = _iso_date(raw_date)
                if not iso_value:
                    continue
                parsed = date.fromisoformat(iso_value)
                if expiry - timedelta(days=15 * 366) <= parsed < expiry:
                    candidates.append(parsed)
            if candidates:
                metadata["issue_date"] = max(candidates).isoformat()

    detected_fields = [key for key, value in metadata.items() if value]
    return {
        **metadata,
        "detected_fields": detected_fields,
        "requires_review": True,
        "message": (
            f"Detected {len(detected_fields)} field(s). Please verify them before saving."
            if detected_fields
            else "No metadata could be detected. Please enter the details manually."
        ),
    }
