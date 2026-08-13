"""
MegaPlay M3U8 Extractor
========================
Usage:
    python extractor.py --mal-id 1735                  # all episodes
    python extractor.py --mal-id 1735 --episode 1      # single episode
    python extractor.py --mal-id 1735 --episode 1-24   # episode range
    python extractor.py --mal-id 1735 --episode 1,5,9  # specific episodes

Output is saved to:
    streams/<mal_id>.json              (single or all)
    streams/<mal_id>_ep<N>.json        (when --episode is a single number)
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
RETRY_DELAY = 2   # seconds between retries
MAX_RETRIES = 3

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
    Try all three variants.
    """
    # 1. plain JSON
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 2. base64 (utf-8 byte string)
    try:
        padded = raw.strip()
        padded += b"=" * (-len(padded) % 4)
        return json.loads(base64.b64decode(padded))
    except Exception:
        pass
    # 3. base64 of latin-1 decoded string
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
    Fetch the HLS master playlist and return a list of quality variants:
      [{"resolution": "1920x1080", "bandwidth": "...", "url": "..."}, ...]
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

def extract_episode(mal_id: int, episode: int) -> dict:
    """
    Extract all available streams (sub + dub, master + variants) for one
    episode.  Returns a dict shaped for JSON serialisation.
    """
    result: dict = {
        "mal_id":  mal_id,
        "episode": episode,
    }

    stream_index = 1  # global counter across sub/dub so keys stay unique

    for typ in ("sub", "dub"):
        try:
            fid              = get_file_id(mal_id, episode, typ)
            master_url, intro, outro = get_sources(fid)

            # master stream
            key = f"stream_{typ}_{stream_index}"
            result[key] = {
                "label":   f"{typ.upper()} – Master Playlist",
                "url":     master_url,
                "type":    "hls",
                "quality": "master",
                "intro":   intro,
                "outro":   outro,
            }
            stream_index += 1

            # quality variants
            for v in parse_variants(master_url):
                key = f"stream_{typ}_{stream_index}"
                result[key] = {
                    "label":     f"{typ.upper()} – {v['resolution']}",
                    "url":       v["url"],
                    "type":      "hls",
                    "quality":   v["resolution"],
                    "bandwidth": v["bandwidth"],
                    "codecs":    v["codecs"],
                    "intro":     intro,
                    "outro":     outro,
                }
                stream_index += 1

            print(
                f"  ✓ [{typ.upper()}] {stream_index - 1} stream(s) found "
                f"(master + {stream_index - 2} variants)"
            )

        except Exception as exc:
            print(f"  ✗ [{typ.upper()}] skipped — {exc}")

    return result


def extract_all_episodes(mal_id: int) -> list[dict]:
    """
    Probe episodes starting from 1 until two consecutive failures,
    then return the list of successfully extracted episode dicts.
    """
    results = []
    episode = 1
    consecutive_fails = 0

    print(f"\n[MAL {mal_id}] Scanning all episodes…")

    while True:
        print(f"\n  Episode {episode}…")
        data = extract_episode(mal_id, episode)

        # check if any stream was found
        has_streams = any(k.startswith("stream_") for k in data)
        if has_streams:
            results.append(data)
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            print(f"  → no streams; consecutive failures = {consecutive_fails}")
            if consecutive_fails >= 2:
                print("  → stopping scan.")
                break

        episode += 1
        time.sleep(0.5)   # be polite

    return results


# ── JSON persistence ─────────────────────────────────────────────────────────

def load_existing(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def save_json(path: Path, data) -> None:
    STREAMS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Saved → {path}")


# ── output formatters ────────────────────────────────────────────────────────

def build_episode_entry(ep_data: dict) -> dict:
    """
    Flatten one episode extraction into the canonical JSON structure:
      {
        "mal_id": ...,
        "episode": ...,
        "stream_sub_1": {...},
        "stream_dub_1": {...},
        ...
      }
    (already in this shape — just return it.)
    """
    return ep_data


def build_full_output(mal_id: int, episodes: list[dict]) -> dict:
    """
    Wrap multiple episodes under a single JSON object:
      {
        "mal_id": 1735,
        "total_episodes": 3,
        "episodes": { "1": {...}, "2": {...} }
      }
    """
    return {
        "mal_id":         mal_id,
        "total_episodes": len(episodes),
        "episodes": {
            str(ep["episode"]): ep
            for ep in episodes
        }
    }


# ── argument parsing ─────────────────────────────────────────────────────────

def parse_episode_arg(raw: str) -> list[int]:
    """
    Accept:
      "5"       → [5]
      "1-12"    → [1,2,...,12]
      "1,3,7"   → [1,3,7]
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

    mal_id   = args.mal_id
    STREAMS_DIR.mkdir(parents=True, exist_ok=True)

    # ── single or specific episodes ──────────────────────────────────────────
    if args.episode is not None:
        ep_list = parse_episode_arg(args.episode)

        if len(ep_list) == 1:
            # single episode → flat file
            ep = ep_list[0]
            out_path = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}_ep{ep}.json"
            print(f"\n[MAL {mal_id}] Extracting episode {ep}…")
            data = extract_episode(mal_id, ep)
            save_json(out_path, build_episode_entry(data))

        else:
            # multiple episodes → combined file
            out_path = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}.json"
            print(f"\n[MAL {mal_id}] Extracting {len(ep_list)} episodes: {ep_list}")
            extracted = []
            for ep in ep_list:
                print(f"\n  Episode {ep}…")
                d = extract_episode(mal_id, ep)
                if any(k.startswith("stream_") for k in d):
                    extracted.append(d)
                time.sleep(0.4)

            existing = load_existing(out_path)
            # merge into existing if file already present
            if existing and "episodes" in existing:
                for ep_data in extracted:
                    existing["episodes"][str(ep_data["episode"])] = ep_data
                existing["total_episodes"] = len(existing["episodes"])
                save_json(out_path, existing)
            else:
                save_json(out_path, build_full_output(mal_id, extracted))

    # ── all episodes ─────────────────────────────────────────────────────────
    else:
        out_path = Path(args.output) if args.output else STREAMS_DIR / f"{mal_id}.json"
        all_eps  = extract_all_episodes(mal_id)
        if not all_eps:
            print("\n[error] No streams found for any episode.")
            sys.exit(1)
        save_json(out_path, build_full_output(mal_id, all_eps))

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
