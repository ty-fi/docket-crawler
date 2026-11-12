# Georgia PSC docket crawler

Collects data-request responses filed on [Georgia PSC](https://psc.ga.gov) dockets, pulls out each
question and response, and publishes them as a searchable site:
**https://ty-fi.github.io/docket-crawler/**

For each configured docket it:

1. **fetch**: lists the docket's filings through the PSC search API, keeps the ones from the
   configured company whose description matches the request pattern (e.g. `STF-JKA-1`), and
   downloads their `.zip` / `.docx` attachments into `output/docket_<id>/cache/`.
2. **parse**: opens each cached file, finds the response documents (e.g. `STF-JKA-1-4.docx`),
   splits them into question and response (tables included), links each attachment to its
   request, and numbers repeat answers as supplementals. Writes `output/docket_<id>/qa.csv`.
3. **site**: builds `site/` with the explorer page plus combined `data.json`, `qa.csv` and
   `qa.sqlite` (with an FTS5 full-text index) downloads.

A GitHub Actions workflow runs all three every Monday, commits any changed CSV/manifest, and
deploys the site to GitHub Pages.

## Usage

```sh
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m docket_crawler all                 # fetch + parse + site, every docket
.venv/bin/python -m docket_crawler fetch --docket 55378
.venv/bin/python -m docket_crawler parse               # no network
.venv/bin/python -m docket_crawler site                # no network; open site/index.html via a local server:
python -m http.server -d site 8000

.venv/bin/python -m pytest
```

### Going easy on the PSC site

- One request at a time, at least 2 s apart (`--delay`), with a User-Agent that includes a contact address.
- `fetch` reads pages and downloads files **only for new filings** (or ones that failed last time).
  Filings already in the manifest were parsed when they were new; if their files aren't cached
  (e.g. in CI), `parse` keeps their rows from the committed CSV. A normal weekly run is just the
  filing list, about one request per 50 filings.
- Transient errors (429/5xx, timeouts) are retried with exponential backoff, and a `Retry-After`
  header is honored (up to 10 minutes; longer means give up on that request).
- After 3 refusals in a row (401/403, or 429 that persists through retries) the run stops
  (exit code 3). What was fetched so far is kept, and the rest is tried next run.
- At most 200 files are downloaded per run (`--max-downloads`); the rest wait for the next run.
- A failed filing is recorded with `"status": "error"` in the manifest and retried next run.

To re-parse everything after a parser change, fill the cache first (this re-downloads all files):
`python -m docket_crawler fetch --download-all && python -m docket_crawler parse`.

## Adding a docket

Add an entry to `dockets.json`:

```json
{
  "id": "55378",
  "label": "Docket 55378",
  "subtitle": "Staff Data Requests — Georgia Power Company",
  "company_filter": "Georgia Power",
  "request_prefix": "STF",
  "party_label": "Staff"
}
```

| key | meaning |
| --- | --- |
| `id` | PSC docket number |
| `label`, `subtitle` | shown on the site |
| `company_filter` | substring of the filing company's name (case-insensitive) |
| `request_prefix` | prefix of request IDs, e.g. `STF` for `STF-JKA-1-4` (default `STF`) |
| `party_label` | what the party code after the prefix means, used as a column header (default `Staff`) |
| `description_pattern` | optional regex filing descriptions must match (default `<prefix>-<party code>-`, where a party code is 2–4 letters/digits starting with a letter, e.g. `JKA`, `PCMG`, `NU1`) |

Then run `python -m docket_crawler all --docket <id>` (or trigger the workflow from the Actions tab)
and check the parse notes printed at the end for files it could not handle.

## Data

`output/docket_<id>/qa.csv` has one row per response. Usually that is one document; a document that answers several requests (`STF-LA-9-1 - 101.docx`) is split at each request-ID heading.

| column | |
| --- | --- |
| `row_id` | stable ID (hash of docket, filing, file and path in the archive) |
| `doc_id`, `party`, `set_no`, `item_no` | request ID, e.g. `STF-JKA-1-4` → `JKA`, 1, 4 |
| `response_seq`, `is_supplemental` | 1 for the first response to a request, 2+ for later (supplemental) ones, by filing date |
| `is_pd` | `yes` when the response is a **public disclosure (PD)** version: its text is redacted, and a trade-secret version was filed confidentially. Set when the file name says so (`PD STF-…`, `… PD.docx`, `… PUBLIC DISCLOSURE.docx`) or the text contains `REDACTED` |
| `has_trade_secret` | `yes` when any of this response's material has a non-public trade-secret version: the response is PD, an attachment is PD, or the text cites a `TS …` document |
| `filed_date` | ISO date of the filing |
| `document_id`, `filing_description`, `filing_url` | the PSC filing |
| `source_file`, `source_url`, `source_member` | downloaded file, its download link, and the path inside the ZIP |
| `question`, `response` | text; table rows are rendered as `cell \| cell` lines |
| `attachments` | other files in the archive for this request (`; `-separated) |
| `confidential_files` | trade-secret / confidential files in the same archive |
| `parse_warning` | e.g. the document's own ID disagrees with its file name |

`output/docket_<id>/manifest.json` lists matching filings and their attachment download links.
Downloaded files and the built site are not committed.
