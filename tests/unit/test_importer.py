"""Tests for smart CSV importer."""
import pytest
import tempfile
import os
from releasi.campaign.importer import parse_csv, normalize_linkedin_url


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


def test_parse_keeps_named_rows_without_url():
    csv_content = """First Name,LinkedIn URL
John,https://www.linkedin.com/in/johndoe
Jane,
Bob,https://www.linkedin.com/in/bob
"""
    path = _write_csv(csv_content)
    try:
        leads, result = parse_csv(path, "campaign-1")
        assert result.imported == 3
        assert result.no_url_imported == 1
        assert result.no_url_skipped == 0
        assert leads[1].first_name == "Jane"
        assert leads[1].linkedin_url is None
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
        assert leads[0].location == "Berlin"
        assert "industry" in result.extra_columns
        assert "location" not in result.extra_columns
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


@pytest.mark.parametrize("headers,values", [
    ("pid,nm,ti,co,em,li", "source-id,Jane Smith,Head of People,Acme,jane@example.com,https://linkedin.com/in/jane/?isSelfProfile=false"),
    ("company,contact_name,contact_title,email,linkedin_url", "Acme,Jane Smith,Head of People,jane@example.com,https://linkedin.com/in/jane"),
    ("companyname,person_name,person_title,person_email,linkedin_url", "Acme,Jane Smith,Head of People,jane@example.com,https://linkedin.com/in/jane"),
])
def test_parse_hiring_export_headers(headers, values):
    path = _write_csv(headers + "\n" + values + "\n")
    try:
        leads, result = parse_csv(path, lead_list_id="list-1")
        assert result.imported == 1
        lead = leads[0]
        assert (lead.first_name, lead.last_name) == ("Jane", "Smith")
        assert (lead.company, lead.title, lead.email) == ("Acme", "Head of People", "jane@example.com")
        assert lead.linkedin_url == "https://www.linkedin.com/in/jane"
        if "pid" in headers:
            assert lead.extra_data == {"pid": "source-id"}
            assert lead.apollo_person_id is None
    finally:
        os.unlink(path)


@pytest.mark.parametrize("headers,values", [
    ("first_name,last_name,contact_name", "Mary Jane,Watson,Mary Watson"),
    ("contact_name,first_name,last_name", "Mary Watson,Mary Jane,Watson"),
])
def test_explicit_names_win_over_full_name_in_either_order(headers, values):
    path = _write_csv(headers + ",linkedin_url\n" + values + ",https://linkedin.com/in/mary\n")
    try:
        leads, _ = parse_csv(path, lead_list_id="list-1")
        assert (leads[0].first_name, leads[0].last_name) == ("Mary Jane", "Watson")
    finally:
        os.unlink(path)


def test_abbreviated_identity_without_url_is_retained():
    path = _write_csv("nm,em,li\nJane Smith,jane@example.com,\n,,\n")
    try:
        leads, result = parse_csv(path, lead_list_id="list-1")
        assert result.no_url_imported == 1
        assert result.no_url_skipped == 1
        assert result.no_url_rows == [3]
        assert leads[0].first_name == "Jane"
    finally:
        os.unlink(path)
