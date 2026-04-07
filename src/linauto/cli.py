"""CLI interface using Typer."""
from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path

# Suppress SQLAlchemy/aiosqlite GC warnings for CLI (harmless in short-lived processes)
import logging
logging.getLogger("sqlalchemy.pool").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.pool.impl").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*garbage collector.*")
warnings.filterwarnings("ignore", message=".*non-checked-in.*")
try:
    from sqlalchemy.exc import SAWarning
    warnings.filterwarnings("ignore", category=SAWarning)
except ImportError:
    pass

# Monkey-patch to suppress the direct stderr write from AdaptedConnection.__del__
import sqlalchemy.pool.base as _pool_base
if hasattr(_pool_base, '_finalize_fairy'):
    pass  # Cannot easily patch this
# Suppress at stderr level for cleanup messages
import atexit
import os

def _suppress_gc_stderr():
    """Redirect stderr at exit to suppress GC cleanup messages."""
    try:
        sys.stderr = open(os.devnull, 'w')
    except Exception:
        pass

atexit.register(_suppress_gc_stderr)

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(name="linauto", help="LinkedIn Automation Tool", no_args_is_help=True)

# Sub-command groups
account_app = typer.Typer(help="Manage LinkedIn accounts")
campaign_app = typer.Typer(help="Manage campaigns")
app.add_typer(account_app, name="account")
app.add_typer(campaign_app, name="campaign")


def _run(coro):
    """Run an async function from sync CLI context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)


async def _get_repo():
    """Get a Repository instance with an active session."""
    from linauto.db.engine import init_db, get_session_factory
    from linauto.db.repository import Repository
    await init_db()
    session = get_session_factory()()
    return Repository(session), session


async def _cleanup(session):
    """Close session and dispose engine."""
    await session.close()
    from linauto.db.engine import close_db
    await close_db()


# ── Account commands ───────────────────────────────────────────────────────

@account_app.command("add")
def account_add(
    name: str = typer.Option(..., "--name", "-n", help="Account label (e.g., 'nicolas')"),
    li_at: str = typer.Option(
        ..., "--li-at", help="LinkedIn li_at cookie value", prompt=True, hide_input=True
    ),
    timezone: str = typer.Option(
        "Europe/Berlin", "--timezone", "-tz", help="Account timezone (e.g., 'America/New_York')"
    ),
    proxy: str = typer.Option(
        None, "--proxy", help="Explicit proxy URL (e.g., 'http://user:pass@host:port')"
    ),
    proxy_country: str = typer.Option(
        None, "--proxy-country", "-pc",
        help="Auto-generate residential proxy for country (e.g., 'ca-montreal', 'de-berlin', 'es')"
    ),
):
    """Add a new LinkedIn account with its session cookie."""
    async def _add():
        repo, session = await _get_repo()
        existing = await repo.get_account_by_name(name)
        if existing:
            console.print(f"[red]Account '{name}' already exists.[/red]")
            raise typer.Exit(1)

        kwargs = {"timezone": timezone}
        if proxy:
            kwargs["proxy_url"] = proxy
        if proxy_country:
            kwargs["proxy_country"] = proxy_country

        account = await repo.create_account(name=name, li_at_cookie=li_at, **kwargs)
        console.print(f"[green]Account '{name}' added (id: {account.id[:8]}...)[/green]")
        console.print(f"  Timezone: {timezone}")
        if proxy:
            console.print(f"  Proxy: configured (explicit)")
        if proxy_country:
            console.print(f"  Proxy country: {proxy_country} (auto-generated)")
        await _cleanup(session)

    _run(_add())


@account_app.command("list")
def account_list():
    """List all accounts."""
    async def _list():
        repo, session = await _get_repo()
        accounts = await repo.list_accounts()
        if not accounts:
            console.print("[dim]No accounts found. Use 'linauto account add' to add one.[/dim]")
            return

        table = Table(title="LinkedIn Accounts")
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("Daily Limit", justify="right")
        table.add_column("Weekly Limit", justify="right")
        table.add_column("Paused Until")

        for a in accounts:
            status_style = "green" if a.status.value == "active" else "red"
            paused = str(a.paused_until) if a.paused_until else "-"
            table.add_row(
                a.name,
                f"[{status_style}]{a.status.value}[/{status_style}]",
                str(a.daily_limit),
                str(a.weekly_limit),
                paused,
            )
        console.print(table)
        await _cleanup(session)

    _run(_list())


@account_app.command("validate")
def account_validate(
    name: str = typer.Option(..., "--name", "-n", help="Account name to validate"),
):
    """Check if an account's LinkedIn session is still valid."""
    async def _validate():
        repo, session = await _get_repo()
        account = await repo.get_account_by_name(name)
        if not account:
            console.print(f"[red]Account '{name}' not found.[/red]")
            raise typer.Exit(1)

        from linauto.linkedin.browser import LinkedInBrowser
        browser = LinkedInBrowser()
        try:
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if valid:
                console.print(f"[green]Session for '{name}' is valid.[/green]")
            else:
                console.print(f"[red]Session for '{name}' has expired. Update the li_at cookie.[/red]")
                await repo.update_account(account, status="cookie_expired")
        finally:
            await browser.close()
        await _cleanup(session)

    _run(_validate())


