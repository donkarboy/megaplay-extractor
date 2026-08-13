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
import sys
import time
from pathlib import Path

# reuse everything from extractor.py
from extractor import (
    STREAMS_DIR,
    extract_episode,
    extract_all_episodes,
    build_episode_entry,
    build_full_output,
    load_existing,
    save_json,
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
            # all episodes
            out_path = STREAMS_DIR / f"{mal_id}.json"
            all_eps  = extract_all_episodes(mal_id)
            if all_eps:
                save_json(out_path, build_full_output(mal_id, all_eps))

        else:
            ep_list = parse_episode_arg(str(episode))

            if len(ep_list) == 1:
                ep       = ep_list[0]
                out_path = STREAMS_DIR / f"{mal_id}_ep{ep}.json"
                print(f"\n[MAL {mal_id}] Extracting episode {ep}…")
                data     = extract_episode(mal_id, ep)
                save_json(out_path, build_episode_entry(data))

            else:
                out_path  = STREAMS_DIR / f"{mal_id}.json"
                extracted = []
                for ep in ep_list:
                    print(f"\n  Episode {ep}…")
                    d = extract_episode(mal_id, ep)
                    if any(k.startswith("stream_") for k in d):
                        extracted.append(d)
                    time.sleep(0.4)

                existing = load_existing(out_path)
                if existing and "episodes" in existing:
                    for ep_data in extracted:
                        existing["episodes"][str(ep_data["episode"])] = ep_data
                    existing["total_episodes"] = len(existing["episodes"])
                    save_json(out_path, existing)
                else:
                    save_json(out_path, build_full_output(mal_id, extracted))

        print()
        time.sleep(1)   # pause between MAL IDs

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
