#!/usr/bin/env python3
"""Build mafiascope_demo.srt from a recording's marks.json.

One subtitle block per segment; text = the shortened voiceover (CAPS) captured
during recording; timings = segment [start, end] shifted by the trim point so
they line up with the trimmed mp4. See docs/screencast_reshoot.md.

Usage: build_srt.py <marks.json> <out.srt>
"""
import json
import sys


def ts(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    marks_path, out_path = sys.argv[1], sys.argv[2]
    m = json.load(open(marks_path, encoding="utf-8"))
    trim = m["trim"]
    caps = m["caps"]
    blocks = []
    n = 0
    for seg in m["segments"]:
        i = seg["i"]
        start = seg["start"] - trim
        end = seg["end"] - trim
        text = caps[i - 1]
        if not text.strip():
            continue  # silent segment (e.g. the closing URL card): no cue
        n += 1
        blocks.append(f"{n}\n{ts(start)} --> {ts(end)}\n{text}\n")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
    print(f"wrote {out_path} ({len(blocks)} cues, ends {ts(m['segments'][-1]['end']-trim)})")


if __name__ == "__main__":
    main()