@account_app.command("update-cookie")
def account_update_cookie(
    name: str = typer.Option(..., "--name", "-n", help="Account name"),
    li_at: str = typer.Option(
        ..., "--li-at", help="New li_at cookie value", prompt=True, hide_input=True
    ),
):
    """Update the li_at cookie for an account."""
    async def _update():
        repo, session = await _get_repo()
        account = await repo.get_account_by_name(name)
        if not account:
            console.print(f"[red]Account '{name}' not found.[/red]")
            raise typer.Exit(1)
        await repo.update_account(account, li_at_cookie=li_at, status="active")
        console.print(f"[green]Cookie updated for '{name}'.[/green]")
        await _cleanup(session)

    _run(_update())


# ── Campaign commands ──────────────────────────────────────────────────────

@campaign_app.command("create")
def campaign_create(
    name: str = typer.Option(..., "--name", "-n", help="Campaign name"),
    account: str = typer.Option(..., "--account", "-a", help="Account name to use"),
    connect_msg: str = typer.Option(
        None, "--connect-msg", "-m",
        help='Connection message template, e.g., "Hi {{firstname}}, happy to connect!"',
    ),
    followup_msg: str = typer.Option(
        None, "--followup-msg", "-f", help="Follow-up message template (sent after acceptance)",
    ),
    followup_delay: int = typer.Option(
        24, "--followup-delay", help="Hours to wait after acceptance before sending follow-up",
    ),
    filter_no_photo: bool = typer.Option(
        False, "--filter-no-photo/--no-filter-no-photo",
        help="Skip profiles without a profile photo",
    ),
    filter_min_connections: int = typer.Option(
        None, "--filter-min-connections",
        help="Skip profiles with fewer than N connections (e.g., 500)",
    ),
):
    """Create a new campaign."""
    async def _create():
        repo, session = await _get_repo()
        acct = await repo.get_account_by_name(account)
        if not acct:
            console.print(f"[red]Account '{account}' not found.[/red]")
            raise typer.Exit(1)

        existing = await repo.get_campaign_by_name(name)
        if existing:
            console.print(f"[red]Campaign '{name}' already exists.[/red]")
            raise typer.Exit(1)

        campaign = await repo.create_campaign(
            account_id=acct.id,
            name=name,
            connection_message_template=connect_msg,
            followup_message_template=followup_msg,
            followup_delay_hours=followup_delay,
            filter_no_photo=filter_no_photo,
            filter_min_connections=filter_min_connections,
        )
        console.print(f"[green]Campaign '{name}' created (id: {campaign.id[:8]}...)[/green]")

        if connect_msg:
            from linauto.campaign.template import extract_variables
            variables = extract_variables(connect_msg)
            if variables:
                console.print(f"  Template variables: {', '.join('{{' + v + '}}' for v in variables)}")
        if filter_no_photo or filter_min_connections:
            filters = []
            if filter_no_photo:
                filters.append("skip no-photo profiles")
            if filter_min_connections:
                filters.append(f"skip <{filter_min_connections} connections")
            console.print(f"  Filters: {', '.join(filters)}")
        await _cleanup(session)

    _run(_create())


