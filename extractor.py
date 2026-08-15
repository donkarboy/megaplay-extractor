"""
MegaPlay M3U8 Extractor
========================
Usage:
    python extractor.py --mal-id 1735                  # all episodes
    python extractor.py --mal-id 1735 --episode 1      # single episode
    python extractor.py --mal-id 1735 --episode 1-24   # episode range
    python extractor.py --mal-id 1735 --episode 1,5,9  # specific episodes

Output format (flat keys):
    {
      "mal_id": 1535,
      "total_episodes": 37,
      "ep-1-sub-1": "https://...",
      "ep-1-sub-2": "https://...",
      "ep-1-dub-1": "https://...",
      ...
    }

Output is saved to:
    streams/<mal_id>.json              (all episodes, or auto-split parts)
    streams/<mal_id>_ep<N>.json        (when --episode is a single number)

Auto-split: if the output exceeds 20 MB, it is written as
    streams/<mal_id>_part1.json
    streams/<mal_id>_part2.json
    ...
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── constants ────────────────────────────────────────────────────────────────

BASE = "https://megaplay.buzz"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
}
STREAMS_DIR = Path("streams")
RETRY_DELAY = 2       # seconds between retries
MAX_RETRIES = 3
MAX_FILE_BYTES = 20 * 1024 * 1024   # 20 MB

# ── helpers ──────────────────────────────────────────────────────────────────

def get_bytes(url: str, retries: int = MAX_RETRIES) -> bytes:
    """Fetch raw bytes from *url* with simple retry logic."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries:
                print(f"  [warn] HTTP {exc.code} on {url!r}, retry {attempt}/{retries}…")
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise
        except Exception as exc:
            if attempt < retries:
                print(f"  [warn] {exc} on {url!r}, retry {attempt}/{retries}…")
                time.sleep(RETRY_DELAY)
            else:
                raise


def decode_sources(raw: bytes) -> dict:
    """
    MegaPlay returns either:
      • plain JSON
      • base64-encoded JSON (utf-8 or latin-1 encoded before b64)
    """
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        padded = raw.strip()
        padded += b"=" * (-len(padded) % 4)
        return json.loads(base64.b64decode(padded))
    except Exception:
        pass
    try:
        text = raw.strip().decode("latin-1")
        text += "=" * (-len(text) % 4)
        return json.loads(base64.b64decode(text))
    except Exception as exc:
        raise RuntimeError(
            f"Cannot decode getSources response: {exc}\n"
            f"Raw (first 120 bytes): {raw[:120]}"
        )


def get_file_id(mal_id: int, episode: int, typ: str) -> str:
    """Scrape the data-id attribute from the stream embed page."""
    url = f"{BASE}/stream/mal/{mal_id}/{episode}/{typ}?autostart=true"
    html = get_bytes(url)
    match = re.search(rb'data-id="(\d+)"', html)
    if not match:
        raise RuntimeError(f"data-id not found for MAL {mal_id} ep {episode} [{typ}]")
    return match.group(1).decode()


def get_sources(file_id: str) -> tuple[str, dict | None, dict | None]:
    """Return (m3u8_url, intro_dict_or_None, outro_dict_or_None)."""
    url = f"{BASE}/stream/getSources?id={file_id}&id={file_id}"
    raw = get_bytes(url)
    data = decode_sources(raw)
    m3u8 = data["sources"]["file"]
    return m3u8, data.get("intro"), data.get("outro")


def parse_variants(master_url: str) -> list[dict]:
    """
    Fetch the HLS master playlist and return a list of quality variants
    ordered from lowest to highest index (they'll become sub-1, sub-2, …).
    Each entry: {"resolution": "1920x1080", "bandwidth": "...", "url": "..."}
    """
    try:
        raw = get_bytes(master_url)
        content = raw.decode("utf-8", errors="replace")
    except Exception:
        return []

    base = master_url.rsplit("/", 1)[0] + "/"
    variants = []
    lines = content.splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = {}
        for m in re.finditer(r'([\w-]+)=(?:"([^"]*)"|([^,\s]+))', line):
            key = m.group(1).upper()
            val = m.group(2) if m.group(2) is not None else m.group(3)
            attrs[key] = val

        if i + 1 < len(lines):
            seg = lines[i + 1].strip()
            if seg and not seg.startswith("#"):
                abs_url = seg if seg.startswith("http") else base + seg
                variants.append({
                    "resolution": attrs.get("RESOLUTION", "unknown"),
                    "bandwidth":  attrs.get("BANDWIDTH", "unknown"),
                    "codecs":     attrs.get("CODECS", ""),
                    "url":        abs_url,
                })
    return variants


