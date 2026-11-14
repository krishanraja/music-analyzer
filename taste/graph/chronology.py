"""How Krish's taste moved: per-period figures from tracks.csv by the date each track was added (Spotify's added_at).

Periods are calendar years of adding. Quarters are too thin (most hold under 10 tracks), so a year with fewer than
MIN_N measured tracks is merged with the next year (or the previous one, at the end). Every figure comes from trackcard
on the full track (rows whose measured_from is "full"); style families are inferred (families.json).

Writes (this folder): chronology.json, chronology.png, and the numbers block of CHRONOLOGY.md between
<!-- numbers:start --> and <!-- numbers:end --> (the prose around it is written by hand).

Run after build_graph.py: python taste/graph/chronology.py
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
MIN_N = 10
ARR = ["intro_bars_before_first_peak", "peak_share", "breakdowns", "sections"]


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def iqr(xs):
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 4:
        return None
    q = st.quantiles(xs, n=4)
    return [round(q[0], 1), round(q[2], 1)]


def periods(rows: list[dict]) -> list[tuple[str, list[str]]]:
    years = sorted({r["added_year"] for r in rows})
    n_meas = Counter(r["added_year"] for r in rows if r["measured_from"] == "full")
    out, cur = [], []
    for y in years:
        cur.append(y)
        if sum(n_meas[x] for x in cur) >= MIN_N:
            out.append(cur)
            cur = []
    if cur:
        if out:
            out[-1] += cur
        else:
            out.append(cur)
    return [(p[0] if len(p) == 1 else f"{p[0]}-{p[-1][2:]}", p) for p in out]


def summarise(rows: list[dict], fam_names: dict[str, str]) -> dict:
    m = [r for r in rows if r["measured_from"] == "full"]
    keys = Counter(f"{r['camelot']} {r['key']}" for r in m)
    fams = Counter(r["primary_family"] or "unassigned" for r in rows)
    ages = [int(r["added_year"]) - int(r["release_year"]) for r in rows if r["release_year"]]
    return {
        "added": len(rows),
        "measured_full": len(m),
        "bpm_median": med([num(r["bpm"]) for r in m]),
        "bpm_iqr": iqr([num(r["bpm"]) for r in m]),
        "major_share": round(sum(r["mode"] == "major" for r in m) / len(m), 2) if m else None,
        "top_keys": keys.most_common(3),
        "family_share": {k: round(v / len(rows), 2) for k, v in fams.most_common()},
        "air_db_median": med([num(r["band_air"]) for r in m]),
        "high_mid_db_median": med([num(r["band_high-mid"]) for r in m]),
        "crest_db_median": med([num(r["crest_db"]) for r in m]),
        "lufs_median": med([num(r["lufs"]) for r in m]),
        "length_s_median": med([num(r["duration_s"]) for r in rows]),
        "release_year_median": med([num(r["release_year"]) for r in rows]),
        "age_at_add_median": med(ages),
        **{f"{a}_median": med([num(r.get(a)) for r in m]) for a in ARR},
    }


def markers(rows: list[dict], prev: dict | None, cur: dict) -> dict:
    """The family whose share grew most against the previous period, and its first tracks added in this period."""
    if not prev:
        fam = max(cur["family_share"], key=cur["family_share"].get)
        gain = None
    else:
        gains = {f: cur["family_share"].get(f, 0) - prev["family_share"].get(f, 0) for f in cur["family_share"]}
        fam = max(gains, key=gains.get)
        gain = round(gains[fam], 2)
    picks = sorted((r for r in rows if (r["primary_family"] or "unassigned") == fam), key=lambda r: r["added_at"])[:3]
    return {"family": fam, "share_gain": gain,
            "tracks": [{"id": r["id"], "artists": r["artists"], "title": r["title"], "added": r["added_at"][:10],
                        "bpm": r.get("bpm") or None, "key": r.get("key") or None} for r in picks]}


def references(playlist_ids: dict[str, str]) -> list[dict]:
    path = HERE.parent / "references.json"
    if not path.exists():
        return []
    out = []
    for r in json.loads(path.read_text(encoding="utf-8"))["references"]:
        m = re.search(r"track/([A-Za-z0-9]+)", r.get("url", ""))
        tid = m.group(1) if m else None
        out.append({"date": r["date"], "shared_by": r["shared_by"], "artist": r["artist"], "title": r["title"],
                    "year": r.get("year"), "in_playlist_added": playlist_ids.get(tid)})
    return out


def chart(rows: list[dict], pers: list[dict], fam_names: dict[str, str], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#8a8984"]
    m = [r for r in rows if r["measured_from"] == "full" and r.get("bpm")]
    when = lambda r: datetime.fromisoformat(r["added_at"].replace("Z", "+00:00"))
    mid = lambda p: datetime(int(p["years"][0]), 1, 1) + (datetime(int(p["years"][-1]) + 1, 1, 1) - datetime(int(p["years"][0]), 1, 1)) / 2

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "axes.titlecolor": INK, "axes.titlesize": 10,
                         "axes.titleweight": "bold", "axes.titlelocation": "left"})
    fig, axes = plt.subplots(3, 1, figsize=(10, 10.5), sharex=True, facecolor=SURF,
                             gridspec_kw={"height_ratios": [1.2, 0.9, 1.1], "hspace": 0.32})
    for ax in axes:
        ax.set_facecolor(SURF)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

    ax = axes[0]
    ax.scatter([when(r) for r in m], [float(r["bpm"]) for r in m], s=12, color="#b5b4ae", edgecolor="none",
               label="one track (full-track BPM)")
    xs, ys = [mid(p) for p in pers], [p["bpm_median"] for p in pers]
    ax.plot(xs, ys, color=SERIES[0], linewidth=2, marker="o", markersize=7, markeredgecolor=SURF, markeredgewidth=2,
            label="median for the year of adding")
    ax.set_ylim(60, 170)
    ax.set_ylabel("BPM")
    ax.set_title("Tempo of each track, by the date Krish added it")
    ax.legend(loc="upper left", frameon=False, fontsize=8)

    ax = axes[1]
    ax.plot(xs, [p["major_share"] * 100 for p in pers], color=SERIES[0], linewidth=2, marker="o", markersize=7,
            markeredgecolor=SURF, markeredgewidth=2)
    for x, p in zip(xs, pers):
        ax.annotate(f"{round(p['major_share'] * 100)}%\nn={p['measured_full']}", (x, p["major_share"] * 100),
                    textcoords="offset points", xytext=(0, 9), ha="center", fontsize=7.5, color=INK2)
    ax.set_ylim(0, 100)
    ax.set_ylabel("% major key")
    ax.set_title("Share of tracks in a major key (full-track measured)")

    ax = axes[2]
    fams = [f for f in fam_names if any(p["family_share"].get(f) for p in pers)]
    if any(p["family_share"].get("unassigned") for p in pers):
        fams.append("unassigned")
    width = [(datetime(int(p["years"][-1]) + 1, 1, 1) - datetime(int(p["years"][0]), 1, 1)).days * 0.85 for p in pers]
    bottom = [0.0] * len(pers)
    for i, f in enumerate(fams[:9]):
        vals = [p["family_share"].get(f, 0) * 100 for p in pers]
        ax.bar(xs, vals, width=width, bottom=bottom, color=SERIES[i] if f != "unassigned" else "#d9d8d3",
               edgecolor=SURF, linewidth=2, label=fam_names.get(f, "no family assigned"))
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of tracks added")
    ax.set_title("Style family of the tracks added (inferred, Claude's read)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False, fontsize=8)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.text(0.01, 0.005, "Source: added dates from the Spotify Web API; tempo and key measured by trackcard on full tracks; "
             "families inferred. Thin years merged so each point has 10+ measured tracks.", fontsize=7, color=INK2)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURF)
    plt.close(fig)


def table(pers: list[dict], fam_names: dict[str, str]) -> str:
    def f(v, fmt="{:g}"):
        return "-" if v is None else fmt.format(v)
    L = ["| added in | tracks (measured) | median BPM (middle half) | major | top keys | air dB | crest dB | "
         "intro bars | at full energy | breakdowns | length | released (median) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in pers:
        keys = ", ".join(f"{k.split(' ', 1)[0]} ({n})" for k, n in p["top_keys"])
        L.append(f"| {p['period']} | {p['added']} ({p['measured_full']}) | {f(p['bpm_median'])} "
                 f"({'-'.join(f(x) for x in p['bpm_iqr']) if p['bpm_iqr'] else '-'}) | {round(p['major_share'] * 100)}% | {keys} | "
                 f"{f(p['air_db_median'])} | {f(p['crest_db_median'])} | {f(p['intro_bars_before_first_peak_median'])} | "
                 f"{round(p['peak_share_median'] * 100) if p['peak_share_median'] is not None else '-'}% | "
                 f"{f(p['breakdowns_median'])} | {int(p['length_s_median'] // 60)}:{int(p['length_s_median'] % 60):02d} | "
                 f"{f(p['release_year_median'], '{:.0f}')} |")
    fams = [k for k in fam_names] + ["unassigned"]
    L += ["", "Style family of the tracks added, share of that period (inferred, Claude's read):", "",
          "| added in | " + " | ".join(fam_names.get(k, "no family") for k in fams) + " |",
          "|---|" + "---|" * len(fams)]
    for p in pers:
        L.append(f"| {p['period']} | " + " | ".join(f"{round(p['family_share'].get(k, 0) * 100)}%" for k in fams) + " |")
    L += ["", "Tracks that mark each period (the family whose share grew most against the period before, and its first "
          "tracks added in that period):", ""]
    for p in pers:
        mk = p["markers"]
        gain = f", up {round(mk['share_gain'] * 100)} points" if mk["share_gain"] is not None else ""
        tr = "; ".join(f"{t['artists'].replace(';', ',')} - {t['title']} ({t['added']}"
                       + (f", {float(t['bpm']):.0f} BPM, {t['key']}" if t["bpm"] else ", not measured") + ")" for t in mk["tracks"])
        L.append(f"- **{p['period']}**, {fam_names.get(mk['family'], 'no family assigned')}{gain}: {tr}")
    return "\n".join(L)


def main() -> dict:
    rows = list(csv.DictReader(open(HERE / "tracks.csv", newline="", encoding="utf-8")))
    fam_names = {k: d["name"] for k, d in json.loads((HERE / "families.json").read_text(encoding="utf-8"))["families"].items()}
    pers, prev = [], None
    for name, years in periods(rows):
        sub = [r for r in rows if r["added_year"] in years]
        s = {"period": name, "years": years, **summarise(sub, fam_names)}
        s["markers"] = markers(sub, prev, s)
        pers.append(s)
        prev = s
    by_quarter = Counter(r["added_quarter"] for r in rows)
    doc = {
        "about": "Per-period taste figures by date added (Spotify added_at). Measured figures use only full-track "
                 "trackcard measurements; families are inferred. Built by chronology.py from tracks.csv.",
        "min_measured_per_period": MIN_N,
        "quarters_with_10_plus_tracks": sum(1 for v in by_quarter.values() if v >= 10),
        "quarters_with_tracks": len(by_quarter),
        "periods": pers,
        "references": references({r["id"]: r["added_at"][:10] for r in rows}),
        "overall": summarise(rows, fam_names),
    }
    (HERE / "chronology.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    chart(rows, pers, fam_names, HERE / "chronology.png")
    md = HERE / "CHRONOLOGY.md"
    block = "<!-- numbers:start -->\n" + table(pers, fam_names) + "\n<!-- numbers:end -->"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        text = re.sub(r"<!-- numbers:start -->.*?<!-- numbers:end -->", lambda _: block, text, flags=re.S)
    else:
        text = "# Chronology\n\n" + block + "\n"
    md.write_text(text, encoding="utf-8")
    print(table(pers, fam_names))
    return doc


if __name__ == "__main__":
    main()
