"""
Catalog Batch Extractor
=======================
Fetches MAL IDs from the remote anisnatch catalog JSON and extracts
MegaPlay streams for a user-specified range of serial numbers.

The catalog URL:
  https://raw.githubusercontent.com/donkarboy/anisantch_top/refs/heads/main/anime-page-only-url-scraper.json

Output file: streams/megaplay_stream.json
  (auto-split into megaplay_stream_part1.json, part2.json … if > 20 MB)

Usage (interactive — asks for serial range):
    python catalog_batch.py

Usage (non-interactive — pass range on CLI):
    python catalog_batch.py --serial 1-100
    python catalog_batch.py --serial 45-60
    python catalog_batch.py --serial 1,5,10-20

Episode source:
  • If the catalog entry has "total_ep", ALL episodes are fetched (1 … total_ep).
  • If only "total_ep_aired" is available, aired episodes are fetched instead.
  • Single-episode entries (total_ep == 1) use episode 1.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from extractor import (
    STREAMS_DIR,
    extract_episode_flat,
    build_multi_episode_output,
    load_existing,
    save_json_with_split,
    parse_episode_arg,
)

# ── constants ────────────────────────────────────────────────────────────────

CATALOG_URL = (
    "https://raw.githubusercontent.com/donkarboy/anisantch_top"
    "/refs/heads/main/anime-page-only-url-scraper.json"
)
OUTPUT_STEM  = "megaplay_stream"          # streams/megaplay_stream[_partN].json
INTER_DELAY  = 0.4   # seconds between episodes
ANIME_DELAY  = 1.0   # seconds between anime titles


# ── catalog helpers ──────────────────────────────────────────────────────────

def fetch_catalog() -> list[dict]:
    """Download and parse the anisnatch catalog JSON."""
    print(f"[catalog] Fetching catalog from:\n  {CATALOG_URL}\n")
    try:
        with urllib.request.urlopen(CATALOG_URL, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"[error] Could not fetch catalog: {exc}")
        sys.exit(1)
    print(f"[catalog] Loaded {len(data)} entries.\n")
    return data


def parse_serial_arg(raw: str) -> list[int]:
    """
    Parse a serial-number range string into a sorted list of integers.
      "5"        → [5]
      "1-100"    → [1, 2, …, 100]
      "1,3,7"    → [1, 3, 7]
      "1,5-8,10" → [1, 5, 6, 7, 8, 10]
    """
    serials: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            serials.update(range(int(start.strip()), int(end.strip()) + 1))
        else:
            serials.add(int(part))
    return sorted(serials)


def ask_serial_range(total: int) -> list[int]:
    """
    Interactively prompt the user for a serial range.
    Keeps asking until valid input is received.
    """
    print(f"The catalog has {total} entries (serial_no 1 – {total}).")
    print("Enter the serial range you want to extract.")
    print("  Examples:  1-100   |   45-60   |   1,5,10-20   |   all\n")

    while True:
        raw = input("Serial range: ").strip()
        if not raw:
            print("  [!] Please enter a range (e.g. 1-50).")
            continue
        if raw.lower() == "all":
            return list(range(1, total + 1))
        try:
            result = parse_serial_arg(raw)
            if not result:
                raise ValueError("empty")
            # validate bounds
            out_of_range = [s for s in result if s < 1 or s > total]
            if out_of_range:
                print(f"  [!] Serial numbers out of range (1–{total}): {out_of_range[:5]}…")
                continue
            return result
        except (ValueError, IndexError):
            print(f"  [!] Invalid format. Try something like  1-100  or  45,50-60.")


def episodes_for_entry(entry: dict) -> list[int]:
    """
    Determine which episode numbers to fetch for a catalog entry.
    Priority:
      1. total_ep (complete count)
      2. total_ep_aired (only aired so far)
      3. Fallback: episode 1
    """
    total = entry.get("total_ep") or entry.get("total_ep_aired")
    if not total or total < 1:
        return [1]
    return list(range(1, int(total) + 1))


# ── main extraction logic ────────────────────────────────────────────────────

def run_catalog_batch(serial_list: list[int], catalog: list[dict]) -> None:
    """
    Extract streams for every catalog entry whose serial_no is in serial_list.
    All results are merged into streams/megaplay_stream[_partN].json.
    """
    # build lookup: serial_no → entry
    by_serial: dict[int, dict] = {e["serial_no"]: e for e in catalog}

    # filter + warn about missing serials
    entries_to_process = []
    missing = []
    for s in serial_list:
        if s in by_serial:
            entries_to_process.append(by_serial[s])
        else:
            missing.append(s)

    if missing:
        print(f"[warn] {len(missing)} serial number(s) not found in catalog: "
              f"{missing[:10]}{'…' if len(missing) > 10 else ''}")

    if not entries_to_process:
        print("[error] No valid entries to process.")
        sys.exit(1)

    print(f"\nProcessing {len(entries_to_process)} anime title(s).\n")

    # determine output path
    STREAMS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STREAMS_DIR / f"{OUTPUT_STEM}.json"

    # load any previously saved data so we can merge
    master: dict = load_existing(out_path) or {}

    total_anime_processed = 0

    for idx, entry in enumerate(entries_to_process, 1):
        mal_id     = int(entry["anime_id"])
        anime_name = entry.get("anime_name", f"MAL {mal_id}")
        serial_no  = entry["serial_no"]
        ep_list    = episodes_for_entry(entry)

        print(f"━━━ [{idx}/{len(entries_to_process)}] "
              f"Serial #{serial_no} | MAL {mal_id} | {anime_name}")
        print(f"     Episodes to fetch: {ep_list[0]}–{ep_list[-1]} "
              f"({len(ep_list)} ep{'s' if len(ep_list) != 1 else ''})")

        all_entries: dict = {}
        found_eps: set   = set()

        for ep in ep_list:
            print(f"\n  Episode {ep}…")
            ep_entries = extract_episode_flat(mal_id, ep)
            if ep_entries:
                all_entries.update(ep_entries)
                found_eps.add(ep)
            time.sleep(INTER_DELAY)

        if all_entries:
            master.update(all_entries)
            total_anime_processed += 1
            print(f"\n  ✓ {len(found_eps)} episode(s) extracted for MAL {mal_id}.")
        else:
            print(f"\n  ✗ No streams found for MAL {mal_id} ({anime_name}).")

        print()
        time.sleep(ANIME_DELAY)

    # compute total unique episode-level entries
    anime_ids_found = {
        int(re.match(r"ep-(\d+)-", k).group(1))   # these are episode numbers per anime
        for k in master
        if re.match(r"ep-(\d+)-", k)
    } if master else set()

    # build final output  (recount unique mal_ids represented)
    output = {
        "source":           "megaplay",
        "catalog_url":      CATALOG_URL,
        "total_anime":      total_anime_processed,
        **master,
    }

    print(f"Saving combined output → {out_path}")
    save_json_with_split(out_path, output, stem_override=OUTPUT_STEM)
    print("\n✅ Catalog batch complete.")


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract MegaPlay streams from the anisnatch catalog.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help=(
            "Serial number range to extract (optional — omit for interactive prompt).\n"
            "  Single  : --serial 5\n"
            "  Range   : --serial 1-100\n"
            "  List    : --serial 1,3,7\n"
            "  Mixed   : --serial 1,5-8,10\n"
            "  All     : --serial all"
        ),
    )
    args = parser.parse_args()

    catalog = fetch_catalog()

    if args.serial:
        if args.serial.lower() == "all":
            serial_list = list(range(1, len(catalog) + 1))
        else:
            serial_list = parse_serial_arg(args.serial)
    else:
        serial_list = ask_serial_range(len(catalog))

    run_catalog_batch(serial_list, catalog)


if __name__ == "__main__":
    main()