# ── core extraction ──────────────────────────────────────────────────────────

def extract_episode_flat(mal_id: int, episode: int) -> dict:
    """
    Extract all streams for one episode and return them as flat key-value pairs
    using the naming convention:
        ep-<episode>-<typ>-1  →  master playlist URL  (string)
        ep-<episode>-<typ>-2  →  variant 1 URL        (string)
        ep-<episode>-<typ>-3  →  variant 2 URL        (string)
        …

    Returns a dict of { "ep-N-sub-1": url, "ep-N-dub-1": url, … }
    plus a boolean "has_streams" flag (not written to JSON).
    """
    entries: dict = {}

    for typ in ("sub", "dub"):
        try:
            fid = get_file_id(mal_id, episode, typ)
            master_url, intro, outro = get_sources(fid)

            # index 1 = master playlist
            idx = 1
            entries[f"ep-{episode}-{typ}-{idx}"] = master_url
            idx += 1

            # subsequent indices = quality variants
            for v in parse_variants(master_url):
                entries[f"ep-{episode}-{typ}-{idx}"] = v["url"]
                idx += 1

            found = idx - 1
            print(f"  ✓ [{typ.upper()}] {found} URL(s) (1 master + {found - 1} variants)")

        except Exception as exc:
            print(f"  ✗ [{typ.upper()}] skipped — {exc}")

    return entries


def extract_all_episodes(mal_id: int) -> dict:
    """
    Probe episodes from 1 upward until two consecutive failures.
    Returns a flat dict of ALL ep-N-sub/dub-M keys and the episode count.
    """
    all_entries: dict = {}
    episode = 1
    consecutive_fails = 0
    found_episodes = set()

    print(f"\n[MAL {mal_id}] Scanning all episodes…")

    while True:
        print(f"\n  Episode {episode}…")
        ep_entries = extract_episode_flat(mal_id, episode)

        if ep_entries:
            all_entries.update(ep_entries)
            found_episodes.add(episode)
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            print(f"  → no streams; consecutive failures = {consecutive_fails}")
            if consecutive_fails >= 2:
                print("  → stopping scan.")
                break

        episode += 1
        time.sleep(0.5)

    return all_entries, len(found_episodes)


# ── JSON persistence ─────────────────────────────────────────────────────────

