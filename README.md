# MegaPlay M3U8 Extractor

Pure-Python CLI tool that extracts HLS (`.m3u8`) stream URLs from [MegaPlay](https://megaplay.buzz) using a MyAnimeList (MAL) ID and saves them as structured JSON files.

No external dependencies — stdlib only (`urllib`, `json`, `re`, `base64`, `argparse`).

---

## Quick start

```bash
git clone https://github.com/YOUR_USER/megaplay-extractor
cd megaplay-extractor

# single episode
python extractor.py --mal-id 1735 --episode 1

# episode range
python extractor.py --mal-id 1735 --episode 1-12

# comma list / mixed
python extractor.py --mal-id 1735 --episode 1,5,9
python extractor.py --mal-id 1735 --episode 1,5-8,10

# ALL episodes (auto-scans until two consecutive failures)
python extractor.py --mal-id 1735
```

Output is written to the `streams/` folder inside the repo.

---

## Output file naming

| Input | Output path |
|---|---|
| `--mal-id 1735 --episode 5` | `streams/1735_ep5.json` |
| `--mal-id 1735 --episode 1-12` | `streams/1735.json` |
| `--mal-id 1735` (all) | `streams/1735.json` |

Use `--output path/to/file.json` to override.

---

## JSON structure

### Single episode (`streams/1735_ep5.json`)

```json
{
  "mal_id": 1735,
  "episode": 5,
  "stream_sub_1": {
    "label": "SUB – Master Playlist",
    "url": "https://…/master.m3u8",
    "type": "hls",
    "quality": "master",
    "intro": { "start": 90, "end": 180 },
    "outro": { "start": 1320, "end": 1410 }
  },
  "stream_sub_2": {
    "label": "SUB – 1920x1080",
    "url": "https://…/1080p.m3u8",
    "type": "hls",
    "quality": "1920x1080",
    "bandwidth": "4000000",
    "codecs": "avc1.640028,mp4a.40.2",
    "intro": { "start": 90, "end": 180 },
    "outro": { "start": 1320, "end": 1410 }
  },
  "stream_sub_3": {
    "label": "SUB – 1280x720",
    "url": "https://…/720p.m3u8",
    "type": "hls",
    "quality": "1280x720",
    ...
  },
  "stream_dub_4": {
    "label": "DUB – Master Playlist",
    ...
  }
}
```

### Multiple / all episodes (`streams/1735.json`)

```json
{
  "mal_id": 1735,
  "total_episodes": 24,
  "episodes": {
    "1": {
      "mal_id": 1735,
      "episode": 1,
      "stream_sub_1": { ... },
      "stream_sub_2": { ... },
      "stream_dub_3": { ... }
    },
    "2": { ... }
  }
}
```

---

## Batch extraction

Edit `batch_config.json`:

```json
[
  { "mal_id": 1735, "episode": "1-5" },
  { "mal_id": 145,  "episode": "1" },
  { "mal_id": 5114 }
]
```

Run:

```bash
python batch.py                        # uses batch_config.json
python batch.py --config my_list.json
```

---

## GitHub Actions (run in the cloud)

A ready-to-use workflow is at `.github/workflows/extract.yml`.

### Manual trigger

1. Go to **Actions → Extract M3U8 Streams → Run workflow**
2. Enter the MAL ID and optionally an episode string
3. The workflow commits the resulting JSON back to `streams/`

### Automatic scheduled refresh

Uncomment the `schedule:` block in the workflow YAML to run every day at 03:00 UTC.

---

## Repository layout

```
megaplay-extractor/
├── extractor.py          ← main CLI script
├── batch.py              ← batch runner
├── batch_config.json     ← batch input (edit freely)
├── streams/              ← output JSONs committed here
│   ├── 1735.json
│   └── 1735_ep5.json
├── .github/
│   └── workflows/
│       └── extract.yml
├── .gitignore
└── README.md
```

---

## Notes

- Streams are rate-limited: a short sleep is added between episodes automatically.
- If a SUB or DUB track is unavailable for an episode it is silently skipped.
- Stream keys (`stream_sub_1`, `stream_dub_4`, …) are numbered **globally per episode** so they are always unique within one episode object.
- Re-running with the same MAL ID merges new episodes into an existing `streams/<mal_id>.json` rather than overwriting it.
