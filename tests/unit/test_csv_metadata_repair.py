"""The repair fills metadata only and leaves delivery history unchanged."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "repair_csv_metadata", Path(__file__).resolve().parents[2] / "scripts/repair_csv_metadata.py",
)
repair_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair_module)


@pytest.fixture
def repair_fixture(tmp_path):
    db = tmp_path / "test.db"
    connection = sqlite3.connect(str(db))
    connection.execute("CREATE TABLE lead_lists (id TEXT PRIMARY KEY, name TEXT)")
    connection.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, linkedin_url TEXT, lead_list_id TEXT, status TEXT, extra_data TEXT, " + ", ".join(field + " TEXT" for field in repair_module.FIELDS) + ")")
    connection.execute("CREATE TABLE lead_list_memberships (id TEXT PRIMARY KEY, lead_id TEXT, lead_list_id TEXT)")
    connection.execute("CREATE TABLE campaigns (id TEXT PRIMARY KEY, status TEXT)")
    connection.execute("CREATE TABLE campaign_lead_assignments (id TEXT PRIMARY KEY, lead_id TEXT, status TEXT, retry_count INTEGER)")
    connection.execute("INSERT INTO lead_lists VALUES ('list', 'Hiring HRs')")
    connection.execute("INSERT INTO campaigns VALUES ('campaign', 'PAUSED')")
    connection.execute("INSERT INTO leads (id,linkedin_url,lead_list_id,status,extra_data,company) VALUES ('lead','https://www.linkedin.com/in/jane/?isSelfProfile=false',NULL,'connection_requested','{}','Enriched Company')")
    connection.execute("INSERT INTO lead_list_memberships VALUES ('membership','lead','list')")
    connection.execute("INSERT INTO campaign_lead_assignments VALUES ('assignment','lead','connection_requested',2)")
    connection.commit()
    connection.close()
    csv_path = tmp_path / "source.csv"
    csv_path.write_text("nm,co,ti,em,li\nJane Smith,Source Company,Head of People,jane@example.com,http://linkedin.com/in/jane\n", encoding="utf-8")
    return db, csv_path


def test_dry_run_does_not_write(repair_fixture):
    db, csv_path = repair_fixture
    before = db.read_bytes()
    result = repair_module.repair(db, [("list", csv_path)])
    assert result["applied"] is False
    assert result["leads_to_update"] == 1
    assert result["lists"][0]["fields_filled"] == {
        "first_name": 1, "last_name": 1, "title": 1, "email": 1,
    }
    assert db.read_bytes() == before


def test_repair_is_idempotent_and_preserves_enrichment_and_history(repair_fixture, tmp_path):
    db, csv_path = repair_fixture
    result = repair_module.repair(db, [("list", csv_path)], True, tmp_path / "audit")
    assert result["delivery_state_unchanged"] is True
    assert result["leads_updated"] == 1
    connection = sqlite3.connect(str(db))
    assert connection.execute("SELECT first_name,last_name,company,status FROM leads").fetchone() == (
        "Jane", "Smith", "Enriched Company", "connection_requested",
    )
    assert connection.execute("SELECT status,retry_count FROM campaign_lead_assignments").fetchone() == ("connection_requested", 2)
    assert connection.execute("SELECT status FROM campaigns").fetchone() == ("PAUSED",)
    connection.close()
    audit = json.loads(Path(result["audit"]).read_text())
    assert audit["changes"]["lead"]["before"]["first_name"] is None
    backup = sqlite3.connect(result["backup"])
    assert backup.execute("SELECT first_name FROM leads").fetchone() == (None,)
    backup.close()
    assert Path(result["audit"]).stat().st_mode & 0o777 == 0o600
    assert Path(result["backup"]).stat().st_mode & 0o777 == 0o600
    assert repair_module.repair(db, [("list", csv_path)])["leads_to_update"] == 0


def test_no_url_matches_only_a_unique_email(repair_fixture):
    db, csv_path = repair_fixture
    with sqlite3.connect(str(db)) as connection:
        connection.execute("UPDATE leads SET linkedin_url=NULL,email='jane@example.com'")
    csv_path.write_text("contact_name,contact_title,email,linkedin_url\nJane Smith,HR,jane@example.com,\n", encoding="utf-8")
    result = repair_module.repair(db, [("list", csv_path)])
    assert result["lists"][0]["matched"] == 1
    assert result["leads_to_update"] == 1


def test_unmatched_urls_are_never_reassigned(repair_fixture):
    db, csv_path = repair_fixture
    with sqlite3.connect(str(db)) as connection:
        connection.execute("UPDATE leads SET linkedin_url='https://linkedin.com/in/other'")
    result = repair_module.repair(db, [("list", csv_path)])
    assert result["leads_to_update"] == 0
    assert result["lists"][0]["unmatched"] == ["lead"]