@campaign_app.command("import")
def campaign_import(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
    csv_path: str = typer.Option(..., "--csv", help="Path to CSV file"),
):
    """Import leads from a CSV into a campaign. Works on active campaigns too."""
    async def _import():
        repo, session = await _get_repo()

        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        if not Path(csv_path).exists():
            console.print(f"[red]File not found: {csv_path}[/red]")
            raise typer.Exit(1)

        # Get existing URLs for dedup
        from linauto.db.models import LeadStatus
        from sqlalchemy import select
        from linauto.db.models import Lead
        result = await session.execute(
            select(Lead.linkedin_url).where(Lead.campaign_id == campaign.id)
        )
        existing_urls = {row[0] for row in result.all()}

        # Parse CSV
        from linauto.campaign.importer import parse_csv
        leads, report = parse_csv(csv_path, campaign_id=campaign.id, existing_urls=existing_urls)

        if report.errors:
            for err in report.errors:
                console.print(f"[red]Error: {err}[/red]")
            raise typer.Exit(1)

        # Save leads to DB
        if leads:
            await repo.bulk_create_leads(leads)
            await repo.update_campaign(
                campaign, total_leads=campaign.total_leads + report.imported
            )

        # Print report
        console.print()
        console.print(f"[green]Imported: {report.imported} leads[/green]")
        if report.duplicates_skipped:
            console.print(f"[yellow]Skipped (duplicate): {report.duplicates_skipped}[/yellow]")
        if report.no_url_skipped:
            rows_str = ", ".join(str(r) for r in report.no_url_rows[:10])
            if len(report.no_url_rows) > 10:
                rows_str += "..."
            console.print(
                f"[yellow]Skipped (no LinkedIn URL found): {report.no_url_skipped}  "
                f"[rows: {rows_str}][/yellow]"
            )
        if report.column_mapping:
            mapped = ", ".join(f"{v}" for v in set(report.column_mapping.values()))
            console.print(f"[dim]Columns mapped: {mapped}[/dim]")
        if report.extra_columns:
            extras = ", ".join(f"{{{{{c}}}}}" for c in report.extra_columns[:10])
            console.print(f"[dim]Extra columns available as template vars: {extras}[/dim]")

        await _cleanup(session)

    _run(_import())


