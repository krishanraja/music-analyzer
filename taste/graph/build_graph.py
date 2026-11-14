"""Build the taste knowledge graph from the Spotify playlist read plus trackcard measurements of the full tracks.

Inputs:
  playlist.json        (this folder) one record per track from the Spotify Web API: id, title, artists, duration_s,
                       release_date, label (+ label_source), added_at. Made by reading the playlist with Krish's login.
  <cards>/<id>.trackcard.json        trackcard output for the FULL track (no stems, Laplacian sections), kept outside the
                                     repo next to the audio. The audio was length-matched to Spotify's within 6 s.
  <cards>/<id>.stems.trackcard.json  optional: the same track measured with Demucs stems; adds drops and stem densities.
  <cards>/fetch_log.jsonl            optional: where each full track came from (SoundCloud or YouTube upload).
  --previews <dir>     optional, labelled fallback only: <dir>/<id>.trackcard.json measured on a 15-30 s preview clip.
  families.json        (this folder) style family per artist; an inference, labelled as such in the graph.

Output (this folder): graph.json (nodes + edges), tracks.csv (one row per track, flat features).

Run: python taste/graph/build_graph.py <cards dir> [--previews <dir>]
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
BANDS = ["sub", "low", "mid", "high-mid", "air"]
STEMS = ["drums", "bass", "vocals", "other"]
LENGTH_TOLERANCE_S = 6.5  # the fetch accepted uploads within 6 s; cards round duration to 0.1 s


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


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


def quarter(added_at: str) -> str:
    return f"{added_at[:4]}-Q{(int(added_at[5:7]) - 1) // 3 + 1}"


def clean_label(label: str | None) -> str | None:
    """Spotify's phonographic line is printed text: drop leading years ("2019, 1983 Warner ..." -> "Warner ...")."""
    if not label:
        return None
    label = re.sub(r"^℗\s*", "", label)
    label = re.sub(r"^(\d{4}\s*,?\s*)+", "", label).strip().replace("&#x27;", "'")
    return label or None


def weighted(sections, fn):
    tot = sum(s["end"] - s["start"] for s in sections) or 1
    return sum(fn(s) * (s["end"] - s["start"]) for s in sections) / tot


def features(card: dict, spotify_s: float) -> dict:
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
    # Arrangement, meaningful only on the whole track: the energy arc across sections
    energies = [s["energy"] for s in secs]
    peaks = [s for s in secs if s["energy"] == "peak"]
    f["full_track"] = abs(card["duration"] - spotify_s) <= LENGTH_TOLERANCE_S
    if f["full_track"]:
        f["sections"] = len(secs)
        f["intro_bars_before_first_peak"] = (peaks[0]["start_bar"] - 1) if peaks else None
        f["first_peak_s"] = round(peaks[0]["start"]) if peaks else None
        f["peak_share"] = round(sum(s["end"] - s["start"] for s in peaks) / card["duration"], 2)
        f["breakdowns"] = sum(1 for a, b in zip(energies, energies[1:]) if a == "peak" and b != "peak")
        f["energy_arc"] = " > ".join(e[0].upper() for e in energies)  # P peak, M mid, L low
    f["stems"] = bool(card.get("stems_available"))
    return f


def stem_features(card: dict) -> dict:
    """Drops and stem densities from a stems card. Drops are only detected when stems ran."""
    f = {"drops": len(card.get("drops") or [])}
    for st in STEMS:
        f[f"stem_{st}"] = round(weighted(card["sections"], lambda s: s["stems"][st]["density"]), 2)
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
            t.append("instrumental")
    t.append("club length (6 min+)" if duration_s >= 360 else ("radio length (under 4 min)" if duration_s < 240 else "mid length (4-6 min)"))
    return t


def fetch_sources(cards: Path) -> dict[str, dict]:
    log = cards / "fetch_log.jsonl"
    last: dict[str, dict] = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            last[r["id"]] = r  # the last line per id decides; a rejected upload is logged with ok false
    return {i: r for i, r in last.items() if r.get("ok")}


class _FamilyLookup(dict):
    """Artist -> family, matched case-insensitively ('Sons Of Maria' and 'Sons of Maria' are one artist)."""

    def __init__(self, by_lower: dict[str, str]):
        super().__init__()
        self.by_lower = by_lower

    def __contains__(self, artist) -> bool:
        return artist.lower() in self.by_lower

    def __getitem__(self, artist: str) -> str:
        return self.by_lower[artist.lower()]


