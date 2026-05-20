"""Smart CSV importer — auto-detects LinkedIn URLs and maps fields."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from linauto.db.models import Lead

logger = structlog.get_logger()

# Regex to find LinkedIn profile URLs anywhere in a string
# Handles: https://linkedin.com/in/slug, www.linkedin.com/in/slug, linkedin.com/in/slug
_LINKEDIN_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?linkedin\.com/in/([\w-]+)/?(?:\?[^\s]*)?'
)

# Regex to extract Twitter/X username from a URL or bare handle
_TWITTER_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/?'
)

# Regex to extract Telegram username from a t.me URL
_TELEGRAM_URL_RE = re.compile(
    r'(?:https?://)?t\.me/([A-Za-z0-9_]+)/?'
)

# Column names that contain a full name to be split into first/last
_FULL_NAME_COLUMNS = {
    "name", "full_name", "fullname", "full name", "contact name",
    "person name", "contact", "nombre completo",
}

# Common column name variants -> standard field names
_COLUMN_MAP = {
    # first_name
    "first_name": "first_name",
    "firstname": "first_name",
    "first name": "first_name",
    "first": "first_name",
    "given_name": "first_name",
    "given name": "first_name",
    "vorname": "first_name",
    "prenom": "first_name",
    "nombre": "first_name",
    # last_name
    "last_name": "last_name",
    "lastname": "last_name",
    "last name": "last_name",
    "last": "last_name",
    "family_name": "last_name",
    "family name": "last_name",
    "nachname": "last_name",
    "surname": "last_name",
    "nom": "last_name",
    "apellido": "last_name",
    # company
    "company": "company",
    "company_name": "company",
    "company name": "company",
    "organization": "company",
    "organisation": "company",
    "firma": "company",
    "employer": "company",
    "unternehmen": "company",
    "entreprise": "company",
    "project": "company",
    "project_name": "company",
    "project name": "company",
    "startup": "company",
    # title
    "title": "title",
    "job_title": "title",
    "job title": "title",
    "jobtitle": "title",
    "position": "title",
    "role": "title",
    "designation": "title",
    "titel": "title",
    # email
    "email": "email",
    "e-mail": "email",
    "email_address": "email",
    "emailaddress": "email",
    "mail": "email",
    "courriel": "email",
    # phone
    "phone": "phone",
    "phone_number": "phone",
    "phonenumber": "phone",
    "mobile": "phone",
    "mobile_number": "phone",
    "tel": "phone",
    "telephone": "phone",
    "telefon": "phone",
    # twitter_url — accepts full URLs (twitter.com or x.com) or bare @handles
    "twitter_url": "twitter_url",
    "twitter": "twitter_url",
    "x_url": "twitter_url",
    "x": "twitter_url",
    "twitter_profile_url": "twitter_url",
    "twitter handle": "twitter_url",
    "twitter_handle": "twitter_url",
    "x handle": "twitter_url",
    "x_handle": "twitter_url",
    # telegram_username — accepts t.me URLs, @handles, or bare usernames
    "telegram_username": "telegram_username",
    "telegram": "telegram_username",
    "tg": "telegram_username",
    "tg_username": "telegram_username",
    "telegram handle": "telegram_username",
    "telegram_handle": "telegram_username",
    "tg handle": "telegram_username",
    "tg_handle": "telegram_username",
}

# Standard fields that map directly to Lead model columns
_STANDARD_FIELDS = {
    "first_name", "last_name", "company", "title",
    "email", "phone", "twitter_url", "telegram_username",
}


@dataclass
class ImportResult:
    total_rows: int = 0
    imported: int = 0
    duplicates_skipped: int = 0
    no_url_skipped: int = 0
    no_url_rows: list = field(default_factory=list)
    column_mapping: dict = field(default_factory=dict)
    extra_columns: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def normalize_twitter_url(val: str) -> Optional[str]:
    """
    Normalise a Twitter/X value to canonical https://x.com/<handle> form.
    Accepts: full twitter.com/x.com URLs, @handle, or bare handle.
    Returns None if the value looks empty or unparseable.
    """
    val = val.strip()
    if not val:
        return None
    # Strip leading @
    if val.startswith("@"):
        handle = val[1:]
        return f"https://x.com/{handle}" if handle else None
    # Full URL (twitter.com or x.com)
    m = _TWITTER_URL_RE.search(val)
    if m:
        return f"https://x.com/{m.group(1)}"
    # Bare alphanumeric handle (no slashes or dots)
    if re.match(r'^[A-Za-z0-9_]{1,50}$', val):
        return f"https://x.com/{val}"
    return None


def normalize_telegram_username(val: str) -> Optional[str]:
    """
    Normalise a Telegram value to a bare username (no @ prefix, no t.me/).
    Accepts: t.me/username, @username, or bare username.
    Returns None if the value is empty or unparseable.
    """
    val = val.strip()
    if not val:
        return None
    # t.me URL
    m = _TELEGRAM_URL_RE.search(val)
    if m:
        return m.group(1)
    # Strip leading @
    if val.startswith("@"):
        username = val[1:]
        return username if username else None
    # Bare username
    if re.match(r'^[A-Za-z0-9_]{3,32}$', val):
        return val
    return None


def normalize_linkedin_url(url: str) -> Optional[str]:
    """
    Normalize a LinkedIn URL to canonical form.
    Returns None if the input is not a valid LinkedIn profile URL.
    """
    match = _LINKEDIN_URL_RE.search(url)
    if not match:
        return None
    slug = match.group(1).lower()
    return f"https://www.linkedin.com/in/{slug}"


def _detect_url_column(headers: list, first_row: dict) -> Optional[str]:
    """
    Detect which column contains LinkedIn URLs.
    First checks column names, then scans values in the first row.
    """
    # Check column names for obvious LinkedIn URL columns
    url_column_names = {
        "linkedin_url", "linkedin url", "linkedin_profile_url",
        "linkedin profile url", "profile_url", "profile url",
        "linkedin", "li_url", "url", "person linkedin url",
    }
    for header in headers:
        if header.lower().strip() in url_column_names:
            return header

    # Scan first row values for LinkedIn URLs
    for header in headers:
        val = first_row.get(header, "")
        if val and _LINKEDIN_URL_RE.search(str(val)):
            return header

    return None


def _extract_linkedin_url(row: dict, url_column: Optional[str]) -> Optional[str]:
    """Extract and normalize a LinkedIn URL from a row."""
    # If we know the URL column, try it first
    if url_column and url_column in row:
        url = normalize_linkedin_url(str(row[url_column]))
        if url:
            return url

    # Scan all columns for a LinkedIn URL
    for val in row.values():
        if val:
            url = normalize_linkedin_url(str(val))
            if url:
                return url

    return None


def _split_full_name(full_name: str) -> tuple:
    """Split 'First Last' into (first_name, last_name). Extra parts go into last_name."""
    parts = full_name.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], None


def _build_column_mapping(headers: list) -> dict:
    """Map CSV column headers to standard Lead fields."""
    mapping = {}  # csv_column -> lead_field
    for header in headers:
        normalized = header.lower().strip()
        if normalized in _FULL_NAME_COLUMNS:
            mapping[header] = "full_name"
        elif normalized in _COLUMN_MAP:
            mapping[header] = _COLUMN_MAP[normalized]
    return mapping


def parse_csv(
    file_path: str,
    campaign_id: Optional[str] = None,
    lead_list_id: Optional[str] = None,
    existing_urls: Optional[set] = None,
) -> tuple:
    """
    Parse a CSV file and return (leads, import_result).

    Args:
        file_path: Path to the CSV file
        campaign_id: Campaign to assign leads to (optional if lead_list_id provided)
        lead_list_id: Lead list to assign leads to (optional if campaign_id provided)
        existing_urls: Set of normalized URLs already in the target (for dedup)

    Returns:
        Tuple of (list[Lead], ImportResult)
    """
    if not campaign_id and not lead_list_id:
        raise ValueError("Either campaign_id or lead_list_id must be provided")
    existing_urls = existing_urls or set()
    result = ImportResult()
    leads = []

    path = Path(file_path)
    if not path.exists():
        result.errors.append(f"File not found: {file_path}")
        return leads, result

    # Detect CSV dialect and encoding
    with open(path, "r", encoding="utf-8-sig") as f:
        # Read first few lines to detect dialect
        sample = f.read(8192)
        f.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel  # Default to comma-separated

        reader = csv.DictReader(f, dialect=dialect)
        headers = reader.fieldnames or []

        if not headers:
            result.errors.append("CSV has no headers")
            return leads, result

        # Build column mapping
        column_mapping = _build_column_mapping(headers)
        result.column_mapping = {
            h: f for h, f in column_mapping.items() if f in _STANDARD_FIELDS | {"full_name"}
        }

        # Identify extra columns (not mapped to standard fields and not the URL column)
        mapped_columns = set(column_mapping.keys())
        result.extra_columns = [
            h for h in headers if h not in mapped_columns
        ]

        # Detect URL column from first row
        rows = list(reader)
        result.total_rows = len(rows)

        if not rows:
            result.errors.append("CSV has no data rows")
            return leads, result

        url_column = _detect_url_column(headers, rows[0])
        if url_column and url_column in result.extra_columns:
            result.extra_columns.remove(url_column)

        # Process each row
        for row_idx, row in enumerate(rows, start=2):  # Start at 2 (header is row 1)
            linkedin_url = _extract_linkedin_url(row, url_column)

            if not linkedin_url:
                result.no_url_skipped += 1
                result.no_url_rows.append(row_idx)
                continue

            # Dedup check
            if linkedin_url in existing_urls:
                result.duplicates_skipped += 1
                continue

            existing_urls.add(linkedin_url)

            # Extract standard fields
            first_name = None
            last_name = None
            company = None
            title = None
            email = None
            phone = None
            twitter_url = None
            telegram_username = None
            extra_data = {}

            for header in headers:
                value = (row.get(header) or "").strip()
                if not value:
                    continue

                mapped_field = column_mapping.get(header)
                if mapped_field == "full_name":
                    first_name, last_name = _split_full_name(value)
                elif mapped_field == "first_name":
                    first_name = value
                elif mapped_field == "last_name":
                    last_name = value
                elif mapped_field == "company":
                    company = value
                elif mapped_field == "title":
                    title = value
                elif mapped_field == "email":
                    email = value
                elif mapped_field == "phone":
                    phone = value
                elif mapped_field == "twitter_url":
                    twitter_url = normalize_twitter_url(value)
                elif mapped_field == "telegram_username":
                    telegram_username = normalize_telegram_username(value)
                elif header != url_column:
                    extra_data[header] = value

            lead_kwargs = dict(
                linkedin_url=linkedin_url,
                first_name=first_name,
                last_name=last_name,
                company=company,
                title=title,
                email=email,
                phone=phone,
                twitter_url=twitter_url,
                telegram_username=telegram_username,
                extra_data=extra_data if extra_data else None,
            )
            if campaign_id:
                lead_kwargs["campaign_id"] = campaign_id
            if lead_list_id:
                lead_kwargs["lead_list_id"] = lead_list_id
            lead = Lead(**lead_kwargs)
            leads.append(lead)
            result.imported += 1

    return leads, result