@campaign_app.command("status")
def campaign_status(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
):
    """Show campaign progress and statistics."""
    async def _status():
        repo, session = await _get_repo()
        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        acct = await repo.get_account(campaign.account_id)
        counts = await repo.get_campaign_status_counts(campaign.id)
        total = sum(counts.values())

        # Calculate acceptance rate
        sent_statuses = {"connection_requested", "connected", "followup_scheduled", "followup_sent", "completed"}
        total_sent = sum(counts.get(s, 0) for s in sent_statuses)
        accepted_statuses = {"connected", "followup_scheduled", "followup_sent", "completed"}
        total_accepted = sum(counts.get(s, 0) for s in accepted_statuses)
        acceptance_rate = (total_accepted / total_sent * 100) if total_sent > 0 else 0

        # Get weekly stats
        from datetime import date, timedelta
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        weekly_sent = await repo.get_weekly_request_count(acct.id, monday)

        console.print()
        console.print(f"[bold]Campaign:[/bold] {campaign.name}")
        console.print(f"[bold]Account:[/bold]  {acct.name}")
        console.print(f"[bold]Status:[/bold]   {campaign.status.value}")
        console.print(f"[bold]Created:[/bold]  {campaign.created_at.strftime('%Y-%m-%d')}")
        console.print()

        def _pct(n):
            return f"({n / total * 100:.1f}%)" if total > 0 else ""

        console.print(f"[bold]Leads:[/bold] {total} total")

        status_lines = [
            ("completed", "green", "Completed", ""),
            ("followup_sent", "green", "Follow-up sent", ""),
            ("followup_scheduled", "cyan", "Follow-up queued", ""),
            ("connected", "cyan", "Connected", "[awaiting follow-up]"),
            ("connection_requested", "yellow", "Requested", "[awaiting acceptance]"),
            ("scheduled", "dim", "Scheduled", "[queued]"),
            ("pending", "dim", "Pending", "[not yet scheduled]"),
            ("limit_paused", "yellow", "Limit paused", ""),
            ("skipped", "red", "Skipped", ""),
            ("error", "red", "Error", ""),
        ]

        for status_val, color, label, suffix in status_lines:
            count = counts.get(status_val, 0)
            if count > 0:
                console.print(
                    f"  [{color}]{label}:{' ' * (18 - len(label))}{count:>5} {_pct(count)}  {suffix}[/{color}]"
                )

        console.print()
        console.print(f"[bold]Acceptance rate:[/bold] {acceptance_rate:.1f}% ({total_accepted} accepted / {total_sent} sent)")
        console.print(f"[bold]This week:[/bold] {weekly_sent}/{acct.weekly_limit} requests sent")
        paused_info = f"paused until {acct.paused_until}" if acct.paused_until else "active (no cooldown)"
        console.print(f"[bold]Account status:[/bold] {paused_info}")

        await _cleanup(session)

    _run(_status())


@campaign_app.command("set-template")
def campaign_set_template(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
    connect_msg: str = typer.Option(
        None, "--connect-msg", "-m", help="Connection request message template"
    ),
    followup_msg: str = typer.Option(
        None, "--followup-msg", "-f", help="Follow-up message template"
    ),
    followup_delay: int = typer.Option(
        None, "--followup-delay", help="Hours to wait after acceptance"
    ),
    filter_no_photo: bool = typer.Option(
        None, "--filter-no-photo/--no-filter-no-photo",
        help="Skip profiles without a profile photo",
    ),
    filter_min_connections: int = typer.Option(
        None, "--filter-min-connections",
        help="Skip profiles with fewer than N connections (e.g., 500). Set to 0 to disable.",
    ),
):
    """Update message templates and filters for a campaign."""
    async def _set():
        repo, session = await _get_repo()
        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        updates = {}
        if connect_msg is not None:
            updates["connection_message_template"] = connect_msg
        if followup_msg is not None:
            updates["followup_message_template"] = followup_msg
        if followup_delay is not None:
            updates["followup_delay_hours"] = followup_delay
        if filter_no_photo is not None:
            updates["filter_no_photo"] = filter_no_photo
        if filter_min_connections is not None:
            updates["filter_min_connections"] = filter_min_connections if filter_min_connections > 0 else None

        if not updates:
            console.print("[yellow]No updates specified.[/yellow]")
            raise typer.Exit(1)

        await repo.update_campaign(campaign, **updates)
        console.print(f"[green]Campaign '{campaign_name}' updated.[/green]")

        if connect_msg:
            from linauto.campaign.template import validate_template
            warnings = validate_template(connect_msg)
            for w in warnings:
                console.print(f"  [yellow]Warning: {w}[/yellow]")

        await _cleanup(session)

    _run(_set())


@campaign_app.command("list")
def campaign_list():
    """List all campaigns."""
    async def _list():
        repo, session = await _get_repo()
        campaigns = await repo.list_campaigns()
        if not campaigns:
            console.print("[dim]No campaigns found.[/dim]")
            return

        table = Table(title="Campaigns")
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("Leads", justify="right")
        table.add_column("Created")

        for c in campaigns:
            status_style = {"active": "green", "draft": "dim", "paused": "yellow", "completed": "blue"}
            style = status_style.get(c.status.value, "white")
            table.add_row(
                c.name,
                f"[{style}]{c.status.value}[/{style}]",
                str(c.total_leads),
                c.created_at.strftime("%Y-%m-%d"),
            )
        console.print(table)
        await _cleanup(session)

    _run(_list())


