# Opportunity Radar — Third Shift Works

**The studio's public opportunity radar.** Third Shift Works is an AI-operated
studio (human owner) that takes on small, well-defined jobs — websites,
spreadsheets & automation, music visualizers, and website front-desk chat.
This repository is how the studio watches for work: a read-only scanner that
sweeps public job and bounty boards every night, scores what it finds, and
publishes the shortlist.

**Live dashboard:** https://thirdshiftworks.github.io/opportunity-scanner/
**Studio site:** https://thirdshiftworks.github.io/

## How the nightly robot works

1. A **GitHub Actions** workflow (`.github/workflows/nightly-scan.yml`) wakes
   up on a schedule — `9 7 * * *` UTC, roughly 2 AM US Central — and can also
   be run by hand (`workflow_dispatch`).
2. It runs **`scan.py`** (Python, standard library only — nothing to install)
   on GitHub's free runners. The scanner reads only *public, no-login*
   sources: Opire, GitHub and GitLab bounty-labelled issues, Superteam Earn,
   and Hacker News freelancer threads.
3. Every listing passes hard filters (stated pay, work the studio can do,
   no stake/entry fees, no prompt-harvesting traps, no dead payment rails,
   no already-assigned or already-solved items), then deep verification
   (comments, timelines, linked PRs, maintainer activity, the repo's own
   contribution policy) and a **0–100 credibility score** with structural
   tier caps. A human-audited **blocklist** (`blocklist.json`, 13 listings)
   can never resurface.
4. The scanner remembers what it has seen in **`seen.json`** at the repo
   root, so only genuinely **new** finds are ever news. The workflow commits
   the updated `seen.json` and dashboard data back to `main` as the
   `github-actions[bot]`.
5. Results land in **`docs/data/`**: `latest.json` (summary counts,
   timestamp, and the run's new credible finds) and `opportunities.json`
   (the current credible + watchlist finds). The dashboard in `docs/` renders
   them via GitHub Pages.
6. For each **new credible** find, the workflow opens one issue in this
   repo (labelled `new`, duplicates guarded by title) so a human can audit
   it — repo rules, payment rail, fit — before anything is claimed.
7. When an audit kills a find, the listing goes into **`blocklist.json`**
   (URL → why it died) so it can never resurface, and the check that let it
   through is added to the scanner's filters so the next one is caught
   automatically.

The scanner only ever *reads*. It never bids, comments, claims, opens PRs on
other projects, sends messages, or spends anything. Acting on a find is a
separate, human-approved step — the radar just makes sure nothing worth
looking at slips by overnight.

## Honest framing

The dashboard lists **public listings the studio is evaluating** — not
clients, not contracted work, and not earnings. Scores are a ranking aid,
not a verdict; every top find gets a manual read before the studio acts.

## Files

| Path | What it is |
|---|---|
| `scan.py` | The scanner (stdlib-only Python) |
| `tests/test_scan.py` | Unit tests for the filters (`python3 -m unittest discover -s tests`) |
| `seen.json` | Seen-database: every listing the scanner has already surfaced |
| `blocklist.json` | Audit-killed listings that must never resurface |
| `.github/workflows/nightly-scan.yml` | The nightly robot worker |
| `docs/index.html` | The public dashboard (GitHub Pages, served from `/docs`) |
| `docs/data/latest.json` | Latest run summary + new credible finds |
| `docs/data/opportunities.json` | Current credible + watchlist finds |
| `PUBLISH-STEPS.md` | How this repo was published (one-time setup record) |
