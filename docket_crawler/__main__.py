"""Command line: python -m docket_crawler {fetch,parse,site,all} [--docket ID]

  fetch  list filings on the PSC site, download files for new filings, update manifest.json
  parse  parse cached attachments into output/docket_<id>/qa.csv (no network)
  site   build site/ (index.html, data.json, qa.csv, qa.sqlite) from the CSVs (no network)
  all    fetch, parse, then site
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from .build import build_docket, write_csv
from .config import load_dockets, select_dockets
from .fetch import fetch_docket
from .psc_client import PSCClient, SiteRefusingError
from .site import build_site


def _report(title: str, problems: List[str], limit: int = 40) -> None:
    if not problems:
        return
    print(f"\n{title} ({len(problems)}):")
    for p in problems[:limit]:
        print(f"  - {p}")
    if len(problems) > limit:
        print(f"  ... and {len(problems) - limit} more")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{title}: {len(problems)} (see log)")


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="docket_crawler", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["fetch", "parse", "site", "all"])
    parser.add_argument("--docket", help="only this docket id (default: all in dockets.json)")
    parser.add_argument("--refresh-pages", action="store_true",
                        help="re-read every filing page, not just new ones")
    parser.add_argument("--download-all", action="store_true",
                        help="download every missing file, not just new filings' (for a full re-parse)")
    parser.add_argument("--max-downloads", type=int, default=200,
                        help="stop downloading after this many files per run (default 200)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="minimum seconds between requests to the PSC site (default 2)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any fetch error occurred")
    args = parser.parse_args(argv)

    all_dockets = load_dockets()
    dockets = select_dockets(all_dockets, args.docket)
    fetch_errors: List[str] = []
    refused = False

    if args.command in ("fetch", "all"):
        client = PSCClient(min_interval=args.delay)
        remaining = args.max_downloads
        for d in dockets:
            try:
                before = client.downloads
                fetch_errors += fetch_docket(d, client, refresh_pages=args.refresh_pages,
                                             download_all=args.download_all,
                                             max_downloads=remaining)
                remaining -= client.downloads - before
            except SiteRefusingError as e:
                print(f"\nSTOPPED: {e}")
                if os.environ.get("GITHUB_ACTIONS"):
                    print(f"::error::PSC site refused requests: {e}")
                refused = True
                break
        print(f"\n{client.requests_made} requests made to the PSC site this run")
        _report("Fetch errors", fetch_errors)

    if args.command in ("parse", "all"):
        for d in dockets:
            rows, problems = build_docket(d)
            write_csv(d.csv_path, rows)
            print(f"  Wrote {d.csv_path}")
            _report(f"Docket {d.id} parse notes", problems)

    if args.command in ("site", "all"):
        print("\n== Building site")
        build_site(all_dockets)

    if refused:
        return 3
    return 1 if (args.strict and fetch_errors) else 0


if __name__ == "__main__":
    sys.exit(main())