@campaign_app.command("reset-leads")
def campaign_reset_leads(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
    all_leads: bool = typer.Option(
        False, "--all", help="Reset ALL non-completed leads (including connection_requested)"
    ),
):
    """Reset leads back to pending so they can be reprocessed. By default resets error, skipped, and limit_paused leads."""
    async def _reset():
        repo, session = await _get_repo()
        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        from linauto.db.models import LeadStatus
        if all_leads:
            statuses = [
                LeadStatus.ERROR,
                LeadStatus.SKIPPED,
                LeadStatus.LIMIT_PAUSED,
                LeadStatus.SCHEDULED,
                LeadStatus.CONNECTION_REQUESTED,
            ]
        else:
            statuses = None  # defaults to error, skipped, limit_paused

        # Show what will be reset
        counts = await repo.get_campaign_status_counts(campaign.id)
        target_statuses = [s.value for s in statuses] if statuses else ["error", "skipped", "limit_paused"]
        total_to_reset = sum(counts.get(s, 0) for s in target_statuses)

        if total_to_reset == 0:
            console.print("[yellow]No leads to reset.[/yellow]")
            return

        reset_count = await repo.reset_campaign_leads(campaign.id, statuses)
        console.print(f"[green]Reset {reset_count} leads back to pending in '{campaign_name}'.[/green]")
        for s in target_statuses:
            c = counts.get(s, 0)
            if c > 0:
                console.print(f"  {s}: {c}")

        await _cleanup(session)

    _run(_reset())


@campaign_app.command("activate")
def campaign_activate(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
):
    """Activate a campaign (start scheduling leads for processing)."""
    async def _activate():
        repo, session = await _get_repo()
        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        await repo.update_campaign(campaign, status="active")
        console.print(f"[green]Campaign '{campaign_name}' activated.[/green]")
        await _cleanup(session)

    _run(_activate())


@campaign_app.command("pause")
def campaign_pause(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
):
    """Pause a campaign."""
    async def _pause():
        repo, session = await _get_repo()
        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        await repo.update_campaign(campaign, status="paused")
        console.print(f"[yellow]Campaign '{campaign_name}' paused.[/yellow]")
        await _cleanup(session)

    _run(_pause())


# ── Execute once (testing / manual mode) ───────────────────────────────────

@app.command("execute-once")
def execute_once(
    campaign_name: str = typer.Option(..., "--campaign", "-c", help="Campaign name"),
    limit: int = typer.Option(5, "--limit", "-l", help="Max number of leads to process"),
):
    """Execute connection requests for a batch of leads immediately (testing/manual mode)."""
    async def _execute():
        repo, session = await _get_repo()

        campaign = await repo.get_campaign_by_name(campaign_name)
        if not campaign:
            console.print(f"[red]Campaign '{campaign_name}' not found.[/red]")
            raise typer.Exit(1)

        acct = await repo.get_account(campaign.account_id)
        if not acct:
            console.print("[red]Account not found.[/red]")
            raise typer.Exit(1)

        # Get pending leads
        leads = await repo.get_pending_leads(campaign.id, limit=limit)
        if not leads:
            console.print("[yellow]No pending leads to process.[/yellow]")
            return

        console.print(f"Processing {len(leads)} leads from '{campaign_name}'...")
        console.print(f"Account: {acct.name}")
        if campaign.connection_message_template:
            console.print(f"Template: {campaign.connection_message_template}")
        console.print()

        from linauto.campaign.executor import CampaignExecutor
        executor = CampaignExecutor(repo)
        stats = await executor.execute_batch(acct, campaign, leads)

        console.print()
        console.print("[bold]Results:[/bold]")
        console.print(f"  [green]Sent: {stats['success']}[/green]")
        console.print(f"  [yellow]Skipped: {stats['skipped']}[/yellow]")
        console.print(f"  [red]Errors: {stats['error']}[/red]")
        if stats["limit_reached"]:
            console.print("  [red bold]Weekly limit reached — stopped processing.[/red bold]")

        await _cleanup(session)

    _run(_execute())


