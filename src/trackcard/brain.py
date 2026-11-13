"""The Music Brain: taste evidence -> recounted patterns -> proposed rules -> approved rules.

Three layers, kept apart (the inspirationhouse model):
  1. evidence  taste/brain/evidence.jsonl  Krish's or Calvin's own words, kept whole, each tagged with signals from the
               fixed vocabulary (taste/brain/vocab.json). A quote must appear character for character in the words.
               Measured evidence is never stored: it is derived from taste/graph/tracks.csv on every run, so a track that
               leaves the playlist stops counting the next time anything is recounted.
  2. patterns  recounted from live evidence every run, never stored as truth (taste/brain/PATTERNS.md is a generated view).
  3. claims    taste/brain/claims.jsonl  at most N proposals a week (taste/brain/policy.json); once proposed, a claim's
               words and evidence are frozen (sha256). Krish accepts or rejects. A rejected claim is never proposed again.
               Accepted claims render to taste/APPROVED.md, the only file a generator should treat as rules.

A signal never decides anything; only an accepted claim does.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

POS, NEG = 1, -1


class BrainError(Exception):
    pass


# ---------------------------------------------------------------- loading

@dataclass
class Brain:
    root: Path  # the taste/ folder
    vocab: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    tracks: list = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return self.root / "brain"


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def load(root: Path) -> Brain:
    root = Path(root)
    b = Brain(root)
    b.vocab = json.loads((b.dir / "vocab.json").read_text(encoding="utf-8"))["dimensions"]
    b.policy = json.loads((b.dir / "policy.json").read_text(encoding="utf-8"))
    b.evidence = _read_jsonl(b.dir / "evidence.jsonl")
    b.claims = _read_jsonl(b.dir / "claims.jsonl")
    tracks_csv = root / "graph" / "tracks.csv"
    if tracks_csv.exists():
        with open(tracks_csv, newline="", encoding="utf-8") as fh:
            b.tracks = list(csv.DictReader(fh))
    return b


# ---------------------------------------------------------------- layer 1: evidence

def check(b: Brain) -> list[str]:
    """Problems that make evidence invalid. An empty list means every row is usable."""
    problems, seen = [], set()
    for row in b.evidence:
        rid = row.get("id", "?")
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        if row.get("who") not in ("krish", "calvin"):
            problems.append(f"{rid}: who must be krish or calvin (measured evidence is derived, never written here)")
        if not row.get("where") or not row.get("when"):
            problems.append(f"{rid}: words need where and when they were said")
        for s in row.get("signals", []):
            if s["dim"] != "other" and s["slug"] not in b.vocab.get(s["dim"], {}):
                problems.append(f"{rid}: {s['dim']}/{s['slug']} is not in vocab.json (file it as other/<word>)")
            if s.get("polarity") not in (POS, NEG):
                problems.append(f"{rid}: polarity must be 1 or -1")
            if s.get("strength") not in (1, 2, 3):
                problems.append(f"{rid}: strength must be 1, 2 or 3")
            if not s.get("quote") or s["quote"] not in row.get("text", ""):
                problems.append(f"{rid}: quote {s.get('quote')!r} is not in the words character for character")
    return problems


def _f(row: dict, k: str) -> float | None:
    v = row.get(k)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def measured_signals(track: dict) -> list[tuple[str, str]]:
    """Vocabulary signals a measured playlist track supports. Rules mirror the vocab descriptions."""
    if track.get("measured") != "True":
        return []
    out = []
    bpm = _f(track, "bpm")
    if bpm is not None:
        out.append(("groove", "tempo_118_125" if 118 <= bpm <= 125 else "tempo_under_110" if bpm < 110
                    else "tempo_126_plus" if bpm >= 126 else "tempo_other"))
    out.append(("harmony", f"{track['mode']}_key"))
    bands = {b: _f(track, f"band_{b}") for b in ("sub", "low", "mid", "high-mid", "air")}
    if all(v is not None for v in bands.values()):
        if max(bands, key=bands.get) in ("sub", "low"):
            out.append(("mix", "warm_low_end"))
        if bands["air"] <= -18:
            out.append(("mix", "soft_top"))
        elif bands["air"] >= -14:
            out.append(("mix", "airy_top"))
    crest = _f(track, "crest_db")
    if crest is not None:
        if crest >= 11:
            out.append(("mix", "dynamic_master"))
        elif crest < 8.5:
            out.append(("mix", "dense_master"))
    if (_f(track, "duration_s") or 0) >= 360:
        out.append(("arrangement", "club_length"))
    if track.get("full_track") == "True":  # arrangement figures need the whole track
        intro = _f(track, "intro_bars_before_first_peak")
        if intro is not None:
            out.append(("arrangement", "quick_to_groove" if intro <= 16 else "long_intro"))
        if (_f(track, "peak_share") or 0) >= 0.7:
            out.append(("arrangement", "held_energy"))
        if (_f(track, "breakdowns") or 0) >= 2:
            out.append(("arrangement", "multiple_breakdowns"))
    return [s for s in out if s[1] != "tempo_other"]


MEASURABLE = {("groove", "tempo_118_125"), ("groove", "tempo_under_110"), ("groove", "tempo_126_plus"),
              ("harmony", "minor_key"), ("harmony", "major_key"), ("mix", "warm_low_end"), ("mix", "soft_top"),
              ("mix", "airy_top"), ("mix", "dynamic_master"), ("mix", "dense_master"), ("arrangement", "club_length"),
              ("arrangement", "quick_to_groove"), ("arrangement", "long_intro"), ("arrangement", "held_energy"),
              ("arrangement", "multiple_breakdowns")}


# Denominator for each measured slug: which tracks could have shown it.
def _eligible(track: dict, dim: str, slug: str) -> bool:
    if (dim, slug) not in MEASURABLE:
        return False
    if track.get("measured") != "True":
        return dim == "arrangement" and slug == "club_length"
    if dim == "arrangement" and slug != "club_length":
        return track.get("full_track") == "True"
    return True


# ---------------------------------------------------------------- layer 2: patterns (recounted, never stored)

def recount(b: Brain) -> dict[tuple[str, str], dict]:
    pats: dict[tuple[str, str], dict] = {}

    def pat(dim, slug):
        return pats.setdefault((dim, slug), {
            "dim": dim, "slug": slug, "words_like": 0, "words_dislike": 0, "words_sources": set(),
            "word_ids": [], "quotes": [], "measured_tracks": [], "eligible": 0})

    for row in b.evidence:
        if row.get("withdrawn"):
            continue  # taken back: stops counting
        for s in row.get("signals", []):
            slug = s["slug"] if s["dim"] != "other" else f"other:{s['slug']}"
            p = pat(s["dim"], slug)
            p["words_like" if s["polarity"] == POS else "words_dislike"] += s["strength"]
            p["words_sources"].add(row.get("track") or row["id"])
            p["word_ids"].append(row["id"])
            p["quotes"].append(f"{row['who'].title()}: \"{s['quote']}\" ({row.get('track') or row['where']})")

    for t in b.tracks:
        for dim, slug in measured_signals(t):
            pat(dim, slug)["measured_tracks"].append(t)
    for (dim, slug), p in pats.items():
        if dim != "other":
            p["eligible"] = sum(1 for t in b.tracks if _eligible(t, dim, slug))
        n = len(p["measured_tracks"])
        p["measured_share"] = round(n / p["eligible"], 2) if p["eligible"] else 0.0
        p["both_ways"] = p["words_like"] > 0 and p["words_dislike"] > 0
        p["trend"] = _trend(p["measured_tracks"], [t for t in b.tracks if _eligible(t, dim, slug)])
    return pats


def _trend(hits: list[dict], eligible: list[dict]) -> str | None:
    """Share among the newest third of tracks by added_at minus the oldest third, when added dates exist."""
    dated = sorted((t for t in eligible if t.get("added_at")), key=lambda t: t["added_at"])
    if len(dated) < 9:
        return None
    third = len(dated) // 3
    hit_ids = {t["id"] for t in hits}
    old = sum(t["id"] in hit_ids for t in dated[:third]) / third
    new = sum(t["id"] in hit_ids for t in dated[-third:]) / third
    delta = new - old
    return "rising" if delta >= 0.15 else "falling" if delta <= -0.15 else "steady"


def qualifies(p: dict, policy: dict) -> int | None:
    """Return the polarity a pattern supports strongly enough to propose, or None."""
    if p["dim"] == "other":
        return None
    if p["words_like"] >= policy["words_min_strength"] and len(p["words_sources"]) >= policy["words_min_sources"] \
            and p["words_like"] > p["words_dislike"]:
        return POS
    if p["words_dislike"] >= policy["words_min_strength"] and p["words_dislike"] > p["words_like"]:
        return NEG
    if len(p["measured_tracks"]) >= policy["measured_min_tracks"] and p["measured_share"] >= policy["measured_min_share"]:
        return POS
    return None


def score(p: dict) -> float:
    return p["words_like"] + p["words_dislike"] + 3 * len(p["words_sources"]) + 10 * p["measured_share"]


# ---------------------------------------------------------------- layer 3: claims

def _claim_text(b: Brain, p: dict, pol: int) -> str:
    desc = b.vocab[p["dim"]][p["slug"]]
    parts = []
    if p["word_ids"]:
        parts.append(f"said {len(p['word_ids'])} time(s) across {len(p['words_sources'])} track(s)")
    if p["measured_tracks"]:
        parts.append(f"measured in {len(p['measured_tracks'])} of {p['eligible']} playlist tracks")
    lead = "Likes" if pol == POS else "Avoid"
    return f"{lead}: {desc} ({'; '.join(parts)})."


def _evidence_refs(p: dict) -> list[str]:
    refs = list(dict.fromkeys(p["word_ids"]))
    refs += [f"track:{t['id']}" for t in p["measured_tracks"][:12]]
    return refs


def _hash(c: dict) -> str:
    body = json.dumps({k: c[k] for k in ("dim", "slug", "polarity", "text", "evidence", "quotes")}, sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _week(today: dt.date) -> str:
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


def propose(b: Brain, today: dt.date | None = None) -> list[dict]:
    """Add up to the weekly budget of new proposals. Returns the new claims (also appended to b.claims)."""
    today = today or dt.date.today()
    week = _week(today)
    budget = b.policy["proposals_per_week"] - sum(1 for c in b.claims if c.get("proposed_week") == week)
    if budget <= 0:
        return []
    taken = {(c["dim"], c["slug"], c["polarity"]) for c in b.claims}  # proposed, accepted or rejected: never again
    cands = []
    for p in recount(b).values():
        pol = qualifies(p, b.policy)
        if pol is not None and (p["dim"], p["slug"], pol) not in taken:
            cands.append((score(p), p, pol))
    new = []
    for _, p, pol in sorted(cands, key=lambda x: -x[0])[:budget]:
        c = {"id": f"{p['dim']}.{p['slug']}" + ("" if pol == POS else ".avoid"), "dim": p["dim"], "slug": p["slug"],
             "polarity": pol, "text": _claim_text(b, p, pol), "evidence": _evidence_refs(p), "quotes": p["quotes"][:5],
             "status": "proposed", "proposed_week": week, "proposed_on": today.isoformat()}
        c["frozen_sha256"] = _hash(c)
        new.append(c)
    b.claims.extend(new)
    return new


def decide(b: Brain, claim_id: str, accept: bool, why: str = "", today: dt.date | None = None) -> dict:
    c = next((c for c in b.claims if c["id"] == claim_id), None)
    if c is None:
        raise BrainError(f"no claim {claim_id!r}")
    if c["status"] != "proposed":
        raise BrainError(f"{claim_id} is already {c['status']}")
    if _hash(c) != c["frozen_sha256"]:
        raise BrainError(f"{claim_id} was edited after it was proposed; frozen claims never change")
    c["status"] = "accepted" if accept else "rejected"
    c["decided_on"] = (today or dt.date.today()).isoformat()
    if why:
        c["why"] = why
    return c


def save(b: Brain) -> None:
    _write_jsonl(b.dir / "claims.jsonl", b.claims)


# ---------------------------------------------------------------- views

def render(b: Brain) -> tuple[str, str]:
    """(APPROVED.md, PATTERNS.md) as text. Both are generated; edit evidence or claims, never these."""
    pats = recount(b)
    a = ["# Approved taste rules", "",
         "Generated by `trackcard brain render` from `taste/brain/claims.jsonl`. These are the only lines a generator or a",
         "Splice search should treat as rules. Each was proposed from evidence and accepted by Krish. Do not edit by hand.",
         "They rank below truth and safety, and they describe how a track is made, not what it is about.", ""]
    acc = [c for c in b.claims if c["status"] == "accepted"]
    if not acc:
        a.append("None accepted yet. See the open proposals below.")
    for c in acc:
        p = pats.get((c["dim"], c["slug"]))
        weak = p is None or qualifies(p, b.policy) != c["polarity"]
        a.append(f"- **{c['text']}** `[{c['id']}]`" + ("  _weakened: today's evidence no longer meets the bar_" if weak else ""))
        for q in c["quotes"][:2]:
            a.append(f"  - {q}")
    open_ = [c for c in b.claims if c["status"] == "proposed"]
    if open_:
        a += ["", "## Waiting for Krish", "",
              "Accept with `trackcard brain accept <id>`, reject with `trackcard brain reject <id> --why \"...\"`.", ""]
        a += [f"- `{c['id']}` (proposed {c['proposed_on']}): {c['text']}" for c in open_]
    rej = [c for c in b.claims if c["status"] == "rejected"]
    if rej:
        a += ["", "## Rejected (never proposed again)", ""] + [f"- `{c['id']}`: {c.get('why', 'no reason given')}" for c in rej]

    t = ["# Taste patterns (recounted)", "",
         "Generated by `trackcard brain render`. Patterns are recounted from live evidence on every run: Krish's and Calvin's",
         "words in `evidence.jsonl` (withdrawn rows excluded) and trackcard measurements of the playlist in `graph/tracks.csv`.",
         "A pattern decides nothing until it becomes an accepted claim.", "",
         "| dimension | signal | words like | words dislike | sources | measured | share | trend | state |",
         "|---|---|---|---|---|---|---|---|---|"]
    claimed = {(c["dim"], c["slug"]): c["status"] for c in b.claims}
    for p in sorted(pats.values(), key=lambda p: (p["dim"], -score(p))):
        pol = qualifies(p, b.policy)
        state = claimed.get((p["dim"], p["slug"])) or ("ready to propose" if pol else "")
        if p["both_ways"]:
            state = (state + ", pulls both ways").strip(", ")
        t.append(f"| {p['dim']} | {p['slug']} | {p['words_like']} | {p['words_dislike']} | {len(p['words_sources'])} | "
                 f"{(str(len(p['measured_tracks'])) + '/' + str(p['eligible'])) if p['eligible'] else 'n/a'} | "
                 f"{format(p['measured_share'], '.0%') if p['eligible'] else 'n/a'} | {p['trend'] or 'n/a'} | {state} |")
    return "\n".join(a) + "\n", "\n".join(t) + "\n"


def write_views(b: Brain) -> None:
    approved, patterns = render(b)
    (b.root / "APPROVED.md").write_text(approved, encoding="utf-8")
    (b.dir / "PATTERNS.md").write_text(patterns, encoding="utf-8")