def main(cards: Path, previews: Path | None = None) -> dict:
    pl = read_json(HERE / "playlist.json")
    families = read_json(HERE / "families.json")
    fam_by_name = {a.lower(): fam for fam, d in families["families"].items() for a in d["artists"]}
    fam_of_artist = _FamilyLookup(fam_by_name)
    sources = fetch_sources(cards)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, kind, **attrs):
        nodes.setdefault(nid, {"id": nid, "type": kind, **attrs})
        return nid

    def edge(a, rel, b, **attrs):
        edges.append({"source": a, "rel": rel, "target": b, **attrs})

    for fam, d in families["families"].items():
        node(f"family:{fam}", "style_family", name=d["name"], description=d["description"],
             provenance="inferred from artists (Claude's read)")

    rows = []
    for r in pl["tracks"]:
        tid, artists = r["id"], r["artists"]
        year = (r.get("release_date") or "")[:4] or None
        label = clean_label(r.get("label"))
        added = r["added_at"]
        tnode = node(f"track:{tid}", "track", title=r["title"], artists=artists, duration_s=r["duration_s"],
                     release_date=r.get("release_date"), release_year=int(year) if year else None,
                     label=label, label_source=r.get("label_source"),
                     added_at=added, added_source="Spotify Web API (playlist items)",
                     spotify=f"https://open.spotify.com/track/{tid}", playlist="Chillout & Ambient")
        for a in artists:
            edge(tnode, "by", node(f"artist:{a}", "artist", name=a))
            if a in fam_of_artist:
                edge(f"artist:{a}", "works_in", f"family:{fam_of_artist[a]}", provenance="inferred")
        fams = sorted({fam_of_artist[a] for a in artists if a in fam_of_artist})
        # Primary family: the first credited artist's, else the first other artist's that has one
        primary = next((fam_of_artist[a] for a in artists if a in fam_of_artist), None)
        for fam in fams:
            edge(tnode, "in_family", f"family:{fam}", provenance="inferred")
        if label:
            edge(tnode, "released_on", node(f"label:{label}", "label", name=label))
        if year:
            era = f"{year[:3]}0s" if int(year) < 2010 else year
            edge(tnode, "from_era", node(f"era:{era}", "era", name=era))
        # When Krish added it (Spotify's added_at): month and year nodes
        y, m = added[:4], added[:7]
        ynode = node(f"added_year:{y}", "added_year", name=y)
        mnode = node(f"added_month:{m}", "added_month", name=m)
        if not any(e["rel"] == "in_year" and e["source"] == mnode for e in edges):
            edge(mnode, "in_year", ynode)
        edge(tnode, "added_in", mnode, added_at=added)
        edge(tnode, "added_in", ynode, added_at=added)

        row = {"id": tid, "title": r["title"], "artists": "; ".join(artists), "duration_s": r["duration_s"],
               "release_date": r.get("release_date"), "release_year": year, "label": label,
               "added_at": added, "added_quarter": quarter(added), "added_year": y,
               "family": "; ".join(fams), "primary_family": primary, "measured": False}
        card_path = cards / f"{tid}.trackcard.json"
        fallback = None
        if not card_path.exists() and previews is not None and (previews / f"{tid}.trackcard.json").exists():
            card_path, fallback = previews / f"{tid}.trackcard.json", "preview"
        if card_path.exists():
            f = features(read_json(card_path), r["duration_s"])
            stems_card = cards / f"{tid}.stems.trackcard.json"
            if stems_card.exists():
                f.update(stem_features(read_json(stems_card)))
            src = sources.get(tid, {})
            if f["full_track"]:
                where = {"soundcloud": "SoundCloud", "youtube": "YouTube"}.get(src.get("source"), "an upload")
                measured_from = (f"full track from {where} (length within 6 s of Spotify's) via trackcard, no stems"
                                 + ("; drops and stem densities from a second trackcard run with Demucs stems" if stems_card.exists() else ""))
            else:
                measured_from = (f"Spotify preview clip ({f['clip_seconds']} s) via trackcard, a slice not the whole track"
                                 if fallback == "preview" else
                                 f"audio of {f['clip_seconds']} s, not Spotify's length ({r['duration_s']} s): not the whole track")
            if src.get("note"):
                measured_from += f"; {src['note']}"
            nodes[tnode]["measured"] = f
            nodes[tnode]["measured_from"] = measured_from
            if src:
                nodes[tnode]["audio_source"] = {k: v for k, v in {
                    "site": src.get("source"), "url": src.get("url"), "upload_title": src.get("upload_title"),
                    "uploader": src.get("uploader"), "length_diff_s": src.get("diff_s"), "via": src.get("via"),
                    "note": src.get("note")}.items() if v is not None}
            row.update(f, measured=True, measured_from=("full" if f["full_track"] else (fallback or "partial")),
                       audio_source=src.get("source"))
            edge(tnode, "in_key", node(f"key:{f['camelot']}", "key", name=f["key"], camelot=f["camelot"]), confidence=f["key_confidence"])
            edge(tnode, "at_tempo", node(f"tempo:{tempo_band(f['bpm'])}", "tempo_band", name=tempo_band(f["bpm"])), bpm=f["bpm"], confidence=f["bpm_confidence"])
            for t in traits(f, r["duration_s"]):
                edge(tnode, "has_trait", node(f"trait:{t}", "trait", name=t, provenance="measured"))
        else:
            row["note"] = "no full-track audio found, not measured"
            row["measured_from"] = "none"
        rows.append(row)

    # Camelot compatibility between keys present in the graph
    keys = [n for n in nodes if n.startswith("key:")]
    for k in keys:
        for nb in camelot_neighbours(nodes[k]["camelot"]):
            if f"key:{nb}" in nodes and k < f"key:{nb}":
                edge(k, "mixes_with", f"key:{nb}", basis="Camelot neighbour")

    # Sound-alike edges: 3 nearest measured tracks on standardised features. Stem densities join the vector only if
    # every measured track has them, so tracks with and without stems are compared on the same footing.
    measured = [r for r in rows if r["measured"]]
    use_stems = bool(measured) and all("stem_vocals" in r for r in measured)

    def vec(r):
        n, _ = camelot_parts(r["camelot"])
        ang = 2 * math.pi * (n - 1) / 12
        v = [r["bpm"], 1.0 if r["mode"] == "major" else 0.0, math.cos(ang), math.sin(ang), r["crest_db"], r["lufs"] or -12]
        v += [r[f"band_{b}"] for b in BANDS]
        if use_stems:
            v += [r[f"stem_{s}"] for s in STEMS]
        return v

    basis = "nearest on measured tempo, key, mode, bands, dynamics" + (", stems" if use_stems else "")
    if len(measured) > 1:
        X = [vec(r) for r in measured]
        cols = list(zip(*X))
        mu = [sum(c) / len(c) for c in cols]
        sd = [math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) or 1 for c, m in zip(cols, mu)]
        Z = [[(x - m) / s for x, m, s in zip(v, mu, sd)] for v in X]
        seen = set()
        for i, r in enumerate(measured):
            for dist, j in sorted((math.dist(Z[i], Z[j]), j) for j in range(len(Z)) if j != i)[:3]:
                a, b = sorted((f"track:{r['id']}", f"track:{measured[j]['id']}"))
                if (a, b) not in seen:
                    seen.add((a, b))
                    edge(a, "sounds_like", b, distance=round(dist, 2), basis=basis)

    n_full = sum(1 for r in rows if r.get("measured_from") == "full")
    n_prev = sum(1 for r in rows if r.get("measured_from") == "preview")
    graph = {
        "about": f"Krish's taste graph. All {len(rows)} tracks of his Spotify playlist 'Chillout & Ambient' (read "
                 f"{pl['read_at'][:10]} from the Spotify Web API, with the date each was added). Release dates and added "
                 "dates are from the Spotify Web API; labels from Spotify's album pages (the phonographic copyright line). "
                 f"Tempo, key, frequency balance, dynamics and arrangement are measured by trackcard on the full track for "
                 f"{n_full} tracks" + (f" and on a preview clip for {n_prev}" if n_prev else "") + "; each track's "
                 "measured_from says which. Style families are inferred (Claude's read).",
        "counts": dict(Counter(n["type"] for n in nodes.values())) | {"edges": len(edges)},
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    (HERE / "graph.json").write_text(json.dumps(graph, indent=1, ensure_ascii=False), encoding="utf-8")
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("id", "title", "artists"), k))
    with open(HERE / "tracks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(graph["counts"]))
    return graph


if __name__ == "__main__":
    args = sys.argv[1:]
    prev = None
    if "--previews" in args:
        i = args.index("--previews")
        prev = Path(args[i + 1])
        del args[i:i + 2]
    main(Path(args[0]), prev)
