# Fundraising Agent — Context for Claude Code

## What This Project Does

This is an automated lead generation pipeline for **MPM Labs**, a Web3 marketing consultancy. It scrapes recent fundraising rounds from CryptoRank and RootData, identifies the team members behind each project, enriches them with LinkedIn/Twitter/Telegram data, and delivers a CSV to Slack. Nicolas then manually crafts personalized outreach via Telegram and LinkedIn.

**Outreach is always manual and never automated.** This system only automates the research and qualification step.

## Business Context

MPM Labs offers marketing and growth services to crypto projects. Projects that recently raised funding are ideal prospects — they have budget, momentum, and typically need marketing help to capitalize on their raise. This agent finds those projects weekly and identifies the right people to reach out to (founders, CEOs, CMOs, heads of marketing/growth).

## Pipeline Architecture

```
CryptoRank (Playwright) ──→ project list (up to 15)
RootData (Playwright)   ──→ project list (up to 15)
                              ↓
                        merge & dedup (by name)
                              ↓
                    For each CryptoRank project:
                        scrape /team page → names, roles, LinkedIn, Twitter
                              ↓
                    Apollo people search (free, no credits) → supplement team
                        members, fill missing LinkedIn/Twitter
                              ↓
                    Telegram resolution (Telethon) → check if Twitter
                        usernames exist on Telegram
                              ↓
                    CSV + JSON output → Slack upload
```

Runs every **Monday at 9:00 UTC** on GitHub Actions, plus manual `workflow_dispatch`.

## Target Roles (Included)

- CEO, Founder, Co-Founder, COO, CFO
- CMO, Head of Marketing, Head of Growth, Head of Brand
- Head of Community, Head of Content, Head of Design
- General Counsel, VP-level roles
- Any C-suite or leadership not in the excluded list

## Excluded Roles

CTO, engineers, developers, HR/recruiting, traders, sales/BD, product managers — these are filtered out both from CryptoRank scraping and Apollo results.

## Nicolas's Manual Workflow (After Receiving CSV)

1. Reviews the CSV in Slack
2. Prioritizes leads with Telegram usernames (fastest outreach)
3. Falls back to LinkedIn for those without Telegram
4. Crafts hyper-customized outreach DMs referencing their recent fundraise

## Outreach Priorities

1. **Telegram** — preferred channel, most responsive in crypto
2. **LinkedIn** — fallback when no Telegram
3. **Email** — not currently used (enrichment disabled to save Apollo credits)

## File Structure

| File | Purpose |
|------|---------|
| `fundraising.py` | Full pipeline: scrape, enrich, resolve Telegram, output CSV, send to Slack |
| `requirements.txt` | Python dependencies |
| `.github/workflows/fundraising.yml` | GitHub Actions workflow (weekly + manual trigger) |

## Data Sources & APIs

| Service | Purpose | Credits |
|---------|---------|---------|
| CryptoRank (Playwright) | Scrape funding rounds + team pages | Free (web scraping) |
| RootData (Playwright) | Scrape funding rounds | Free (web scraping) |
| Apollo.io People Search | Find team members by company domain | Free (no credits) |
| Apollo.io Bulk Enrichment | Email enrichment (currently disabled) | Costs credits |
| Telegram (Telethon) | Resolve Twitter usernames on Telegram | Free (client API) |
| Slack | CSV delivery + notifications | Bot token |

## CryptoRank Scraping Notes

CryptoRank uses CSS-in-JS with generated class names that can change between deployments. The scraper uses `styles_name__` prefix matching (CSS module pattern) rather than exact class names. If scraping breaks, check whether CryptoRank changed their DOM structure — look for the team container section and name/role/link elements within it.

Key classes (as of Feb 2026):
- `styles_name__*` — team member names (`<p>` tags)
- `styles_container__*` — individual team member cards (`<div>`)
- `styles_badge_root__*` — role badges (`<button>`)
- `styles_circle_link__*` — social media links (`<a>`)

## Key Preferences

- **Never automate outreach or message sending** — only automate research
- **Save Apollo credits** — the people search endpoint is free; email enrichment (which costs credits) is disabled since outreach happens via Telegram/LinkedIn
- **CryptoRank team page is the primary data source** — always scrape it first, Apollo supplements
- **Telegram > LinkedIn > Email** for outreach channel priority
- **GitHub Actions 6-hour limit** — pipeline typically completes in ~20 minutes
- API keys are passed via GitHub Secrets (`APOLLO_API_KEY`, `SLACK_BOT_TOKEN`); Telegram credentials are hardcoded

## Related Projects

| Directory | What It Does |
|-----------|-------------|
| `A&GH - TG Lead Gen` | Scans Telegram group chats for project announcements, enriches with Twitter/website data |
| `A&GH - Telegram Members List` | Fetches TG group member lists with bios, exports to CSV |