# ── Scheduler daemon ──────────────────────────────────────────────────────

@app.command("local-login")
def local_login(
    account_name: str = typer.Option(..., "--account", "-a", help="Account name (e.g., 'Nicolas Goehler')"),
):
    """
    Open a headed browser window to log in to LinkedIn locally.

    Launches a visible Chromium window — log in normally, then the session
    is saved automatically. Use this instead of manually pasting cookies.
    Run this before starting the local scheduler.
    """
    async def _local_login():
        from linauto.linkedin.local_login import LocalLoginSession
        from sqlalchemy import select as sa_select

        repo, session = await _get_repo()
        try:
            from linauto.db.models import Account
            result = await session.execute(
                sa_select(Account).where(Account.name == account_name)
            )
            account = result.scalar_one_or_none()
            if not account:
                console.print(f"[red]Account '{account_name}' not found.[/red]")
                raise typer.Exit(1)

            console.print(f"\n[bold]Opening browser for:[/bold] {account.name}")
            console.print("[dim]A Chromium window will open — log in to LinkedIn normally.[/dim]")
            console.print("[dim]This window will close automatically once login is detected.[/dim]\n")

            login = LocalLoginSession()
            result_data = await login.run(
                account_id=account.id,
                account_name=account.name,
                proxy_country=account.proxy_country,
            )

            if result_data["error"] == "timeout":
                console.print("[red]✗ Timed out waiting for login (10 minutes). Try again.[/red]")
                raise typer.Exit(1)

            if not result_data["li_at"]:
                console.print("[red]✗ Login not detected — no li_at cookie found.[/red]")
                raise typer.Exit(1)

            # Save cookie + mark account active
            await repo.update_account(account, li_at_cookie=result_data["li_at"], status="active")
            await session.commit()

            console.print("[green]✓ Login successful![/green]")
            console.print(f"[green]✓ Session saved to:[/green] {result_data['profile_dir']}")
            console.print(f"[green]✓ Cookie updated in DB[/green]")
            console.print("\n[bold]You can now run:[/bold] ./scripts/local_run.sh start")

        finally:
            await _cleanup(session)

    _run(_local_login())


@app.command("run")
def run():
    """Start the scheduler daemon (runs unattended until interrupted)."""
    console.print("[bold]Starting linauto scheduler...[/bold]")
    console.print("Press Ctrl+C to stop.\n")

    async def _run_scheduler():
        from linauto.scheduler.runner import start_scheduler
        await start_scheduler()

    _run(_run_scheduler())


