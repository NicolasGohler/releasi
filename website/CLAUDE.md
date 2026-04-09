# CLAUDE.md — Releasi Website

## Always Do First
- **Invoke the `frontend-design` skill** before writing any frontend code, every session, no exceptions.

## Project Context
You are building the public-facing website for **Releasi** (relea.si), a boutique outreach service for founders and independent professionals in tech. The website lives at `releasi.vercel.app`. The internal dashboard/admin app lives at `releasi.vercel.app/dashboard` — link to it from the nav as "Login" or "Dashboard".

**Owner:** Nicolas Goehler — solo founder, technical background, building Releasi as a productized service. He is the face of the brand; the site is personal but professional. Not an agency, not a SaaS — a high-trust, high-touch operator.

**What Releasi does:** Engineering applied to go-to-market & fundraising for busy professionals. Automated, targeted LinkedIn outreach that feels human. Primary deliverable: 10 qualified chats in the first 30 days for founding clients, or no charge.

---

## Brand

### Name & Meaning
- **Releasi** — Rel (relationships) + ea (easy) + si / relea.si (release)
- Styled as **Relea.si** in some contexts; use **Releasi** as the primary wordmark

### Personality (3 keywords)
- **Creative** — outreach is a craft; messaging, targeting, and systems are deliberate
- **Easygoing** — no jargon, no over-engineering, no friction
- **Straightforward** — no corporate speak, no buzzwords, say what you mean

### How clients should feel reading this site
- "These people get the founder life."
- "This is the smart, strategic choice."
- "Finally, someone who makes this easy."
- "This feels like a trusted insider."

### Tone & Voice Rules
- Write like a sharp, relaxed founder talking to another founder — not a salesperson
- Short sentences. White space. No padding.
- Lead with outcomes, not process
- Specificity over vagueness: "10 qualified chats in 30 days" not "measurable results"
- Use "I" not "we" — Nicolas is the product
- Avoid: "leverage", "synergy", "unlock", "journey", "empower", "solution", "seamless"
- Contractions are fine. Fragments are fine. Formality is not.

### What Releasi is NOT
- Not a volume play ("spray and pray")
- Not a generic lead gen agency
- Not a SaaS tool
- Not corporate

---

## Colors & Fonts

Pull directly from the dashboard's design system — the website shares the same visual identity.

### Color Palette (use these exact values)
| Token | Value | Use |
|-------|-------|-----|
| Background | `oklch(0.923 0.015 84)` / `#EDE8DB` | Page background — warm cream |
| Foreground | `oklch(0.149 0.012 84)` / `#1C1911` | Body text — near-black |
| Card | `oklch(0.955 0.010 84)` / `#F3EFE5` | Card/surface backgrounds |
| Secondary | `oklch(0.878 0.018 84)` / `#E2DDD0` | Hover states, subtle fills |
| Muted text | `oklch(0.50 0.030 77)` | Secondary/caption text |
| Border | `oklch(0.825 0.020 84)` | Dividers, card outlines |
| Dark surface | `oklch(0.149 0.012 84)` / `#1C1911` | Inverted sections, CTA blocks |

**Never use default Tailwind indigo/blue as a primary color.**

### Typography
| Font | Variable | Use |
|------|----------|-----|
| **Cormorant Garamond** | `--font-brand` | Logo, hero headlines, brand moments — weights 400/500/600 |
| **DM Sans** | `--font-sans` | All body copy, UI labels, nav — clean and approachable |
| **Geist Mono** | `--font-geist-mono` | Stats, numbers, code-adjacent content |

Import from Google Fonts. Use Cormorant Garamond for emotional/headline weight; DM Sans for everything readable.

### Border Radius
- Cards: `0.75rem` (12px)
- Buttons: `0.5rem` (8px)
- Inputs: `0.5rem` (8px)

---

## Brand Assets
All assets are in `brand/`:
- `brand/logo.png` — primary logo mark
- `brand/releasi-banner-2.jpg` — hero/banner image (use as cover/background)
- `brand/banner-ai.png` — secondary AI-generated banner (supplementary)
- `brand/brand-guide.pdf` — full brand guide (reference for edge cases)

Always use real assets — never placeholders where a real asset exists.

---

## Page Structure (from Notion content)

### Sections to include (in order):
1. **Hero** — banner image, logo, tagline: *"Starting relationships with people you wish you knew."*
2. **What I do** — "Engineering applied to go-to-market & fundraising for busy professionals."
3. **The offer** — "For founding clients, I'll get you 10 qualified chats in the first 30 days, or you don't pay."
4. **What it looks like** — three service pillars:
   - **Intent Definition & Setup** — optimize account for trust & authenticity, define ideal contact profile
   - **LinkedIn Growth** — targeted connection campaigns, 700+ new high-value contacts/month
   - **Telegram Networking** — right groups + contacts, 10%+ reply rate, the channel most people ignore
5. **Results** — social proof (specific numbers):
   - Teams generated multiple 6 figures in revenue opportunities in 8 months
   - 500 → 3,000 high-value connections + 6 figures in revenue in one year
   - 10%+ reply rate & multiple 5 figures in pipeline on Telegram
6. **CTA / Footer** — Book a call (Google Calendar link), LinkedIn, Login/Dashboard link

### Links to wire up:
- Book a call: `https://calendar.app.google/NQbomXBUjrZcNN4a9`
- LinkedIn: `https://www.linkedin.com/in/nicolas-goehler/`
- Dashboard/Login: `/dashboard`

---

## Local Dev Server
- Framework: Next.js (same as dashboard — shares the repo)
- Start: `npm run dev` from `website/`
- Default port: `http://localhost:3000`
- Never screenshot a `file:///` URL — always serve from localhost

## Screenshot Workflow
- Use the **Claude Preview MCP** (`mcp__Claude_Preview__*`) for screenshots — it's available in this environment
- `preview_start` → `preview_screenshot` → analyze → fix → repeat
- Do at least 2 comparison rounds against any reference. Stop only when no visible differences remain.
- Be specific when comparing: "heading is 32px but reference shows ~24px", "gap should be 24px not 16px"
- Check: spacing/padding, font size/weight/line-height, colors (exact), alignment, border-radius, shadows

---

## Anti-Generic Guardrails
- **Shadows:** Never flat `shadow-md`. Use layered, color-tinted shadows with low opacity.
- **Gradients:** Layer multiple radial gradients. Add grain/texture via SVG noise for depth.
- **Animations:** Only animate `transform` and `opacity`. Never `transition-all`. Spring-style easing.
- **Interactive states:** Every clickable element needs hover, focus-visible, and active states. No exceptions.
- **Images:** Add gradient overlay (`bg-gradient-to-t from-black/60`) and color treatment layer.
- **Spacing:** Intentional, consistent spacing tokens — not random Tailwind steps.
- **Depth:** Surfaces need a layering system (base → elevated → floating).

## Hard Rules
- Do not add sections, features, or content not in the reference
- Do not "improve" a reference — match it, then ask
- Do not stop after one screenshot pass
- Do not use `transition-all`
- Do not use default Tailwind blue/indigo as primary color
- Do not use "we" — Nicolas is the product, use "I"
- Do not write marketing fluff — every sentence must earn its place
