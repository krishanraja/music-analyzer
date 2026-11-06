"""Splice brief: deterministic rules that turn measured gaps into sound requests.

Every suggestion carries the evidence that triggered it, so a reader with no
context can see why it is there. Rules (checked per section, highest priority
first, at most MAX_PER_SECTION kept):

R1 drums absent in a peak section (within 3 dB of the loudest) -> drums
R2 bass absent in a peak section -> bass
R3 no sub (<60 Hz) while bass is active in a peak section -> bass (sub)
R4 nothing / very little above 8 kHz in a section within 8 dB of the loudest -> percussion
R5 drums only sparse in a peak section -> percussion
R6 mids measurably scooped, or neither "other" (chords, pads, synths) nor "vocals"
   (which also catches vocal-like leads) is active -> chords (loud section) / pads
R7 section sits 4 dB or more below the next one -> fx (riser into the next section)
R8 only one stem (or none) active in a non-peak section -> pads (texture)
R9 no vocals anywhere in the track -> vocals (one track-level suggestion, optional)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .key import CAMELOT_MAJOR, CAMELOT_MINOR, PITCHES, compatible_camelot

LAYER_TYPES = (
    "drums", "percussion", "bass", "guitar", "keys", "strings", "vocals",
    "chords", "leads", "pads", "brass-and-woodwinds", "synth", "fx",
)
HIGH_ENERGY_DB = -3.0  # a 'peak' section; mid sections are often deliberate breakdowns
NOT_LOW_DB = -8.0
RISER_JUMP_DB = 4.0
MAX_PER_SECTION = 3

TAGS = {
    "drums": ["drum loops", "kicks", "hats", "claps", "grooves", "top loops"],
    "drums_sub": [],
    "bass": ["basslines", "bass loops", "sub bass", "synth bass"],
    "bass_sub": ["sub bass", "808", "sub"],
    "perc_air": ["hats", "shakers", "cymbals", "rides", "top loops"],
    "perc_sparse": ["percussion loops", "shakers", "congas", "bongos", "tops"],
    "chords": ["chords", "stabs", "chord loops", "keys"],
    "pads": ["pads", "atmospheres", "textures", "drones"],
    "fx": ["risers", "uplifters", "sweeps", "white noise", "fx"],
    "vocals": ["vocal chops", "vocal phrases", "vocal one shots", "vox"],
}
UNPITCHED = {"drums", "percussion", "fx"}


def camelot_name(code: str) -> str:
    table = CAMELOT_MAJOR if code.endswith("B") else CAMELOT_MINOR
    p = PITCHES[table.index(code)]
    return f"{p} {'major' if code.endswith('B') else 'minor'}"


@dataclass
class Suggestion:
    section: str  # section id, e.g. "S3", or "whole track"
    bars: str
    layer: str  # one of LAYER_TYPES
    gap: str  # what is missing, in plain words
    evidence: str  # the measurement behind it
    bpm_range: str
    key: str
    tags: list[str] = field(default_factory=list)
    instruction: str = ""
    rule: str = ""


@dataclass
class SectionFacts:
    sid: str
    bars: str
    label: str
    rel_level_db: float  # vs loudest section
    next_jump_db: float | None  # next section level minus this one
    stems: dict[str, str] | None  # stem -> "active" | "sparse" | "absent" (None = stems unavailable)
    band_rel: dict[str, float]
    band_flags: list[str]


def bpm_range(bpm: float) -> str:
    b = round(bpm)
    return f"{b - 2}-{b + 2}"


def key_text(layer: str, key_name: str, camelot: str, alt: tuple[str, str] | None = None) -> str:
    if layer in UNPITCHED:
        return "any (unpitched; skip the key filter)"
    head = f"{key_name} ({camelot})"
    skip = {camelot}
    if alt:
        head += f" or {alt[0]} ({alt[1]}); trackcard is unsure between the two, so confirm with the producer"
        skip.add(alt[1])
    compat = [c for c in compatible_camelot(camelot)[1:] if c not in skip]
    alts = ", ".join(f"{camelot_name(c)} ({c})" for c in compat)
    return f"{head}; also works: {alts}"


def _instr(layer, tags, bpm_rng, key_name, unpitched, bars, purpose, alt_name=None):
    k = "" if unpitched else f", key {key_name}" + (f" (or {alt_name})" if alt_name else "")
    kind = "loops" if layer in ("drums", "percussion", "bass") else "samples"
    return (
        f"Search Splice for {layer} {kind} at {bpm_rng} BPM{k}, tags: {', '.join(tags[:3])}. "
        f"Use it in bars {bars} to {purpose}."
    )


def merge_repeats(items: list[Suggestion], labels: dict[str, str]) -> list[Suggestion]:
    """Sections that share a label (same material) and trigger the same rule get one row."""
    out: list[Suggestion] = []
    seen: dict[tuple[str, str], Suggestion] = {}
    for s in items:
        k = (labels.get(s.section, s.section), s.rule)
        if k in seen:
            first = seen[k]
            first.section += f", {s.section}"
            first.bars += f", {s.bars}"
            continue
        c = Suggestion(**{**s.__dict__, "tags": list(s.tags)})
        seen[k] = c
        out.append(c)
    for c in out:
        if "," in c.bars:
            head, _, tail = c.instruction.partition(" Use it in bars ")
            purpose = tail.split(" to ", 1)[1] if " to " in tail else tail
            c.instruction = f"{head} Use it in bars {c.bars} to {purpose}"
    return out


def build_brief(
    sections: list[SectionFacts],
    bpm: float,
    key_name: str,
    camelot: str,
    vocals_anywhere: bool | None,
    alt_key: tuple[str, str] | None = None,
) -> list[Suggestion]:
    """alt_key: (name, camelot) of a second plausible key, or None."""
    alt_name = alt_key[0] if alt_key else None
    rng = bpm_range(bpm)
    out: list[Suggestion] = []

    def add(bucket, sf: SectionFacts, layer, tagkey, gap, evidence, purpose, rule):
        tags = TAGS[tagkey]
        bucket.append(
            Suggestion(
                section=sf.sid, bars=sf.bars, layer=layer, gap=gap, evidence=evidence,
                bpm_range=rng, key=key_text(layer, key_name, camelot, alt_key), tags=tags,
                instruction=_instr(layer, tags, rng, key_name, layer in UNPITCHED, sf.bars, purpose, alt_name),
                rule=rule,
            )
        )

    for sf in sections:
        here: list[Suggestion] = []
        high = sf.rel_level_db >= HIGH_ENERGY_DB
        st = sf.stems or {}
        lvl = f"section level {sf.rel_level_db:+.1f} dB vs the loudest section"
        if sf.stems is not None:
            if high and st.get("drums") == "absent":
                add(here, sf, "drums", "drums", "no drums in a peak-energy section",
                    f"drums stem absent; {lvl}", "give the section a groove", "R1")
            if high and st.get("bass") == "absent":
                add(here, sf, "bass", "bass", "no bassline in a peak-energy section",
                    f"bass stem absent; {lvl}", "anchor the low end", "R2")
            if high and st.get("bass") == "active" and sf.band_rel.get("sub", 0) <= -45:
                add(here, sf, "bass", "bass_sub", "bass has no sub weight",
                    f"bass stem active but sub band (<60 Hz) is {sf.band_rel['sub']:.0f} dB under the loudest band",
                    "add sub weight under the existing bass", "R3")
        air_flag = next((f for f in sf.band_flags if "above 8 kHz" in f or "above 2 kHz" in f or "very little air" in f), None)
        if air_flag and sf.rel_level_db >= NOT_LOW_DB:
            add(here, sf, "percussion", "perc_air", "top end is missing",
                f"{air_flag} in {sf.sid} (air band 8-20 kHz is {sf.band_rel.get('air', 0):.0f} dB under the loudest band)",
                "fill the space above 8 kHz", "R4")
        if sf.stems is not None and high and st.get("drums") == "sparse":
            add(here, sf, "percussion", "perc_sparse", "drums are sparse for the section's energy",
                f"drums stem only sparse; {lvl}", "thicken the groove", "R5")
        if sf.stems is not None:
            other = st.get("other")
            scooped = any("mids are scooped" in f for f in sf.band_flags)
            empty_mids = other in ("absent", "sparse") and st.get("vocals") != "active"
            if empty_mids or scooped:
                why = (f"'other' stem (chords/pads/synths) {other} and vocals stem {st.get('vocals')}"
                       if empty_mids else "")
                if scooped:
                    why = (why + "; " if why else "") + f"mid band {sf.band_rel.get('mid', 0):.0f} dB vs loudest band"
                layer, tk = ("chords", "chords") if high else ("pads", "pads")
                add(here, sf, layer, tk, "little harmonic content", why, "add harmony in the mids", "R6")
        if sf.next_jump_db is not None and sf.next_jump_db >= RISER_JUMP_DB:
            add(here, sf, "fx", "fx", "nothing builds into the next section",
                f"next section is {sf.next_jump_db:.1f} dB louder", "build into the next section (last 4-8 bars)", "R7")
        if sf.stems is not None and not high:
            n_active = sum(1 for v in st.values() if v == "active")
            if n_active <= 1:
                add(here, sf, "pads", "pads", "low-energy section is very bare",
                    f"{n_active} stem(s) active; {lvl}", "add texture without adding energy", "R8")
        out.extend(here[:MAX_PER_SECTION])
    out = merge_repeats(out, {sf.sid: sf.label for sf in sections})
    if vocals_anywhere is False:
        tags = TAGS["vocals"]
        out.append(
            Suggestion(
                section="whole track", bars="any", layer="vocals",
                gap="no vocals anywhere (optional: only if the track wants a human element)",
                evidence="vocals stem absent in every section", bpm_range=rng,
                key=key_text("vocals", key_name, camelot, alt_key), tags=tags,
                instruction=_instr("vocals", tags, rng, key_name, False, "of the highest-energy sections", "add a hook", alt_name),
                rule="R9",
            )
        )
    for s in out:
        assert s.layer in LAYER_TYPES
    return out