def load_existing(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def _serialise(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def save_json_with_split(base_path: Path, data: dict) -> None:
    """
    Write *data* to *base_path*.
    If the serialised size exceeds MAX_FILE_BYTES, split the episode keys
    across multiple part files:
        streams/<mal_id>_part1.json
        streams/<mal_id>_part2.json
        …
    Each part file carries its own "mal_id" and "total_episodes" header keys.
    """
    STREAMS_DIR.mkdir(parents=True, exist_ok=True)
    raw = _serialise(data)

    if len(raw.encode("utf-8")) <= MAX_FILE_BYTES:
        # fits in one file
        base_path.write_text(raw, encoding="utf-8")
        print(f"\n💾 Saved → {base_path}  ({len(raw.encode()) / 1024:.1f} KB)")
        return

    # ── need to split ────────────────────────────────────────────────────────
    print(f"\n⚠️  Output is {len(raw.encode()) / (1024*1024):.1f} MB — splitting into parts…")

    mal_id         = data.get("mal_id")
    total_episodes = data.get("total_episodes")

    # collect all ep-N-* keys, grouped by episode number
    ep_keys: dict[int, list[str]] = {}
    header_keys = {"mal_id", "total_episodes"}
    for k in data:
        if k in header_keys:
            continue
        m = re.match(r"ep-(\d+)-", k)
        if m:
            ep_num = int(m.group(1))
            ep_keys.setdefault(ep_num, []).append(k)

    # build parts: keep adding episodes until the part would exceed the limit
    stem = base_path.stem   # e.g. "1535"
    part_num = 1
    current_part: dict = {"mal_id": mal_id, "total_episodes": total_episodes}

    for ep_num in sorted(ep_keys):
        candidate = dict(current_part)
        for k in sorted(ep_keys[ep_num]):
            candidate[k] = data[k]

        if len(_serialise(candidate).encode("utf-8")) > MAX_FILE_BYTES and len(current_part) > 2:
            # flush current part before adding this episode
            part_path = STREAMS_DIR / f"{stem}_part{part_num}.json"
            part_path.write_text(_serialise(current_part), encoding="utf-8")
            size_kb = part_path.stat().st_size / 1024
            print(f"  💾 Part {part_num} → {part_path}  ({size_kb:.1f} KB)")
            part_num += 1
            current_part = {"mal_id": mal_id, "total_episodes": total_episodes}

        for k in sorted(ep_keys[ep_num]):
            current_part[k] = data[k]

    # flush the last part
    if len(current_part) > 2:
        part_path = STREAMS_DIR / f"{stem}_part{part_num}.json"
        part_path.write_text(_serialise(current_part), encoding="utf-8")
        size_kb = part_path.stat().st_size / 1024
        print(f"  💾 Part {part_num} → {part_path}  ({size_kb:.1f} KB)")

    print(f"\n✅ Split into {part_num} part file(s).")


# ── output builders ──────────────────────────────────────────────────────────

def build_single_episode_output(mal_id: int, episode: int, ep_entries: dict) -> dict:
    """Wrap a single episode's flat entries in a top-level object."""
    return {
        "mal_id":         mal_id,
        "total_episodes": 1,
        **ep_entries,
    }


def build_multi_episode_output(mal_id: int, total_eps: int, all_entries: dict) -> dict:
    """Wrap all flat entries together with header keys."""
    return {
        "mal_id":         mal_id,
        "total_episodes": total_eps,
        **all_entries,
    }


# ── argument parsing ─────────────────────────────────────────────────────────

def parse_episode_arg(raw: str) -> list[int]:
    """
    Accept:
      "5"        → [5]
      "1-12"     → [1,2,...,12]
      "1,3,7"    → [1,3,7]
      "1,5-8,10" → [1,5,6,7,8,10]
    """
    episodes = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            episodes.update(range(int(start), int(end) + 1))
        else:
            episodes.add(int(part))
    return sorted(episodes)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract M3U8 stream URLs from MegaPlay by MAL ID.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mal-id",  type=int, required=True, help="MyAnimeList ID")
    parser.add_argument(
        "--episode",
        type=str,
        default=None,
        help=(
            "Episode(s) to fetch.\n"
            "  Single  : --episode 5\n"
            "  Range   : --episode 1-12\n"
            "  List    : --episode 1,3,7\n"
            "  Mixed   : --episode 1,5-8,10\n"
            "  Omit    : fetch ALL episodes automatically"
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Override output JSON path (default: streams/<mal_id>[_ep<N>].json)",
    )
    args = parser.parse_args()

    mal_id = args.mal_id
    STREAMS_DIR.mkdir(parents=True, exist_ok=True)

    # ── single episode ───────────────────────────────────────────────────────
    if args.episode is not None:
        ep_list = parse_episode_arg(args.episode)

        if len(ep_list) == 1:
            ep = ep_list[0]
            out_path = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}_ep{ep}.json"
            print(f"\n[MAL {mal_id}] Extracting episode {ep}…")
            ep_entries = extract_episode_flat(mal_id, ep)
            output     = build_single_episode_output(mal_id, ep, ep_entries)
            save_json_with_split(out_path, output)

        else:
            # ── multiple specific episodes ───────────────────────────────────
            out_path   = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}.json"
            all_entries: dict = {}
            found_eps: set   = set()

            print(f"\n[MAL {mal_id}] Extracting {len(ep_list)} episodes: {ep_list}")
            for ep in ep_list:
                print(f"\n  Episode {ep}…")
                ep_entries = extract_episode_flat(mal_id, ep)
                if ep_entries:
                    all_entries.update(ep_entries)
                    found_eps.add(ep)
                time.sleep(0.4)

            # merge with existing file if present
            existing = load_existing(out_path)
            if existing and any(k not in {"mal_id", "total_episodes"} for k in existing):
                existing.update(all_entries)
                # recount unique episode numbers from keys
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

    # ── all episodes ─────────────────────────────────────────────────────────
    else:
        out_path   = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}.json"
        all_entries, total_eps = extract_all_episodes(mal_id)

        if not all_entries:
            print("\n[error] No streams found for any episode.")
            sys.exit(1)

        output = build_multi_episode_output(mal_id, total_eps, all_entries)
        save_json_with_split(out_path, output)

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
