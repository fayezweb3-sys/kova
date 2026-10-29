# Kova

Drug supply and coverage for Ontario and BC pharmacies. Kova joins five official public sources on the DIN and answers, in one search:

- **Can I get it?** Health Canada marketing status, open shortage and discontinuation reports, recalls.
- **Is it covered?** Ontario Drug Benefit status, price, Limited Use; BC PharmaCare plans, maximum price, LCA and Special Authority.
- **What can I give instead?** Interchangeable, covered products with no open shortage report, cheapest first. Temporary listings and other release forms are shown separately, never as straight swaps.
- **What changed?** Every refresh is compared with the last one. Watchlists get email or webhook alerts for shortages, recalls, coverage changes, new generics and status changes.

## The website

Kova runs as a static website on GitHub Pages: no server, no hosting bill. Every night a GitHub Actions workflow
(`.github/workflows/site.yml`) downloads all five sources, compares them with the night before, and rebuilds the site:

- `/` is the Kova home page (`website/index.html`)
- `/app/` is the drug search: product pages, Ontario and BC alternatives, watchlists (saved in the browser) and recent changes
- `/app/data/` holds the JSON the app reads, which doubles as a read-only API

One-time setup: push this folder to a GitHub repository, then Settings → Pages → Source: **GitHub Actions**, then
Actions → "Refresh data and publish site" → Run workflow. For shortage reports, add the secrets `KOVA_HPSC_EMAIL` and
`KOVA_HPSC_PASSWORD` (Settings → Secrets and variables → Actions).

## Run it locally (optional)

```bash
cd kova-app
bash setup.sh                     # creates .venv, installs, runs the tests
source .venv/bin/activate

python -m kova sample             # optional: try it first on built-in sample data
python -m kova serve --db data/sample.sqlite

python -m kova refresh            # download all sources and load them (needs internet)
python -m kova verify             # check the Ontario rules against the real e-Formulary
python -m kova serve              # http://127.0.0.1:8000
```

Shortage reports need a free Health Product Shortages Canada account (healthproductshortages.ca). Put the login in your environment before `refresh`:

```bash
export KOVA_HPSC_EMAIL="you@example.com"
export KOVA_HPSC_PASSWORD="..."
```

Without it, everything else still loads and the shortage columns stay empty.

## Commands

| Command | What it does |
|---|---|
| `kova fetch [--only odb bc]` | Download sources into `data/raw/` |
| `kova load` | Load what's in `data/raw/` and record changes |
| `kova refresh` | fetch + load + send watchlist alerts; run nightly |
| `kova serve` | Web app and API |
| `kova lookup carbamazepine 200` | Search or product page in the terminal |
| `kova alternatives ON 00773611` | What to dispense instead (ON or BC) |
| `kova watch add "Front shelf" dins.csv --email rx@pharmacy.ca` | Watchlist from any file containing DINs |
| `kova verify` | Compare derived ODB fields with values read from the e-Formulary |
| `kova status` | What's loaded and when |

Nightly refresh on a Mac: `crontab -e`, then
`15 2 * * * cd /path/to/kova-app && .venv/bin/python -m kova refresh >> data/refresh.log 2>&1`

## Sources

| Source | File | Schedule |
|---|---|---|
| Health Canada Drug Product Database | 4 zipped extracts (marketed, approved, cancelled, dormant) | Regularly |
| Ontario Drug Benefit Formulary | Monthly XML data extract (Edition 43 schema) | Monthly |
| BC PharmaCare | `pddf.zip` (weekly, Tuesdays) + LCA and RDP spreadsheets (first Thursday monthly) | Weekly / monthly |
| Health Product Shortages Canada | API v1, account required, 1,000 requests an hour | Continuous |
| Health Canada Recalls and Safety Alerts | Open data JSON (Open Government Licence – Canada) | Daily |

Download links for the ODB extract and the LCA/RDP files change monthly; Kova finds them on the official pages.

## API

`GET /v1/search?q=`, `/v1/products/{din}`, `/v1/coverage/{ON|BC}/{din}`, `/v1/alternatives/{ON|BC}/{din}`, `/v1/shortages`, `/v1/events`, and `/v1/watchlists` (create, read, update, delete, events). Interactive docs at `/docs`. Set `KOVA_API_KEYS=key1,key2` to require an `X-API-Key` header. Webhooks are signed: `X-Kova-Signature: sha256=<HMAC of the body with the watchlist secret>`.

Alert email uses SMTP when `KOVA_SMTP_HOST` (and `KOVA_SMTP_PORT`, `KOVA_SMTP_USER`, `KOVA_SMTP_PASSWORD`, `KOVA_MAIL_FROM`) are set; otherwise messages are saved to `data/outbox/`.

## Rules worth knowing

- **Ontario interchangeability:** a product is treated as interchangeable when its ODB group (pcg9) holds more than one listed product. This matches the e-Formulary for every carbamazepine listing checked on 28 Oct 2026. `kova verify` re-checks it on each new extract; if it reports differences, the rule needs work before pharmacies rely on it.
- **BC alternatives** use the LCA category when a product has one, otherwise Health Canada's same ingredients, strength, form and route.
- **Ontario temporary benefits** are the listings marked `sec3c="Y"` in the extract (usually PINs for products brought in during a shortage).
- **Recalls** are linked to a DIN when the notice prints it; matches by product name are labelled as such.

## Not built yet

The 90-day shortage forecast (planned for February 2027), BC PharmaCare temporary shortage coverage notices, and wholesaler stock.

## Tests

`python -m pytest -q`. Sample data: the Ontario carbamazepine listings are real e-Formulary rows (28 Oct 2026); BC coverage, shortage reports and recalls in the sample are invented and labelled as such in the app.
