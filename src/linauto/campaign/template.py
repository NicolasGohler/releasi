"""Message template engine with {{variable}} substitution."""
from __future__ import annotations

import re
from typing import Optional

import structlog

from linauto.db.models import Lead

logger = structlog.get_logger()

_PATTERN = re.compile(r'\{\{(\w+)\}\}')

# Map template variable names to Lead model attributes
_FIELD_MAP = {
    "firstname": "first_name",
    "first_name": "first_name",
    "lastname": "last_name",
    "last_name": "last_name",
    "company": "company",
    "title": "title",
}


def render_template(template: str, lead: Lead) -> str:
    """
    Replace {{variable}} placeholders with lead data.

    Supports:
      - {{firstname}}, {{first_name}} -> lead.first_name
      - {{lastname}}, {{last_name}} -> lead.last_name
      - {{company}} -> lead.company
      - {{title}} -> lead.title
      - Any other variable -> looked up in lead.extra_data JSON

    Missing values are replaced with empty string.
    """
    def _replace(match):
        var_name = match.group(1).lower()

        # Check standard field map
        attr_name = _FIELD_MAP.get(var_name)
        if attr_name:
            value = getattr(lead, attr_name, None)
            if value:
                return value
            logger.debug("template.missing_field", variable=var_name, lead_url=lead.linkedin_url)
            return ""

        # Check extra_data
        if lead.extra_data and var_name in lead.extra_data:
            return str(lead.extra_data[var_name])

        # Also try original case in extra_data
        if lead.extra_data:
            for key, val in lead.extra_data.items():
                if key.lower() == var_name:
                    return str(val)

        logger.debug("template.unknown_variable", variable=var_name, lead_url=lead.linkedin_url)
        return ""

    return _PATTERN.sub(_replace, template)


def extract_variables(template: str) -> list:
    """Extract all {{variable}} names from a template."""
    return _PATTERN.findall(template)


def validate_template(template: str, lead: Optional[Lead] = None) -> list:
    """
    Validate a template. Returns list of warnings.
    If a sample lead is provided, checks that all variables resolve.
    """
    warnings = []
    variables = extract_variables(template)

    if not variables:
        warnings.append("Template contains no {{variables}} — message will be identical for all leads")

    if lead:
        for var in variables:
            var_lower = var.lower()
            attr_name = _FIELD_MAP.get(var_lower)
            if attr_name:
                if not getattr(lead, attr_name, None):
                    warnings.append(f"{{{{{var}}}}} will be empty for this lead")
            elif not lead.extra_data or var_lower not in {k.lower() for k in lead.extra_data}:
                warnings.append(f"{{{{{var}}}}} not found in lead data")

    # LinkedIn connection note limit: 300 characters
    if len(template) > 250:
        warnings.append(f"Template is {len(template)} chars — LinkedIn limit is 300 (after variable substitution)")

    return warnings
