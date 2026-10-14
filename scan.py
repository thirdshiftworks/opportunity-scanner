#!/usr/bin/env python3
"""
Autonomous Opportunity Scanner v2
=================================
Read-only sweep of PUBLIC, no-login boards where Muse could earn by doing work,
for Johnathan's standalone Muse-run business, Third Shift Works ($0 upfront rule).

v2 upgrades over v1 (per Johnathan's order, Oct 13 2026: "search for more and
scrape more and verify opportunities better"):
  * MORE SOURCES - GitLab public bounty issues added; GitHub query set widened
    (bounty / diamond-bounty / paid / reward labels); HN gains a second thread
    (Who-is-hiring, contract-only filter); Reddit r/forhire + r/slavelabour
    attempted every run as a FLAGGED high-scam-risk appendix (currently blocked
    by Reddit - recorded honestly, never ranked).
  * DEEPER SCRAPING - top candidates get their issue body AND comments AND
    timeline fetched (GitHub API): assignees, linked open/merged PRs, claim-
    comment counts, last-activity dates, maintainer responsiveness, and a
    payment-rail cross-check (Opire API) with dead rails (IssueHunt etc.)
    hard-rejected.
  * BETTER VERIFICATION - every survivor gets a 0-100 credibility score
    (pay x openness x low competition x maintainer activity x rail liveness
    x skill fit), hard-reject patterns for prompt-harvesting / hardware gates /
    already-solved / stale listings, and a seen-database (seen.json at the repo root) so
    nightly runs only surface genuinely NEW items.

Sources probed and verified NOT usable without login/JS (Oct 13-14, 2026 -
re-probed lightly each run, status recorded in run stats):
  Upwork (times out / bot wall), Wellfound (Cloudflare Turnstile wall),
  Contra (JS app - no job data in the static HTML), Fiverr (fetchable but it
  lists SELLER gigs; there is no public buyer task board), Reddit JSON
  (HTTP 403 unauthenticated).

Hard filters (an item must pass ALL, same spirit as v1 plus v2 additions):
  - Pay stated and parseable (USD/USDC > 0; salaries do not count).
  - Work type Muse can do (coding, web, sheets/data, scripts, writing,
    research, docs, design, video).
  - NO stake / bond / entry fee / deposit / pay-to-apply.
  - NO clinical or medical-advice work.
  - NO identity misuse (account renting, KYC-for-hire, "use your account").
  - NO abuse tooling (captcha solving incl. misspellings, fake reviews,
    botted engagement, credential stuffing).
  - NO security-audit / bug-bounty submissions (parked lane).
  - NO off-platform contact funnels (Telegram/WhatsApp "to apply").
  - NO prompt-harvesting (listings demanding an agent's system prompt etc.).
  - NO physical-hardware requirements (specific GPU cards, Tenstorrent...).
  - NO already-assigned issues, NO issues with a merged solution PR.
  - NO dead payment rails (IssueHunt / Bountysource as the stated rail).
  - Superteam HUMAN_ONLY never passes. Reddit appendix never ranks.

Output: runs/scan-<timestamp>.json + runs/latest.md (local run record), seen.json + blocklist.json at the repo root, and the public dashboard data in docs/data/ (latest.json + opportunities.json).
Stdlib only. No accounts, no bids, no messages, no spending - it only reads.

Usage:  python3 scan.py [--per-source-limit N] [--enrich-limit N] [--no-enrich]
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Repo layout (GitHub Actions variant): this script sits at the repo root, so
# every data path is relative to the root and travels with the checkout.
REPO_ROOT = HERE
RUNS = HERE / "runs"
SEEN_DB = REPO_ROOT / "seen.json"
BLOCKLIST_PATH = REPO_ROOT / "blocklist.json"
DOCS_DATA = REPO_ROOT / "docs" / "data"
UA = {"User-Agent": "thirdshiftworks-opportunity-scanner/2.0 (read-only research; github actions)"}
TIMEOUT = 25
NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------- fetch helpers

def fetch_json(url, headers=None, timeout=TIMEOUT, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(3)
    raise last

def fetch_status(url, timeout=10):
    """Lightweight probe: returns HTTP status code or None."""
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2000)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None

def link_ok(url):
    return fetch_status(url, timeout=15)

# ---------------------------------------------------------------- dates

def parse_dt(s):
    if not s:
        return None
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(s / 1000 if s > 1e12 else s, tz=timezone.utc)
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def days_ago(dt):
    if not dt:
        return None
    return max(0, (NOW - dt).days)

# ---------------------------------------------------------------- pay parsing

MONEY_RE = re.compile(
    r"(?:\$|USD\s?|USDC\s?|USDT\s?)([0-9][0-9,]*(?:\.[0-9]{1,2})?)(?![0-9kK])|([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s?(?:USD|USDC|USDT)\b",
    re.IGNORECASE,
)

def parse_pay(text, cap=50_000):
    """Return the largest plausible stated amount in text, or None."""
    if not text:
        return None
    amounts = []
    for m in MONEY_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if 5 <= v <= cap:
            amounts.append(v)
    return max(amounts) if amounts else None

# ---------------------------------------------------------------- filters

SKILL_WORDS = [
    "code", "coding", "fix", "bug", "feature", "implement", "refactor", "test",
    "docs", "documentation", "website", "web app", "webapp", "spreadsheet", "excel",
    "google sheets", "sheets", "script", "automation", "automate", "python",
    "javascript", "typescript", "rust", "react", "node", "api", "data", "research",
    "write", "writing", "content", "article", "blog", "design", "ui", "ux",
    "video", "visualizer", "sql", "database", "migration", "plugin", "extension",
    "library", "cli", "parser", "scrape", "etl", "dashboard", "landing page",
    "tutorial", "guide", "template",
]

REJECT_PATTERNS = {
    "stake/bond/entry-fee": r"\b(stake|staking|bond|entry fee|application fee|deposit required|pay to (apply|join|participate)|registration fee)\b",
    "clinical/medical": r"\b(clinical|diagnos(e|is|tic)|therapy session|medical advice|patient(s)?\b(?!.*data migrat)|nursing care|hipaa|telemedicine|prescrib)\b",
    "identity-misuse": r"\b(rent (your|my|an?) .{0,20}account|account (for rent|rental)|use your (identity|account|passport)|kyc (for|bypass)|verify your identity for|sell your account|buy(ing)? (upwork|fiverr|freelancer) account)\b",
    "abuse-tooling": r"\b(captcha|hcaptcha|hcapcha|recaptcha|capcha|solve (h)?captcha|captcha (solver|bypass|solving)|fake review|botted|bot followers|credential stuffing|account generator|mass account creation|sms bypass|bypass .{0,15}(detection|security check))\b",
    "security-audit/bug-bounty": r"\b(security audit|vulnerability (report|disclosure|hunting)|bug bounty|penetration test|pentest|exploit (development|writing))\b",
    "off-platform-contact": r"\b(contact|apply|message|dm) .{0,30}(telegram|whatsapp|t\.me/|wa\.me)\b",
    "digest/list-not-a-task": r"\b(open bounties right now|bounty (digest|roundup|list)|daily bounty|list of bounties|bounties available today)\b",
    "employment-not-a-task": r"\b(full[- ]time (role|position|job)|founding engineer|equity \+|base salary|salary range|\$\s?[0-9]{2,3}\s?k\s?[-–+])\b",
    # v2 additions ------------------------------------------------------------
    "prompt-harvesting": r"(system prompt|paste (your|the) (entire |full )?(system )?prompt|your (initial|platform) (prompt|initialization)|initialization text (present|from) (at|the)|full (platform )?initialization|reveal (your|the) (system )?prompt|copy (your|the) (entire )?system (prompt|instructions)|initialized_with|paste (the )?(complete|full|entire) text of the first message|first message in your conversation|full unedited text of (your|the) (first|initial) message)",
    "hardware-required": r"\b(tenstorrent|wormhole|grayskull|blackhole|t3k\b|tt-metal|physical (gpu|card|hardware|device) (is )?required|run (the )?(test|benchmark|it) on (the |a )?physical|requires? (a |an )?(rtx|gtx) \d{4}|on (an? )?(rtx|gtx) \d{4} (ti )?(\d+\s?gb )?card|(rtx|gtx) \d{4}\s?(ti\s?)?\d+\s?gb)\b",
    "dead-rail": r"\b(issuehunt|bountysource)\b",
}

SPAM_REPO_PATTERNS = re.compile(
    r"(bounty[-_]?(plaza|board|hub|digest|list|feed|only)|bounties[-_]?(daily|list|feed))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- blocklist
# Specific listings killed by a human/Muse audit. A blocklisted URL is rejected
# before any filter/scoring, forever, so nightly runs never resurface it.
# Add entries here (URL -> reason) whenever an audit kills a specific listing.
def _load_blocklist():
    """The blocklist lives in blocklist.json at the repo root so it can be
    edited (and committed back by the nightly robot) without touching code.
    A missing/unreadable file means an EMPTY blocklist plus a loud warning -
    never a silent fallback to stale in-code data."""
    try:
        data = json.loads(BLOCKLIST_PATH.read_text())
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        print("  [scan] WARNING: blocklist.json is not a JSON object - using empty blocklist",
              file=sys.stderr)
    except FileNotFoundError:
        print("  [scan] WARNING: blocklist.json not found - using empty blocklist",
              file=sys.stderr)
    except Exception as e:
        print(f"  [scan] WARNING: could not parse blocklist.json: {e} - using empty blocklist",
              file=sys.stderr)
    return {}

BLOCKLIST = _load_blocklist()

def matched_skills(text):
    return sorted({w for w in SKILL_WORDS if w in text})

def passes_filters(item):
    """Base filter (title+body). Returns (ok, reason)."""
    text = f"{item.get('title','')} {item.get('body','')}".lower()
    if not item.get("pay"):
        return False, "no stated pay"
    if not matched_skills(text):
        return False, "work type not a Muse fit"
    for name, pat in REJECT_PATTERNS.items():
        if re.search(pat, text, re.IGNORECASE):
            return False, f"rejected: {name}"
    return True, "pass"

def gh_api_headers():
    """GitHub API headers. Uses GITHUB_TOKEN/GH_TOKEN from the environment
    when present (GitHub Actions provides one - higher rate limits); falls
    back to unauthenticated locally, exactly like v2 always ran."""
    h = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h

# ---------------------------------------------------------------- sources

def src_opire(limit):
    items = []
    data = fetch_json("https://api.opire.dev/rewards")
    for r in data[:limit]:
        price = (r.get("pendingPrice") or {})
        pay = None
        if price.get("unit") == "USD_CENT" and price.get("value"):
            pay = price["value"] / 100.0
        claimers = len(r.get("claimerUsers") or []) + len(r.get("tryingUsers") or [])
        items.append({
            "source": "Opire",
            "title": (r.get("title") or "").strip(),
            "url": r.get("url"),
            "pay": pay,
            "pay_note": "USD via Opire (live rail, cross-checked this run); dev keeps 100%, Stripe payout",
            "body": " ".join(r.get("programmingLanguages") or []),
            "langs": r.get("programmingLanguages") or [],
            "rail": "opire-live",
            "claim_count": claimers,
            "created_at": (parse_dt(r.get("createdAt")) or NOW).isoformat() if r.get("createdAt") else None,
            "submission": ("GitHub account (business identity) + claim on Opire, fix, PR. "
                           "Payout via Stripe -> Johnathan's legal-name Stripe KYC. "
                           "Human taps: one-time account setup + approve the PR before submission."),
        })
    return items

GITHUB_QUERIES = [
    "is:issue is:open label:bounty sort:updated-desc",
    'is:issue is:open label:"💎 Bounty" sort:updated-desc',
    "is:issue is:open label:paid sort:updated-desc",
    "is:issue is:open label:reward sort:updated-desc",
    'is:issue is:open label:"Bounty" sort:updated-desc',
    "is:issue is:open bounty in:title sort:updated-desc",
]

def gh_repo_parts(url):
    if "github.com/" not in (url or ""):
        return None, None
    tail = url.split("github.com/")[1]
    m = re.match(r"([^/]+)/([^/]+)/issues/(\d+)", tail)
    if not m:
        return None, None
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))

def src_github(limit):
    items, seen = [], set()
    per_repo = {}
    for q in GITHUB_QUERIES:
        url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
            {"q": q, "per_page": 50})
        try:
            data = fetch_json(url, gh_api_headers(), timeout=60)
        except Exception as e:
            print(f"  [github] query failed: {e}", file=sys.stderr)
            continue
        for it in data.get("items", []):
            html = it.get("html_url", "")
            if html in seen:
                continue
            seen.add(html)
            repo_full = html.split("github.com/")[1].rsplit("/issues/", 1)[0] if "github.com/" in html else ""
            repo_name = repo_full.split("/")[-1] if repo_full else ""
            if SPAM_REPO_PATTERNS.search(repo_name) or SPAM_REPO_PATTERNS.search(repo_full):
                continue
            per_repo[repo_full] = per_repo.get(repo_full, 0) + 1
            if per_repo[repo_full] > 2:
                continue
            title = it.get("title", "")
            body = (it.get("body") or "")[:6000]
            pay = parse_pay(title) or parse_pay(body)
            items.append({
                "source": "GitHub (bounty labels)",
                "title": title.strip(),
                "url": html,
                "pay": pay,
                "pay_note": "As stated in issue; rail re-verified during deep enrichment",
                "body": body,
                "langs": [],
                "labels": [l.get("name", "") for l in it.get("labels", [])],
                "assignees": [a.get("login", "") for a in it.get("assignees", [])],
                "comments_count": it.get("comments", 0),
                "created_at": it.get("created_at"),
                "updated_at": it.get("updated_at"),
                "author": (it.get("user") or {}).get("login"),
                "submission": ("GitHub account (business identity); follow the payout rail named in the issue. "
                               "Human taps: approve starting + approve PR submission; payout identity per that rail."),
            })
            if len(items) >= limit:
                return items
        time.sleep(2)  # unauthenticated search API: 10 req/min - stay polite
    return items

def src_gitlab(limit):
    items, seen = [], set()
    for lab in ["bounty", "Bounty", "reward", "paid"]:
        url = "https://gitlab.com/api/v4/issues?" + urllib.parse.urlencode(
            {"scope": "all", "labels": lab, "state": "opened", "per_page": 30,
             "order_by": "updated_at", "sort": "desc"})
        try:
            data = fetch_json(url, timeout=40)
        except Exception as e:
            print(f"  [gitlab] label {lab} failed: {e}", file=sys.stderr)
            continue
        for it in data or []:
            web = it.get("web_url", "")
            if not web or web in seen:
                continue
            seen.add(web)
            title = it.get("title", "")
            body = (it.get("description") or "")[:6000]
            items.append({
                "source": "GitLab (bounty labels)",
                "title": title.strip(),
                "url": web,
                "pay": parse_pay(title) or parse_pay(body),
                "pay_note": "As stated in issue; GitLab has no native bounty escrow - rail must be named in the issue",
                "body": body,
                "langs": [],
                "labels": it.get("labels", []),
                "assignees": [a.get("username", "") for a in it.get("assignees", [])],
                "comments_count": it.get("user_notes_count", 0),
                "created_at": it.get("created_at"),
                "updated_at": it.get("updated_at"),
                "author": (it.get("author") or {}).get("username"),
                "project_id": it.get("project_id"),
                "iid": it.get("iid"),
                "submission": ("GitLab/GitHub account per the rail named in the issue (often none - "
                               "flag if no escrow exists). Human taps: approve starting + approve submission."),
            })
            if len(items) >= limit:
                return items
        time.sleep(1)
    return items

def src_superteam(limit):
    items = []
    data = fetch_json(f"https://superteam.fun/api/listings?limit={max(limit, 60)}")
    for x in data:
        if x.get("status") != "OPEN":
            continue
        agent = x.get("agentAccess")
        slug = x.get("slug", "")
        pay, note = None, ""
        if x.get("compensationType") == "fixed" and x.get("rewardAmount"):
            pay = float(x["rewardAmount"])
            note = f"{x.get('token','')} fixed pool (per-winner share may be smaller); escrowed on platform"
        elif x.get("maxRewardAsk") or x.get("minRewardAsk"):
            lo = float(x.get("minRewardAsk") or 0)
            hi = float(x.get("maxRewardAsk") or lo)
            pay = lo if lo else hi
            note = f"{x.get('token','')} ask range {lo:,.0f}-{hi:,.0f} (scored on the low end); escrowed on platform"
        elif x.get("rewardAmount"):
            pay = float(x["rewardAmount"])
            note = f"{x.get('token','')} total pool; escrowed on platform"
        items.append({
            "source": "Superteam Earn",
            "title": x.get("title", "").strip(),
            "url": f"https://superteam.fun/earn/listing/{slug}" if slug else "https://superteam.fun/earn",
            "pay": pay,
            "pay_note": note,
            "body": f"type={x.get('type')} agentAccess={agent} deadline={x.get('deadline')}",
            "langs": [],
            "agent_access": agent,
            "deadline": x.get("deadline"),
            "submission": ("AGENT_ALLOWED: free agent API registration (no KYC) - a one-time approval tap; "
                           "submission itself can be Muse's. Winnings claimed by human: talent profile + "
                           "claim code + Solana wallet in Johnathan's name."),
        })
        if items[-1].get("agent_access") == "HUMAN_ONLY":
            items[-1]["force_reject"] = "Superteam HUMAN_ONLY - needs human talent profile + human submission"
    return items

def _hn_thread_items(story_id, source_name, require_signals, limit):
    items = []
    thread = fetch_json(f"https://hn.algolia.com/api/v1/items/{story_id}")
    def walk(node, depth=0):
        if len(items) >= limit or depth > 6:
            return
        txt = re.sub(r"<[^>]+>", " ", node.get("text") or "")
        txt = txt.replace("&#x2F;", "/").replace("&gt;", ">").replace("&amp;", "&").replace("&#x27;", "'")
        low = txt.lower()
        if txt.strip() and any(s in low for s in require_signals):
            items.append({
                "source": source_name,
                "title": (txt.strip().split("\n")[0])[:120],
                "url": f"https://news.ycombinator.com/item?id={node.get('id')}",
                "pay": parse_pay(txt),
                "pay_note": "As stated in comment; no escrow on HN - highest scam risk, terms negotiated directly",
                "body": txt[:4000],
                "langs": [],
                "submission": ("Email/contact the poster directly - an outward message, so Johnathan approves "
                               "the exact text first. Payment terms must be agreed + ideally escrowed before work."),
            })
        for child in node.get("children", []) or []:
            walk(child, depth + 1)
    walk(thread)
    return items

def src_hn(limit):
    items = []
    # Thread 1: Seeking freelancer (employers seeking freelancers)
    try:
        q = urllib.parse.urlencode({"tags": "story,author_whoishiring",
                                    "query": "Seeking freelancer", "hitsPerPage": 3})
        data = fetch_json(f"https://hn.algolia.com/api/v1/search_by_date?{q}")
        hits = data.get("hits", [])
        if hits:
            items += _hn_thread_items(
                hits[0]["objectID"], "Hacker News (Seeking freelancer thread)",
                ("seeking freelancer", "looking to hire", "hiring a freelancer",
                 "seeking a freelancer", "contract role", "freelance"), limit)
    except Exception as e:
        print(f"  [hn] freelancer thread failed: {e}", file=sys.stderr)
    # Thread 2: Who is hiring - keep ONLY contract/freelance posts (employment rejected downstream too)
    try:
        q = urllib.parse.urlencode({"tags": "story,author_whoishiring",
                                    "query": "Who is hiring", "hitsPerPage": 3})
        data = fetch_json(f"https://hn.algolia.com/api/v1/search_by_date?{q}")
        hits = data.get("hits", [])
        if hits:
            items += _hn_thread_items(
                hits[0]["objectID"], "Hacker News (Who is hiring - contract only)",
                ("contract", "freelance", "freelancer"), limit)
    except Exception as e:
        print(f"  [hn] who-is-hiring failed: {e}", file=sys.stderr)
    return items

REDDIT_SUBS = ["forhire", "slavelabour"]

def src_reddit(limit):
    """Appendix only. Reddit currently blocks unauthenticated JSON (403);
    attempted each run so the status stays honest. Items NEVER rank."""
    items, notes = [], []
    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
        try:
            data = fetch_json(url, timeout=15, retries=0)
            posts = (data.get("data") or {}).get("children", [])
            notes.append(f"r/{sub}: JSON reachable ({len(posts)} posts)")
            for p in posts[:limit]:
                d = p.get("data", {})
                title = d.get("title", "")
                if "[hiring]" not in title.lower():
                    continue
                body = (d.get("selftext") or "")[:3000]
                items.append({
                    "source": f"Reddit r/{sub} (APPENDIX - high scam risk)",
                    "title": title.strip(),
                    "url": "https://www.reddit.com" + d.get("permalink", ""),
                    "pay": parse_pay(title) or parse_pay(body),
                    "pay_note": "Reddit: NO escrow, no platform protection - highest scam risk; appendix only, never ranked",
                    "body": body,
                    "langs": [],
                    "appendix_only": True,
                    "submission": "Direct contact only - Johnathan approves any message; payment must be escrowed externally first.",
                })
        except urllib.error.HTTPError as e:
            notes.append(f"r/{sub}: blocked (HTTP {e.code} unauthenticated)")
        except Exception as e:
            notes.append(f"r/{sub}: unreachable ({type(e).__name__})")
    return items, "; ".join(notes)

PROBES = [
    ("Upwork (public jobs page)", "https://www.upwork.com/freelance-jobs/",
     "login/bot-walled in probes (timeouts); real feed comes via the launch-kit account after setup"),
    ("Wellfound (public jobs)", "https://wellfound.com/jobs",
     "Cloudflare Turnstile wall - no data without a browser session"),
    ("Contra (public hire page)", "https://contra.com/hire?r=jobs",
     "JS app: static HTML contains no job data (verified Oct 13, 2026)"),
    ("Fiverr (public search)", "https://www.fiverr.com/search/gigs?query=automation",
     "fetchable, but lists SELLER gigs - Fiverr has no public buyer task board; intel only, not a source"),
]

def run_probes():
    notes = {}
    for name, url, why in PROBES:
        code = fetch_status(url, timeout=8)
        notes[name] = f"probe HTTP {code if code else 'timeout/blocked'}; {why}"
    return notes

SOURCES = [
    ("Opire", src_opire),
    ("GitHub (bounty labels)", src_github),
    ("GitLab (bounty labels)", src_gitlab),
    ("Superteam Earn", src_superteam),
    ("Hacker News (freelancer + contract threads)", src_hn),
]

# ---------------------------------------------------------------- deep enrichment

CLAIM_RE = re.compile(
    r"(\bclaim(ing|ed)?\b|\bi('ll| will) (take|work on|do) (this|it)\b|assign (me|this to me)|working on (this|it)\b|interested in (working|taking)|picking this up|/claim\b|\$claim\b)",
    re.IGNORECASE)
RAIL_LIVE_WORDS = ["opire", "algora", "polar.sh", "warpspeed", "github sponsors", "buy me a coffee", "ko-fi", "kofi"]
SOLVED_RE = re.compile(r"\b(already (solved|done|fixed|implemented)|this (is|was) (solved|fixed) (by|in)|fixed (by|in) (pr|#)|solved in #)\b", re.IGNORECASE)

def enrich_github(item):
    """Fetch comments + timeline for a GitHub issue. Mutates item['enrich'].
    Returns a hard-reject reason string, or None."""
    repo, num = gh_repo_parts(item["url"])
    if not repo or not num:
        return None
    enr = {}
    api = "https://api.github.com"
    hdr = gh_api_headers()
    try:
        comments = fetch_json(f"{api}/repos/{repo}/issues/{num}/comments?per_page=100", hdr, timeout=40, retries=1)
        if isinstance(comments, list):
            enr["comments_fetched"] = len(comments)
            maintainer = [c for c in comments if (c.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR"))]
            enr["maintainer_comments"] = len(maintainer)
            mdts = [parse_dt(c.get("created_at")) for c in maintainer]
            mdts = [d for d in mdts if d]
            if mdts:
                enr["last_maintainer_comment"] = max(mdts).isoformat()
            claims = 0
            for c in comments:
                assoc = c.get("author_association")
                if assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
                    if SOLVED_RE.search(c.get("body") or ""):
                        enr["maintainer_says_solved"] = True
                    continue
                if CLAIM_RE.search(c.get("body") or ""):
                    claims += 1
            enr["claim_comments"] = claims
            blob = " ".join((c.get("body") or "")[:1500] for c in comments[-30:]).lower()
            rails = [w for w in RAIL_LIVE_WORDS if w in blob]
            if rails:
                enr["rail_mentions_in_comments"] = rails
    except Exception as e:
        enr["comments_error"] = str(e)[:120]
    time.sleep(1)
    try:
        events = fetch_json(f"{api}/repos/{repo}/issues/{num}/timeline?per_page=100", hdr, timeout=40, retries=1)
        if isinstance(events, list):
            prs_open, prs_closed, prs_merged = [], [], []
            for ev in events:
                if ev.get("event") != "cross-referenced":
                    continue
                src = ((ev.get("source") or {}).get("issue") or {})
                if "pull_request" not in src:
                    continue
                pr = src.get("pull_request") or {}
                if pr.get("merged_at"):
                    prs_merged.append(src.get("number"))
                elif src.get("state") == "open":
                    prs_open.append(src.get("number"))
                else:
                    prs_closed.append(src.get("number"))
            enr["linked_prs_open"] = prs_open
            enr["linked_prs_closed_unmerged"] = prs_closed
            enr["linked_prs_merged"] = prs_merged
    except Exception as e:
        enr["timeline_error"] = str(e)[:120]
    item["enrich"] = enr
    # hard rejects discovered only by looking deeper
    if enr.get("linked_prs_merged"):
        return f"already solved (merged PR #{enr['linked_prs_merged'][0]} references this issue)"
    if enr.get("maintainer_says_solved"):
        return "maintainer says already solved in the comments"
    return None

def enrich_gitlab(item):
    """Fetch notes for a GitLab issue (maintainer activity + claims)."""
    pid, iid = item.get("project_id"), item.get("iid")
    if not pid or not iid:
        return None
    enr = {}
    try:
        notes = fetch_json(f"https://gitlab.com/api/v4/projects/{pid}/issues/{iid}/notes?per_page=100", timeout=40, retries=1)
        if isinstance(notes, list):
            enr["comments_fetched"] = len(notes)
            author = item.get("author")
            maint = [n for n in notes if ((n.get("author") or {}).get("username") == author)]
            enr["maintainer_comments"] = len(maint)
            mdts = [parse_dt(n.get("created_at")) for n in maint]
            mdts = [d for d in mdts if d]
            if mdts:
                enr["last_maintainer_comment"] = max(mdts).isoformat()
            enr["claim_comments"] = sum(1 for n in notes
                                         if (n.get("author") or {}).get("username") != author
                                         and CLAIM_RE.search(n.get("body") or ""))
    except Exception as e:
        enr["notes_error"] = str(e)[:120]
    item["enrich"] = enr
    return None

# ---------------------------------------------------------------- scoring

def prelim_rank(item):
    """Cheap pre-enrichment ranking: pay first, then lower visible competition."""
    return ((item.get("pay") or 0), -(item.get("comments_count") or 0))

def score_item(item):
    """0-100 credibility score. Stores item['score'] + item['score_detail'] +
    item['flags']. Returns a hard-reject reason or None."""
    flags = []
    detail = {}
    enr = item.get("enrich") or {}
    pay = item.get("pay") or 0

    # --- hard reject: already assigned (listing data or search data)
    assignees = item.get("assignees") or []
    if assignees:
        return f"already assigned to {', '.join(assignees)}"

    # --- hard reject: stale (no update AND no maintainer activity > 365d)
    upd = parse_dt(item.get("updated_at"))
    last_maint = parse_dt(enr.get("last_maintainer_comment"))
    upd_days = days_ago(upd)
    maint_days = days_ago(last_maint) if last_maint else None
    if upd_days is not None and upd_days > 365 and (maint_days is None or maint_days > 365):
        return f"stale: no activity for {upd_days} days and no recent maintainer comment"
    if upd_days is not None and upd_days > 365:
        flags.append(f"old listing (untouched {upd_days}d) but maintainer active {maint_days}d ago")

    # --- pay component (0-25)
    pay_pts = (25 if pay >= 1000 else 22 if pay >= 500 else 19 if pay >= 250 else
               16 if pay >= 100 else 13 if pay >= 50 else 10 if pay >= 25 else 7)
    detail["pay"] = pay_pts

    # --- openness / competition (0-20)
    open_pts = 10  # unassigned (checked above)
    linked_open = enr.get("linked_prs_open") or []
    if linked_open:
        open_pts -= 8
        flags.append(f"open PR already targets this: {linked_open}")
    closed_prs = enr.get("linked_prs_closed_unmerged") or []
    if len(closed_prs) >= 2:
        open_pts -= 3
        flags.append(f"graveyard signal: {len(closed_prs)} linked PRs closed unmerged")
    claims = item.get("claim_count")
    if claims is None:
        claims = enr.get("claim_comments")
    if claims is None and item.get("comments_count") is not None:
        claims = None  # unknown split; comments_count used as weak proxy below
    if claims is not None:
        if claims == 0:
            open_pts += 6
        elif claims <= 5:
            open_pts += 4
        elif claims <= 25:
            open_pts += 1
        elif claims > 100:
            open_pts -= 6
            flags.append(f"claim-saturated: ~{claims} claim comments")
        else:
            flags.append(f"contested: ~{claims} claim comments")
    elif item.get("comments_count"):
        cc = item["comments_count"]
        if cc > 100:
            open_pts -= 3
            flags.append(f"very busy thread: {cc} comments (claim split unknown)")
    detail["openness"] = max(0, min(20, open_pts))

    # --- maintainer activity (0-15)
    m_pts = 0
    if maint_days is not None:
        m_pts += 9 if maint_days <= 90 else 6 if maint_days <= 180 else 3 if maint_days <= 365 else 0
    if upd_days is not None:
        m_pts += 6 if upd_days <= 90 else 4 if upd_days <= 180 else 2 if upd_days <= 365 else 0
    if maint_days is None and upd_days is None:
        m_pts = 5  # no data (e.g. Opire listing) - neutral
        flags.append("no activity data available")
    detail["maintainer"] = min(15, m_pts)

    # --- rail liveness (0-20)
    rail = item.get("rail")
    if rail == "opire-live":
        r_pts = 20
    elif enr.get("rail_mentions_in_comments"):
        r_pts = 15
        flags.append(f"rail named in comments: {', '.join(enr['rail_mentions_in_comments'])}")
    elif item["source"].startswith("Superteam"):
        r_pts = 14  # platform escrow, but crypto + human claim step
    elif item["source"].startswith("GitLab"):
        r_pts = 5
        flags.append("no escrow on GitLab - rail must be confirmed in-issue before any work")
    elif item["source"].startswith("Hacker News"):
        r_pts = 3
        flags.append("no escrow on HN - direct negotiation only")
    else:
        r_pts = 8  # GitHub: rail stated in issue, unverified beyond that
        flags.append("rail stated in issue only - re-verify before any work")
    if re.search(r"\b(usdt|base chain|on-chain|solana)\b", f"{item.get('title','')} {item.get('body','')}", re.IGNORECASE):
        r_pts -= 3
        flags.append("crypto-denominated pay")
    detail["rail"] = max(0, r_pts)

    # --- skill fit (0-20)
    text = f"{item.get('title','')} {item.get('body','')}".lower()
    n_skills = len(matched_skills(text))
    s_pts = 20 if n_skills >= 4 else 16 if n_skills == 3 else 12 if n_skills == 2 else 8
    detail["skill_fit"] = s_pts

    total = detail["pay"] + detail["openness"] + detail["maintainer"] + detail["rail"] + detail["skill_fit"]

    # --- tier caps: structural uncertainty a raw sum would otherwise hide.
    # Contested work (claim floods / multiple open solution PRs) can be worth
    # attempting, but it is never "credible" - the win is a lottery.
    claims_n = item.get("claim_count")
    if claims_n is None:
        claims_n = enr.get("claim_comments") or 0
    open_prs_n = len(enr.get("linked_prs_open") or [])
    if claims_n > 25 or open_prs_n >= 3:
        total = min(total, 54)
        flags.append("tier capped: contested (claim flood / multiple open PRs)")
    # No-escrow, unverifiable sources can never be "credible" on their own data.
    if item["source"].startswith("Hacker News"):
        total = min(total, 54)
        flags.append("tier capped: HN has no escrow and no verification data")
    if item["source"].startswith("GitLab") and not enr.get("rail_confirmed"):
        total = min(total, 54)
        flags.append("tier capped: payment rail unconfirmed")

    item["score"] = total
    item["score_detail"] = detail
    item["flags"] = flags
    item["tier"] = "credible" if total >= 55 else "plausible" if total >= 40 else "weak"
    return None

# ---------------------------------------------------------------- effort guess

def effort_estimate(item):
    t = f"{item.get('title','')} {item.get('body','')}".lower()
    if any(w in t for w in ["docs", "documentation", "write", "article", "content", "research", "translate", "tutorial"]):
        return "1-3 hrs"
    if any(w in t for w in ["implement", "feature", "build", "create", "research implementation", "migration"]):
        return "6-15 hrs"
    if any(w in t for w in ["spreadsheet", "excel", "sheets", "script", "automation"]):
        return "2-5 hrs"
    return "2-6 hrs"

# ---------------------------------------------------------------- seen database

def load_seen():
    if SEEN_DB.exists():
        try:
            return json.loads(SEEN_DB.read_text())
        except Exception:
            return {}
    # bootstrap from any v1/v2 run files so old items are not "new"
    seen = {}
    for f in sorted(RUNS.glob("scan-*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for it in d.get("items", []):
            u = it.get("url")
            if u:
                seen[u] = {"first_seen": d.get("scanned_at_utc"), "pay": it.get("pay"),
                           "title": it.get("title"), "verdict": it.get("verdict")}
    return seen

# ---------------------------------------------------------------- dashboard data

def _opp_note(item):
    """One-line public note for a find: the first audit flag if there is one,
    else the pay note. Public listing data only - never the submission path
    or any identity/KYC detail."""
    flags = item.get("flags") or []
    text = flags[0] if flags else (item.get("pay_note") or "")
    return " ".join(str(text).split())[:180]

def _opp_entry(item):
    return {
        "title": item.get("title"),
        "platform": item.get("source"),
        "amount": item.get("pay"),
        "url": item.get("url"),
        "score": item.get("score"),
        "tier": item.get("tier"),
        "is_new": bool(item.get("is_new")),
        "note": _opp_note(item),
    }

def write_dashboard_data(stats, shortlist, stamp):
    """Write docs/data/latest.json + docs/data/opportunities.json for the
    public Pages dashboard. opportunities.json lists the credible + watchlist
    (plausible) finds; latest.json carries summary counts/timestamp plus the
    NEW credible finds (the workflow opens one issue per entry)."""
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    opps = [_opp_entry(i) for i in shortlist if i.get("tier") in ("credible", "plausible")]
    new_cred = []
    for i in shortlist:
        if i.get("tier") == "credible" and i.get("is_new"):
            e = _opp_entry(i)
            e["effort"] = i.get("effort")
            new_cred.append(e)
    latest = {
        "scanner": "v2 (github-actions)",
        "scanned_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stamp": stamp,
        "counts": {
            "raw_total": sum(s.get("raw", 0) for s in stats.values()),
            "passed": len(shortlist),
            "credible": sum(1 for i in shortlist if i.get("tier") == "credible"),
            "plausible": sum(1 for i in shortlist if i.get("tier") == "plausible"),
            "new": sum(1 for i in shortlist if i.get("is_new")),
            "new_credible": len(new_cred),
            "opportunities_listed": len(opps),
        },
        "sources": {name: {"raw": s.get("raw", 0), "passed": s.get("passed", 0),
                           "credible": s.get("credible", 0)}
                    for name, s in stats.items()},
        "new_credible": new_cred,
    }
    (DOCS_DATA / "latest.json").write_text(json.dumps(latest, indent=1, ensure_ascii=False))
    (DOCS_DATA / "opportunities.json").write_text(json.dumps(opps, indent=1, ensure_ascii=False))

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source-limit", type=int, default=60)
    ap.add_argument("--enrich-limit", type=int, default=14)
    ap.add_argument("--no-enrich", action="store_true")
    args = ap.parse_args()

    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    seen = load_seen()
    all_items, stats = [], {}

    for name, fn in SOURCES:
        print(f"[scan] {name} ...", file=sys.stderr)
        try:
            raw = fn(args.per_source_limit)
        except Exception as e:
            print(f"  [scan] {name} ERROR: {e}", file=sys.stderr)
            raw, stats[name] = [], {"raw": 0, "passed": 0, "credible": 0, "error": str(e)}
            continue
        passed = []
        for it in raw:
            it["is_new"] = it.get("url") not in seen
            if it.get("url") in BLOCKLIST:
                it["verdict"] = f"rejected: blocklisted - {BLOCKLIST[it['url']]}"
                continue
            forced = it.pop("force_reject", None)
            if forced:
                it["verdict"] = f"rejected: {forced}"
                continue
            ok, reason = passes_filters(it)
            it["verdict"] = reason
            if ok:
                it["effort"] = effort_estimate(it)
                passed.append(it)
        stats[name] = {"raw": len(raw), "passed": len(passed), "credible": 0}
        all_items.extend(raw)
        print(f"  [scan] {name}: raw={len(raw)} passed={len(passed)}", file=sys.stderr)

    # Reddit appendix (never ranked, never in the main shortlist)
    print("[scan] Reddit appendix ...", file=sys.stderr)
    try:
        r_items, r_note = src_reddit(args.per_source_limit)
    except Exception as e:
        r_items, r_note = [], f"error: {e}"
    for it in r_items:
        it["is_new"] = it.get("url") not in seen
    stats["Reddit (appendix)"] = {"raw": len(r_items), "passed": 0, "credible": 0, "note": r_note}
    all_items.extend(r_items)
    print(f"  [scan] Reddit: {r_note}", file=sys.stderr)

    # Probe notes for verified non-sources (kept honest, every run)
    stats["Probed non-sources"] = {"raw": 0, "passed": 0, "credible": 0, "notes": run_probes()}

    # ---- deep enrichment on the most promising survivors
    shortlist = [i for i in all_items if i.get("verdict") == "pass" and not i.get("appendix_only")]
    shortlist.sort(key=prelim_rank, reverse=True)
    enrichable = [i for i in shortlist
                  if i["source"].startswith(("GitHub", "GitLab", "Opire"))]
    if not args.no_enrich:
        # Opire passes are few and sit on the one verified-live rail: always
        # enrich ALL of them first, then the top others by preliminary rank.
        opire_pass = [i for i in enrichable if i["source"] == "Opire"]
        others = [i for i in enrichable if i["source"] != "Opire"]
        targets = opire_pass + others[:max(0, args.enrich_limit - len(opire_pass))]
        for it in targets:
            print(f"[enrich] {it['title'][:60]}", file=sys.stderr)
            try:
                if "github.com" in (it.get("url") or ""):
                    reason = enrich_github(it)
                elif it["source"].startswith("GitLab"):
                    reason = enrich_gitlab(it)
                else:
                    reason = None
            except Exception as e:
                print(f"  [enrich] failed: {e}", file=sys.stderr)
                reason = None
            if reason:
                it["verdict"] = f"rejected (deep check): {reason}"
            time.sleep(1)

    # ---- scoring (after enrichment so scores use the deep data)
    for it in shortlist:
        if it.get("verdict") != "pass":
            continue
        reason = score_item(it)
        if reason:
            it["verdict"] = f"rejected (scoring): {reason}"
    shortlist = [i for i in shortlist if i.get("verdict") == "pass"]
    shortlist.sort(key=lambda i: (i.get("score") or 0, i.get("pay") or 0), reverse=True)
    for idx, it in enumerate(shortlist):
        st = stats.setdefault(it["source"], {"raw": 0, "passed": 0, "credible": 0})
        st["credible"] = st.get("credible", 0) + (1 if it["tier"] == "credible" else 0)
        it["link_status"] = link_ok(it["url"]) if idx < 12 else None

    # ---- update seen db
    for it in all_items:
        u = it.get("url")
        if not u:
            continue
        prev = seen.get(u, {})
        seen[u] = {"first_seen": prev.get("first_seen") or stamp,
                   "last_seen": stamp, "pay": it.get("pay"), "title": it.get("title"),
                   "score": it.get("score"), "verdict": it.get("verdict")}
    SEEN_DB.write_text(json.dumps(seen, indent=1, ensure_ascii=False))

    out_json = RUNS / f"scan-{stamp}.json"
    def clean(it):
        d = dict(it)
        d["body"] = (d.get("body") or "")[:2000]
        return d
    out_json.write_text(json.dumps(
        {"scanner": "v2", "scanned_at_utc": stamp, "stats": stats,
         "items": [clean(i) for i in all_items]},
        indent=1, ensure_ascii=False))

    new_count = sum(1 for i in shortlist if i.get("is_new"))
    cred = [i for i in shortlist if i["tier"] == "credible"]
    lines = [f"# Scanner v2 run {stamp} UTC", ""]
    for name, s in stats.items():
        extra = ""
        if "error" in s:
            extra = f" (ERROR: {s['error']})"
        elif "note" in s:
            extra = f" — {s['note']}"
        elif "notes" in s:
            extra = " — " + " | ".join(f"{k}: {v}" for k, v in s["notes"].items())
        lines.append(f"- {name}: raw {s['raw']} -> passed {s['passed']} -> credible {s.get('credible', 0)}{extra}")
    lines += ["", f"## Scored shortlist ({len(shortlist)} passed, {len(cred)} credible, {new_count} new)", ""]
    for i, it in enumerate(shortlist[:20], 1):
        new = " **NEW**" if it.get("is_new") else ""
        lines.append(f"{i}. **Score {it.get('score','?')}/100 · ${it['pay']:,.0f}** [{it['source']}] {it['title']}{new}  ")
        lines.append(f"   {it['url']}  | effort ~{it.get('effort','?')} | link: {it.get('link_status','-')}"
                     + (f" | flags: {'; '.join(it['flags'])}" if it.get("flags") else ""))
    lines += ["", "## Appendix — Reddit (high scam risk, never ranked)", ""]
    if r_items:
        for it in r_items[:10]:
            lines.append(f"- ${it.get('pay') or '?'} {it['title']} — {it['url']}")
    else:
        lines.append(f"- No usable items this run. {r_note}")
    (RUNS / "latest.md").write_text("\n".join(lines) + "\n")

    write_dashboard_data(stats, shortlist, stamp)

    print(f"[scan] wrote {out_json.name}; total raw={sum(s['raw'] for s in stats.values())} "
          f"passed={len(shortlist)} credible={len(cred)} new={new_count}")

if __name__ == "__main__":
    main()
