"""Health check + Phase 1 dual-write parity diagnostic."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import HealthResponse
from releasi.db.repository import Repository

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


# Phase 1 dual-write reconciliation. Compares state of the legacy `leads`
# table against the new `campaign_lead_assignments` + `lead_list_memberships`
# tables. Any drift (counts that don't match, status fields that disagree)
# indicates the dual-write is missing a site or has a bug.
#
# Lives under /health/lead-schema-parity so it's easy to poll from a
# monitoring script during the Phase 1 watch period.
@router.get(
    "/health/lead-schema-parity",
    dependencies=[Depends(require_api_key)],
)
async def lead_schema_parity(repo: Repository = Depends(get_repo)):
    from sqlalchemy import text

    s = repo.session

    # Memberships: every Lead.lead_list_id should have a matching
    # LeadListMembership(lead_id, lead_list_id). Drift = legacy rows with
    # no matching membership.
    membership_drift = await s.execute(text("""
        SELECT COUNT(*) FROM leads l
        WHERE l.lead_list_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM lead_list_memberships m
              WHERE m.lead_id = l.id AND m.lead_list_id = l.lead_list_id
          )
    """))

    # Assignments: every Lead.campaign_id should have a matching
    # CampaignLeadAssignment(lead_id, campaign_id). Drift = legacy rows
    # with no matching assignment.
    assignment_missing = await s.execute(text("""
        SELECT COUNT(*) FROM leads l
        WHERE l.campaign_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM campaign_lead_assignments a
              WHERE a.lead_id = l.id AND a.campaign_id = l.campaign_id
          )
    """))

    # Status drift: where both tables have a row, the status field must
    # match (case-insensitive, since legacy enum is uppercase and new
    # column stores lowercase). Drift = mismatched rows.
    status_drift = await s.execute(text("""
        SELECT COUNT(*) FROM leads l
        JOIN campaign_lead_assignments a
          ON a.lead_id = l.id AND a.campaign_id = l.campaign_id
        WHERE LOWER(l.status) != LOWER(a.status)
    """))

    # Timestamp drift on the most consequential field. We check
    # connection_accepted_at because that's what the catchup writes and
    # what we'd most want to catch if the dual-write silently regresses.
    accepted_at_drift = await s.execute(text("""
        SELECT COUNT(*) FROM leads l
        JOIN campaign_lead_assignments a
          ON a.lead_id = l.id AND a.campaign_id = l.campaign_id
        WHERE COALESCE(l.connection_accepted_at, '') != COALESCE(a.connection_accepted_at, '')
    """))

    # Total counts for sanity. After backfill these should be:
    #   memberships == leads_with_list
    #   assignments == leads_with_campaign
    counts = await s.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM leads) AS total_leads,
          (SELECT COUNT(*) FROM leads WHERE lead_list_id IS NOT NULL) AS leads_with_list,
          (SELECT COUNT(*) FROM leads WHERE campaign_id IS NOT NULL) AS leads_with_campaign,
          (SELECT COUNT(*) FROM lead_list_memberships) AS memberships,
          (SELECT COUNT(*) FROM campaign_lead_assignments) AS assignments
    """))
    row = counts.fetchone()

    return {
        "counts": {
            "total_leads": row[0],
            "leads_with_list": row[1],
            "leads_with_campaign": row[2],
            "memberships": row[3],
            "assignments": row[4],
        },
        "drift": {
            "leads_missing_membership": membership_drift.scalar_one(),
            "leads_missing_assignment": assignment_missing.scalar_one(),
            "status_mismatches": status_drift.scalar_one(),
            "accepted_at_mismatches": accepted_at_drift.scalar_one(),
        },
        "expected": {
            "leads_missing_membership": 0,
            "leads_missing_assignment": 0,
            "status_mismatches": 0,
            "accepted_at_mismatches": 0,
        },
    }
