"""
Scan a folder for .wav files and write their metadata to a JSON file.
Requires: pip install mutagen

Usage:
    python wav_meta.py <folder> [--output metadata.json] [--recursive]
"""

import argparse
import json
import wave
from pathlib import Path

try:
    from mutagen.wave import WAVE
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("Warning: mutagen not installed. Tag fields (title, artist, genre, etc.) will be missing.")
    print("Install with: pip install mutagen")


# Maps mutagen ID3 frame IDs → friendly JSON key names.
ID3_FIELDS = {
    "TIT2": "title",
    "TPE1": "artist",
    "TPE2": "contributing_artist",
    "TPE3": "conductor",
    "TCOM": "composer",
    "TALB": "album",
    "TCON": "genre",
    "TRCK": "track_number",
    "TPOS": "disc_number",
    "TDRC": "year",
    "TPUB": "publisher",
    "TCOP": "copyright",
    "TENC": "encoded_by",
    "COMM::eng": "comment",
    "COMM::": "comment",
}


def _tags_from_mutagen(path: Path) -> dict:
    tags = {}
    try:
        wf = WAVE(str(path))
        if wf.tags:
            for frame_id, key in ID3_FIELDS.items():
                if frame_id in wf.tags:
                    tags[key] = str(wf.tags[frame_id])
            # Catch any COMM frame regardless of language suffix.
            if "comment" not in tags:
                for frame_id in wf.tags:
                    if frame_id.startswith("COMM"):
                        tags["comment"] = str(wf.tags[frame_id])
                        break
    except Exception:
        pass
    return tags


def wav_metadata(path: Path) -> dict:
    result = {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "error": None,
    }

    # Audio properties via stdlib wave module.
    try:
        with wave.open(str(path), "rb") as wf:
            frame_rate = wf.getframerate()
            n_frames = wf.getnframes()
            result.update({
                "channels": wf.getnchannels(),
                "sample_rate_hz": frame_rate,
                "bit_depth": wf.getsampwidth() * 8,
                "frames": n_frames,
                "duration_seconds": round(n_frames / frame_rate, 6) if frame_rate else 0,
            })
    except Exception as exc:
        result["error"] = str(exc)

    # Tag metadata via mutagen.
    if MUTAGEN_AVAILABLE:
        result.update(_tags_from_mutagen(path))

    return result


def main():
    parser = argparse.ArgumentParser(description="Extract metadata from all .wav files in a folder.")
    parser.add_argument("folder", help="Path to the folder to scan")
    parser.add_argument("--output", default="metadata.json", help="Output JSON file (default: metadata.json)")
    parser.add_argument("--recursive", action="store_true", help="Recurse into subdirectories")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")

    glob = root.rglob("*.wav") if args.recursive else root.glob("*.wav")
    files = sorted(glob)

    if not files:
        print(f"No .wav files found in {root}")
        return

    results = [wav_metadata(f) for f in files]

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote metadata for {len(results)} file(s) to {output_path}")


if __name__ == "__main__":
    main()