@app.command("debug-profile")
def debug_profile(
    account_name: str = typer.Option(..., "--account", "-a", help="Account name"),
    url: str = typer.Option(..., "--url", "-u", help="LinkedIn profile URL to debug"),
):
    """Debug selector matching on a LinkedIn profile (for troubleshooting)."""
    async def _debug():
        repo, session = await _get_repo()
        account = await repo.get_account_by_name(account_name)
        if not account:
            console.print(f"[red]Account '{account_name}' not found.[/red]")
            raise typer.Exit(1)

        from linauto.linkedin.browser import LinkedInBrowser
        browser = LinkedInBrowser()
        try:
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                console.print("[red]Session expired.[/red]")
                return

            page = await browser.new_page()
            from linauto.linkedin.navigator import LinkedInNavigator
            nav = LinkedInNavigator(page)
            result = await nav.go_to_profile(url)
            if not result.success:
                console.print(f"[red]Navigation failed: {result.error}[/red]")
                return

            console.print(f"[green]Page loaded: {page.url}[/green]")
            console.print()

            # Dump all visible buttons via JavaScript
            buttons = await page.evaluate("""
                () => {
                    const result = [];
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const style = window.getComputedStyle(btn);
                        const visible = style.display !== 'none' && style.visibility !== 'hidden';
                        if (!visible) continue;
                        const text = btn.innerText.trim();
                        if (!text) continue;
                        result.push({
                            text: text.substring(0, 60),
                            classes: btn.className.substring(0, 80),
                            ariaLabel: btn.getAttribute('aria-label') || '',
                            ariaExpanded: btn.getAttribute('aria-expanded') || '',
                            tagName: btn.tagName,
                        });
                    }
                    return result;
                }
            """)

            console.print("[bold]All visible buttons on page:[/bold]")
            for i, btn in enumerate(buttons):
                console.print(f"  [{i}] text={btn['text']!r}  classes={btn['classes']!r}  aria-label={btn['ariaLabel']!r}")

            console.print()

            # Test specific selectors
            import re as re_mod
            tests = [
                ("get_by_role('button', name='Connect', exact)", page.get_by_role("button", name="Connect", exact=True)),
                ("get_by_role('button', name='More', exact)", page.get_by_role("button", name="More", exact=True)),
                ("get_by_role('button', name='More actions', exact)", page.get_by_role("button", name="More actions", exact=True)),
                ("get_by_role('button', name='Pending')", page.get_by_role("button", name=re_mod.compile(r"Pending", re_mod.IGNORECASE))),
                ("get_by_role('button', name='Follow')", page.get_by_role("button", name="Follow")),
                ("get_by_role('button', name='Message')", page.get_by_role("button", name="Message")),
                ("CSS: button:text-is('More')", page.locator('button:text-is("More")')),
                ("CSS: button:has-text('More')", page.locator('button:has-text("More")')),
                ("CSS: button:has-text('Connect')", page.locator('button:has-text("Connect")')),
                ("CSS: button:has-text('Pending')", page.locator('button:has-text("Pending")')),
            ]

            console.print("[bold]Selector test results:[/bold]")
            for name, locator in tests:
                try:
                    count = await locator.count()
                    if count > 0:
                        text = await locator.first.inner_text()
                        console.print(f"  [green]MATCH[/green] {name} → count={count}, text={text.strip()!r}")
                    else:
                        console.print(f"  [red]MISS [/red] {name} → count=0")
                except Exception as e:
                    console.print(f"  [red]ERROR[/red] {name} → {e}")

            console.print()

            # Test JavaScript fallback
            js_more = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (let i = 0; i < buttons.length; i++) {
                        const btn = buttons[i];
                        const style = window.getComputedStyle(btn);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (btn.offsetParent === null && style.position !== 'fixed') continue;
                        if (btn.innerText.trim() === 'More') return {index: i, found: true};
                    }
                    return {index: -1, found: false};
                }
            """)
            if js_more['found']:
                console.print(f"  [green]JS FIND[/green] 'More' button at index {js_more['index']}")
            else:
                console.print(f"  [red]JS MISS[/red] No button with innerText 'More'")

            # Save screenshot
            path = Path("data/debug_screenshots/debug_profile.png")
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=False)
            console.print(f"\n[dim]Screenshot saved: {path}[/dim]")

            await page.close()
        finally:
            await browser.close()
        await _cleanup(session)

    _run(_debug())


@app.command("check-acceptances")
def check_acceptances_cmd(
    account_name: str = typer.Option(..., "--account", "-a", help="Account name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change without updating DB"),
):
    """
    Manually run the acceptance checker for an account (safe — uses ephemeral browser, not pool).

    Loads the invitation manager once, diffs against CONNECTION_REQUESTED leads, visits
    disappeared profiles to confirm accepted vs declined. Safe to run while the scheduler
    is active because it does NOT share the BrowserPool.
    """
    async def _check():
        import re as _re
        from datetime import datetime

        repo, session = await _get_repo()
        account = await repo.get_account_by_name(account_name)
        if not account:
            console.print(f"[red]Account '{account_name}' not found.[/red]")
            raise typer.Exit(1)

        from linauto.db.models import LeadStatus, ActionType, ActionLogStatus
        from linauto.linkedin.browser import LinkedInBrowser
        from linauto.linkedin.actions import LinkedInActions

        def _normalize(url: str) -> str:
            m = _re.search(r'/in/([^/?#\s]+)', url)
            if m:
                return f"/in/{m.group(1).rstrip('/')}"
            return ""

        # Gather CONNECTION_REQUESTED leads across all non-archived campaigns
        # (includes paused campaigns — acceptances should be recorded regardless)
        campaigns = await repo.get_operational_campaigns(account.id)
        campaign_map = {c.id: c for c in campaigns}
        requested_leads = []
        for campaign in campaigns:
            leads = await repo.get_leads_by_status(campaign.id, LeadStatus.CONNECTION_REQUESTED)
            requested_leads.extend(leads)

        console.print(f"[bold]Account:[/bold] {account_name}")
        console.print(f"[bold]CONNECTION_REQUESTED leads:[/bold] {len(requested_leads)}")
        if not requested_leads:
            console.print("[yellow]No CONNECTION_REQUESTED leads — nothing to check.[/yellow]")
            await _cleanup(session)
            return

        browser = LinkedInBrowser()
        try:
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                console.print("[red]Session expired.[/red]")
                if not dry_run:
                    await repo.update_account(account, status="cookie_expired")
                    console.print("[red]Account marked as cookie_expired.[/red]")
                return

            inv_page = await browser.new_page()
            try:
                tracked_slugs = {
                    n for lead in requested_leads
                    if (n := _normalize(lead.linkedin_url))
                }
                console.print("\n[bold]Loading invitation manager...[/bold]")
                inv_actions = LinkedInActions(inv_page)
                result = await inv_actions.get_sent_invitation_urls(stop_when_found=tracked_slugs)
            finally:
                await inv_page.close()

            if not result.success:
                console.print("[red]Failed to load invitation manager.[/red]")
                return

            if not result.session_valid:
                console.print("[red]Session invalid (redirect to login).[/red]")
                if not dry_run:
                    await repo.update_account(account, status="cookie_expired")
                    console.print("[red]Account marked as cookie_expired.[/red]")
                return

            console.print(f"[green]Pending invitations found:[/green] {len(result.urls)}")

            # Diff
            pending_normalized = {_normalize(u) for u in result.urls if _normalize(u)}
            disappeared = [
                lead for lead in requested_leads
                if (n := _normalize(lead.linkedin_url)) and n not in pending_normalized
            ]
            console.print(f"[bold]Disappeared (accepted or declined):[/bold] {len(disappeared)}")

            if not disappeared:
                console.print("[green]All leads still pending — nothing to update.[/green]")
                return

            # Confirm each disappeared lead
            console.print()
            accepted = []
            declined = []
            inconclusive = []

            confirm_page = await browser.new_page()
            try:
                confirm_actions = LinkedInActions(confirm_page)
                for lead in disappeared:
                    status = await confirm_actions.check_connection_status(lead.linkedin_url)
                    name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.linkedin_url
                    if status == "connected":
                        accepted.append(lead)
                        console.print(f"  [green]CONNECTED[/green]  {name}")
                        if not dry_run:
                            await repo.update_lead(
                                lead,
                                status=LeadStatus.CONNECTED,
                                connection_accepted_at=datetime.utcnow(),
                            )
                    elif status == "not_connected":
                        declined.append(lead)
                        console.print(f"  [red]DECLINED  [/red]  {name}")
                        if not dry_run:
                            await repo.update_lead(lead, status=LeadStatus.WITHDRAWN)
                    else:
                        inconclusive.append(lead)
                        console.print(f"  [yellow]UNKNOWN   [/yellow]  {name}  (leaving as CONNECTION_REQUESTED)")
            finally:
                await confirm_page.close()

            console.print()
            console.print(f"[green]Accepted:[/green]     {len(accepted)}")
            console.print(f"[red]Declined:[/red]     {len(declined)}")
            console.print(f"[yellow]Inconclusive:[/yellow] {len(inconclusive)}")
            if dry_run:
                console.print("\n[dim]Dry-run — no changes written to DB.[/dim]")

        finally:
            await browser.close()
        await _cleanup(session)

    _run(_check())


if __name__ == "__main__":
    app()
