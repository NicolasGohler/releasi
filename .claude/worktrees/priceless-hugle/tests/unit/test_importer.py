"""Tests for smart CSV importer."""
import pytest
import tempfile
import os
from linauto.campaign.importer import parse_csv, normalize_linkedin_url


def test_normalize_standard_url():
    assert normalize_linkedin_url("https://www.linkedin.com/in/johndoe") == "https://www.linkedin.com/in/johndoe"


def test_normalize_strips_params():
    assert normalize_linkedin_url("https://linkedin.com/in/johndoe?trk=abc") == "https://www.linkedin.com/in/johndoe"


def test_normalize_strips_trailing_slash():
    assert normalize_linkedin_url("https://www.linkedin.com/in/JohnDoe/") == "https://www.linkedin.com/in/johndoe"


def test_normalize_lowercases_slug():
    assert normalize_linkedin_url("https://www.linkedin.com/in/JohnDoe") == "https://www.linkedin.com/in/johndoe"


def test_normalize_no_protocol():
    assert normalize_linkedin_url("linkedin.com/in/johndoe") == "https://www.linkedin.com/in/johndoe"


def test_normalize_www_no_protocol():
    assert normalize_linkedin_url("www.linkedin.com/in/johndoe") == "https://www.linkedin.com/in/johndoe"


def test_normalize_invalid_url():
    assert normalize_linkedin_url("not-a-linkedin-url") is None
    assert normalize_linkedin_url("https://google.com") is None
    assert normalize_linkedin_url("") is None


def _write_csv(content):
    """Write CSV content to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_parse_standard_csv():
    csv_content = """First Name,Last Name,LinkedIn Profile URL,Company
John,Doe,https://www.linkedin.com/in/johndoe,Acme
Jane,Smith,https://www.linkedin.com/in/janesmith,TechCo
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 2
        assert result.no_url_skipped == 0
        assert result.duplicates_skipped == 0
        assert len(leads) == 2
        assert leads[0].first_name == "John"
        assert leads[0].company == "Acme"
        assert leads[1].linkedin_url == "https://www.linkedin.com/in/janesmith"
    finally:
        os.unlink(path)


def test_parse_detects_url_in_any_column():
    csv_content = """name,email,profile
John Doe,john@example.com,https://www.linkedin.com/in/johndoe
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 1
        assert leads[0].linkedin_url == "https://www.linkedin.com/in/johndoe"
    finally:
        os.unlink(path)


def test_parse_skips_rows_without_url():
    csv_content = """First Name,LinkedIn URL
John,https://www.linkedin.com/in/johndoe
Jane,
Bob,https://www.linkedin.com/in/bob
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 2
        assert result.no_url_skipped == 1
        assert 3 in result.no_url_rows  # Row 3 (1-indexed from header)
    finally:
        os.unlink(path)


def test_parse_deduplicates():
    csv_content = """name,linkedin_url
John,https://www.linkedin.com/in/johndoe
John Dup,https://www.linkedin.com/in/johndoe
Jane,https://www.linkedin.com/in/janesmith
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 2
        assert result.duplicates_skipped == 1
    finally:
        os.unlink(path)


def test_parse_dedup_against_existing():
    csv_content = """name,linkedin_url
John,https://www.linkedin.com/in/johndoe
Jane,https://www.linkedin.com/in/janesmith
"""
    path = _write_csv(csv_content)
    existing = {"https://www.linkedin.com/in/johndoe"}
    try:
        leads, result = parse_csv(path, "campaign-1", existing_urls=existing)
        assert result.imported == 1
        assert result.duplicates_skipped == 1
        assert leads[0].linkedin_url == "https://www.linkedin.com/in/janesmith"
    finally:
        os.unlink(path)


def test_parse_maps_column_variants():
    csv_content = """Vorname,Nachname,Firma,Position,url
Nicolas,Goehler,Threedom,CEO,https://linkedin.com/in/nicolasgoehler
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 1
        assert leads[0].first_name == "Nicolas"
        assert leads[0].last_name == "Goehler"
        assert leads[0].company == "Threedom"
        assert leads[0].title == "CEO"
    finally:
        os.unlink(path)


def test_parse_extra_data():
    csv_content = """firstname,linkedin_url,industry,location
John,https://linkedin.com/in/john,Tech,Berlin
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 1
        assert leads[0].extra_data["industry"] == "Tech"
        assert leads[0].extra_data["location"] == "Berlin"
        assert "industry" in result.extra_columns or "location" in result.extra_columns
    finally:
        os.unlink(path)


def test_parse_url_without_protocol():
    csv_content = """name,profile
Alice,linkedin.com/in/alice
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 1
        assert leads[0].linkedin_url == "https://www.linkedin.com/in/alice"
    finally:
        os.unlink(path)
