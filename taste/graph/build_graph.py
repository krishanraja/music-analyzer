"""Build the taste knowledge graph from a Spotify playlist read plus trackcard measurements.

Inputs (made by the playlist fetch step, kept outside the repo because they hold audio):
  meta.json            one record per track: title, artists, duration, release date, label line, preview url
  audio/<id>.trackcard.json   trackcard 0.4 output for that track's Spotify preview clip
  families.json        (this folder) style family per artist; an inference, labelled as such in the graph

Output (this folder): graph.json (nodes + edges), tracks.csv (one row per track, flat features).

Run: python taste/graph/build_graph.py <dir with meta.json and audio/>
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
BANDS = ["sub", "low", "mid", "high-mid", "air"]
STEMS = ["drums", "bass", "vocals", "other"]
PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def camelot_parts(code: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([AB])", code or "")
    return (int(m.group(1)), m.group(2)) if m else (0, "")


def camelot_neighbours(code: str) -> list[str]:
    n, l = camelot_parts(code)
    if not n:
        return []
    up, down = n % 12 + 1, (n - 2) % 12 + 1
    return [f"{up}{l}", f"{down}{l}", f"{n}{'A' if l == 'B' else 'B'}"]


def tempo_band(bpm: float | None) -> str:
    if bpm is None:
        return "unknown"
    for lo, hi in [(0, 100), (100, 110), (110, 118), (118, 122), (122, 126), (126, 130)]:
        if lo <= bpm < hi:
            return f"{lo}-{hi - 1} BPM" if lo else "under 100 BPM"
    return "130+ BPM"


def weighted(sections, fn):
    tot = sum(s["end"] - s["start"] for s in sections) or 1
    return sum(fn(s) * (s["end"] - s["start"]) for s in sections) / tot


def features(card: dict) -> dict:
    secs = card["sections"]
    f = {
        "bpm": card.get("likely_project_bpm") or round(card["bpm"]),
        "bpm_confidence": card["bpm_confidence"],
        "key": card["key"],
        "camelot": card["camelot"],
        "mode": "major" if "major" in card["key"] else "minor",
        "key_confidence": card["key_confidence"],
        "key_alternative": card.get("key_alternative"),
        "lufs": card["loudness"].get("integrated_lufs"),
        "crest_db": card["loudness"]["crest_db"],
        "clip_seconds": round(card["duration"], 1),
    }
    for b in BANDS:
        f[f"band_{b}"] = round(weighted(secs, lambda s: s["bands_rel_db"][b]), 1)
    if card.get("stems_available"):
        for st in STEMS:
            f[f"stem_{st}"] = round(weighted(secs, lambda s: s["stems"][st]["density"]), 2)
    return f


def traits(f: dict, duration_s: int) -> list[str]:
    t = [f"{f['mode']} key"]
    loudest = max(BANDS, key=lambda b: f[f"band_{b}"])
    t.append({"sub": "sub-led low end", "low": "low-mid warmth led", "mid": "mid-forward"}.get(loudest, "bright"))
    if f["band_air"] > -14:
        t.append("airy top end")
    elif f["band_air"] < -20:
        t.append("dark top end")
    if f["crest_db"] >= 11:
        t.append("dynamic master (crest 11 dB+)")
    elif f["crest_db"] < 8.5:
        t.append("dense master (crest under 8.5 dB)")
    if "stem_vocals" in f:
        if f["stem_vocals"] >= 0.45:
            t.append("vocal or vocal-like lead present")
        elif f["stem_vocals"] < 0.1:
            t.append("instrumental in clip")
    t.append("club length (6 min+)" if duration_s >= 360 else ("radio length (under 4 min)" if duration_s < 240 else "mid length (4-6 min)"))
    return t


def main(src: Path) -> None:
    meta = json.loads((src / "meta.json").read_text())
    families = json.loads((HERE / "families.json").read_text())
    fam_of_artist = {a: fam for fam, d in families["families"].items() for a in d["artists"]}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, kind, **attrs):
        nodes.setdefault(nid, {"id": nid, "type": kind, **attrs})
        return nid

    def edge(a, rel, b, **attrs):
        edges.append({"source": a, "rel": rel, "target": b, **attrs})

    for fam, d in families["families"].items():
        node(f"family:{fam}", "style_family", name=d["name"], description=d["description"], provenance="inferred from artists (Claude's read)")

    rows = []
    for tid, r in meta.items():
        artists = [a.strip() for a in r["artists"].split(",")]
        year = (r.get("release_date") or "")[:4] or None
        label = None
        if r.get("label_line"):
            label = re.sub(r"^℗ ?\d{4} ", "", r["label_line"]).strip().replace("&#x27;", "'")
        tnode = node(f"track:{tid}", "track", title=r["title"], artists=artists, duration_s=r["duration_s"],
                     release_year=int(year) if year else None, label=label,
                     spotify=f"https://open.spotify.com/track/{tid}", playlist="Chillout & Ambient")
        for a in artists:
            edge(tnode, "by", node(f"artist:{a}", "artist", name=a))
            if a in fam_of_artist:
                edge(f"artist:{a}", "works_in", f"family:{fam_of_artist[a]}", provenance="inferred")
        fams = sorted({fam_of_artist[a] for a in artists if a in fam_of_artist})
        for fam in fams:
            edge(tnode, "in_family", f"family:{fam}", provenance="inferred")
        if label:
            edge(tnode, "released_on", node(f"label:{label}", "label", name=label))
        if year:
            era = f"{year[:3]}0s" if int(year) < 2010 else year
            edge(tnode, "from_era", node(f"era:{era}", "era", name=era))

        card_path = src / "audio" / f"{tid}.trackcard.json"
        row = {"id": tid, "title": r["title"], "artists": "; ".join(artists), "duration_s": r["duration_s"],
               "release_year": year, "label": label, "family": "; ".join(fams), "measured": False}
        if card_path.exists():
            f = features(json.loads(card_path.read_text()))
            nodes[tnode]["measured"] = f
            nodes[tnode]["measured_from"] = f"Spotify preview clip ({f['clip_seconds']} s) via trackcard"
            row.update(f, measured=True)
            edge(tnode, "in_key", node(f"key:{f['camelot']}", "key", name=f["key"], camelot=f["camelot"]), confidence=f["key_confidence"])
            edge(tnode, "at_tempo", node(f"tempo:{tempo_band(f['bpm'])}", "tempo_band", name=tempo_band(f["bpm"])), bpm=f["bpm"], confidence=f["bpm_confidence"])
            for t in traits(f, r["duration_s"]):
                edge(tnode, "has_trait", node(f"trait:{t}", "trait", name=t, provenance="measured"))
        else:
            row["note"] = "no Spotify preview, not measured"
        rows.append(row)

    # Camelot compatibility between keys present in the graph
    keys = [n for n in nodes if n.startswith("key:")]
    for k in keys:
        for nb in camelot_neighbours(nodes[k]["camelot"]):
            if f"key:{nb}" in nodes and k < f"key:{nb}":
                edge(k, "mixes_with", f"key:{nb}", basis="Camelot neighbour")

    # Sound-alike edges: 3 nearest measured tracks on standardised features
    measured = [r for r in rows if r["measured"]]
    def vec(r):
        n, l = camelot_parts(r["camelot"])
        ang = 2 * math.pi * (n - 1) / 12
        v = [r["bpm"], 1.0 if r["mode"] == "major" else 0.0, math.cos(ang), math.sin(ang), r["crest_db"], r["lufs"] or -12]
        v += [r[f"band_{b}"] for b in BANDS]
        v += [r.get(f"stem_{s}", 0.0) for s in STEMS]
        return v
    X = [vec(r) for r in measured]
    cols = list(zip(*X))
    mu = [sum(c) / len(c) for c in cols]
    sd = [math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) or 1 for c, m in zip(cols, mu)]
    Z = [[(x - m) / s for x, m, s in zip(v, mu, sd)] for v in X]
    for i, r in enumerate(measured):
        d = sorted((math.dist(Z[i], Z[j]), j) for j in range(len(Z)) if j != i)[:3]
        for dist, j in d:
            a, b = f"track:{r['id']}", f"track:{measured[j]['id']}"
            if a < b or not any(e["rel"] == "sounds_like" and e["source"] == b and e["target"] == a for e in edges):
                edge(a, "sounds_like", b, distance=round(dist, 2), basis="nearest on measured tempo, key, bands, dynamics, stems")

    graph = {
        "about": "Krish's taste graph. Tracks from his Spotify playlist 'Chillout & Ambient' (99 tracks, read 2026-11-13). "
                 "Release year and label come from Spotify pages; tempo, key, frequency balance, dynamics and stems are measured by trackcard "
                 "on each track's Spotify preview clip (15-30 s), so they describe a slice, not the whole track. Style families are inferred.",
        "counts": dict(Counter(n["type"] for n in nodes.values())) | {"edges": len(edges)},
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    (HERE / "graph.json").write_text(json.dumps(graph, indent=1, ensure_ascii=False))
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("id", "title", "artists"), k))
    with open(HERE / "tracks.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(graph["counts"]))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
