"""PDF Generation Service for Sales Letters."""

import logging
from django.template.loader import render_to_string
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

# Template mapping for letter types
LETTER_TEMPLATES = {
    'eoi': 'sales/letters/eoi.html',
    'regret_expertise': 'sales/letters/regret_expertise.html',
    'regret_manpower': 'sales/letters/regret_manpower.html',
}

# Color themes for each letter type
LETTER_THEMES = {
    'eoi': {'primary': '#1a3c6e', 'secondary': '#2c5aa0', 'bg': '#f5f7fa'},
    'regret_expertise': {'primary': '#8b1a1a', 'secondary': '#a52a2a', 'bg': '#fdf2f2'},
    'regret_manpower': {'primary': '#8b6914', 'secondary': '#b8860b', 'bg': '#fef9e7'},
}


def get_letter_template(letter_type):
    """Get the template path for a letter type."""
    return LETTER_TEMPLATES.get(letter_type, LETTER_TEMPLATES['eoi'])


def get_letter_theme(letter_type):
    """Get the color theme for a letter type."""
    return LETTER_THEMES.get(letter_type, LETTER_THEMES['eoi'])


def build_pdf_context(letter, custom_data=None):
    """Build the rendering context for PDF templates."""
    from .letter_context import build_letter_context

    merged = dict(letter.custom_data or {})
    merged.update(custom_data or {})
    context = build_letter_context(letter.opportunity, letter=letter, custom_data=merged)
    context['theme'] = get_letter_theme(letter.letter_type)
    return context


def parse_letter_body(body_text):
    """Parse letter body into structured paragraphs for editing/rendering."""
    from .letter_context import split_body_paragraphs

    paragraphs = split_body_paragraphs(body_text)
    field_names = ['body_p1', 'body_p2', 'body_p3', 'body_p4', 'closing']
    result = []
    for i, para in enumerate(paragraphs):
        field = field_names[i] if i < len(field_names) else f'body_p{i + 1}'
        result.append({
            'field': field,
            'content': para,
            'is_first': i == 0,
        })
    return result


def generate_letter_pdf(letter, custom_data=None):
    """
    Generate PDF from letter using HTML template.
    Returns PDF bytes.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        logger.error("weasyprint not installed. Cannot generate PDF.")
        raise RuntimeError("PDF generation requires weasyprint. Please install it: pip install weasyprint>=60.0")

    template_path = get_letter_template(letter.letter_type)
    context = build_pdf_context(letter, custom_data)

    # Render HTML
    html_string = render_to_string(template_path, context)

    # Generate PDF (WeasyPrint 60+ manages fonts internally — FontConfiguration removed)
    # Use logo file directory as base_url so relative file:// URIs resolve correctly
    from .letter_defaults import get_logo_path
    logo_path = get_logo_path()
    base_url = logo_path.parent.as_uri() if logo_path else settings.BASE_DIR
    html = HTML(string=html_string, base_url=base_url)

    try:
        pdf_bytes = html.write_pdf()
    except Exception as e:
        logger.error(f"PDF generation failed for letter {letter.id}: {e}")
        raise

    return pdf_bytes


def save_letter_pdf(letter, custom_data=None):
    """
    Generate and save PDF to letter's pdf_file field.
    Returns the saved file path.
    """
    from .letter_extraction import require_recipient
    require_recipient({**(letter.custom_data or {}), **(custom_data or {})}.get('letterhead') or {})
    pdf_bytes = generate_letter_pdf(letter, custom_data)

    # Create filename
    filename = f"{letter.opportunity.deal_code}-{letter.letter_type}-{letter.generated_at.strftime('%Y%m%d')}.pdf"

    # Save to model
    letter.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    letter.pdf_generated_at = timezone.now()
    letter.save(update_fields=['pdf_file', 'pdf_generated_at', 'updated_at'])

    logger.info(f"PDF generated for letter {letter.id}: {filename}")
    return letter.pdf_file.name


def regenerate_letter_pdf(letter, custom_data=None):
    """Regenerate PDF for an existing letter (after edits)."""
    old_name = letter.pdf_file.name if letter.pdf_file else None
    storage = letter.pdf_file.storage
    new_name = save_letter_pdf(letter, custom_data)
    if old_name and old_name != new_name:
        try:
            storage.delete(old_name)
        except Exception as exc:
            logger.warning("Unable to remove previous PDF for letter %s: %s", letter.id, exc)
    return new_name


def get_letter_pdf_bytes(letter):
    """Get PDF bytes for download (generates if not exists)."""
    # Drafts are editable: preview the current layout without mutating the
    # saved letter, revision, or historical correspondence attachment.
    if letter.status == 'draft':
        return generate_letter_pdf(letter)
    if not letter.pdf_file:
        save_letter_pdf(letter)

    if letter.pdf_file:
        letter.pdf_file.open('rb')
        pdf_bytes = letter.pdf_file.read()
        letter.pdf_file.close()
        return pdf_bytes

    # Fallback: generate on demand
    return generate_letter_pdf(letter)
