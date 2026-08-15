"""
Batch M3U8 Extractor
====================
Reads batch_config.json and extracts streams for every entry.

batch_config.json format:
  [
    { "mal_id": 1735 },
    { "mal_id": 145,  "episode": "1-24" },
    { "mal_id": 5114, "episode": "1,5,10" }
  ]

Usage:
    python batch.py                       # uses batch_config.json
    python batch.py --config my_list.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from extractor import (
    STREAMS_DIR,
    extract_episode_flat,
    extract_all_episodes,
    build_single_episode_output,
    build_multi_episode_output,
    load_existing,
    save_json_with_split,
    parse_episode_arg,
)


def run_batch(config_path: Path) -> None:
    if not config_path.exists():
        print(f"[error] Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    print(f"Batch: {len(entries)} entries from {config_path}\n")

    for idx, entry in enumerate(entries, 1):
        mal_id  = int(entry["mal_id"])
        episode = entry.get("episode", None)

        print(f"━━━ [{idx}/{len(entries)}] MAL {mal_id}  episode={episode or 'ALL'} ━━━")

        if episode is None:
            # ── all episodes ─────────────────────────────────────────────────
            out_path = STREAMS_DIR / f"{mal_id}.json"
            all_entries, total_eps = extract_all_episodes(mal_id)
            if all_entries:
                output = build_multi_episode_output(mal_id, total_eps, all_entries)
                save_json_with_split(out_path, output)

        else:
            ep_list = parse_episode_arg(str(episode))

            if len(ep_list) == 1:
                # ── single episode ───────────────────────────────────────────
                ep       = ep_list[0]
                out_path = STREAMS_DIR / f"{mal_id}_ep{ep}.json"
                print(f"\n[MAL {mal_id}] Extracting episode {ep}…")
                ep_entries = extract_episode_flat(mal_id, ep)
                output     = build_single_episode_output(mal_id, ep, ep_entries)
                save_json_with_split(out_path, output)

            else:
                # ── multiple specific episodes ───────────────────────────────
                out_path    = STREAMS_DIR / f"{mal_id}.json"
                all_entries: dict = {}
                found_eps: set   = set()

                for ep in ep_list:
                    print(f"\n  Episode {ep}…")
                    ep_entries = extract_episode_flat(mal_id, ep)
                    if ep_entries:
                        all_entries.update(ep_entries)
                        found_eps.add(ep)
                    time.sleep(0.4)

                existing = load_existing(out_path)
                if existing and any(k not in {"mal_id", "total_episodes"} for k in existing):
                    existing.update(all_entries)
                    all_ep_nums = {
                        int(re.match(r"ep-(\d+)-", k).group(1))
                        for k in existing
                        if re.match(r"ep-(\d+)-", k)
                    }
                    existing["total_episodes"] = len(all_ep_nums)
                    save_json_with_split(out_path, existing)
                else:
                    output = build_multi_episode_output(mal_id, len(found_eps), all_entries)
                    save_json_with_split(out_path, output)

        print()
        time.sleep(1)

    print("✅ Batch complete.")


def main():
    parser = argparse.ArgumentParser(description="Batch M3U8 extraction from a JSON config.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("batch_config.json"),
        help="Path to batch config JSON (default: batch_config.json)",
    )
    args = parser.parse_args()
    run_batch(args.config)


if __name__ == "__main__":
    main()
