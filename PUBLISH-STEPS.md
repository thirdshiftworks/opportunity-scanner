# Publish steps — opportunity-scanner repo (one-time, browser)

Everything here is done in the GitHub web UI, signed in as **thirdshiftworks**.
All content was prepared and verified locally first; this step only uploads
it and flips the switches. $0 cost throughout — do not buy anything, do not
add a payment method, do not enable any paid feature.

## 1. Create the repo

- New repository: owner **thirdshiftworks**, name **`opportunity-scanner`**
- Visibility: **Public** (required for free Actions minutes + free Pages)
- Do **not** add a README, .gitignore, or license in the create form — the
  upload below brings its own.

## 2. Upload these files (exact repo paths)

Upload via "uploading an existing file" (or the web editor), preserving
paths exactly:

| Local file | Repo path |
|---|---|
| `scan.py` | `scan.py` |
| `seen.json` | `seen.json` |
| `blocklist.json` | `blocklist.json` |
| `README.md` | `README.md` |
| `PUBLISH-STEPS.md` | `PUBLISH-STEPS.md` |
| `.gitignore` | `.gitignore` |
| `.github/workflows/nightly-scan.yml` | `.github/workflows/nightly-scan.yml` |
| `docs/index.html` | `docs/index.html` |
| `docs/data/latest.json` | `docs/data/latest.json` |
| `docs/data/opportunities.json` | `docs/data/opportunities.json` |

Note: the GitHub web uploader cannot create the `.github/workflows/` path in
one drag in every browser — if it balks, create `.github/workflows/nightly-scan.yml`
with "Create new file" and paste the workflow content instead. The workflow
file must land at exactly that path or Actions will never see it.

## 3. Enable GitHub Pages

- Repo **Settings → Pages**
- Build and deployment → Source: **Deploy from a branch**
- Branch: **`main`**, folder: **`/docs`** → Save
- Success: the settings page shows "Your site is live at
  **https://thirdshiftworks.github.io/opportunity-scanner/**" (first build
  can take a minute or two).

## 4. Enable Actions (if prompted)

- Open the **Actions** tab. If GitHub shows "Workflows aren't being run on
  this repository", click **"I understand my workflows, go ahead and enable
  them."**
- The workflow "Nightly opportunity scan" should appear in the left list.

## 5. Run the workflow once by hand

- Actions tab → **Nightly opportunity scan** → **Run workflow** → branch
  `main` → **Run workflow**.
- The run takes a few minutes (the scanner is deliberately polite to the
  public APIs it reads).

## 6. What success looks like

- ✅ The workflow run ends **green** (a red run means the scanner failed
  loudly on purpose — read its log, do not just re-run).
- ✅ A new commit on `main` from **github-actions[bot]**:
  "chore(scan): nightly update …" touching `seen.json` and `docs/data/`.
- ✅ The dashboard loads at
  https://thirdshiftworks.github.io/opportunity-scanner/ and shows a real
  last-scan time, listing counts, and the opportunities table (or an honest
  "nothing on the radar" state).
- ✅ If the run found a **new credible** opportunity, one issue labelled
  `new` exists for it, titled `[Opportunity] <title> — $<amount>`. Zero new
  credible finds → zero issues is also success.
- From here it runs itself nightly (~2 AM US Central) — no further setup.
