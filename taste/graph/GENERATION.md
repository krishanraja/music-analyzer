# Generating from Krish's taste graph

Read this before writing, arranging or choosing sounds for a new track. The graph (`graph.json`, `tracks.csv`) holds the
99 tracks of Krish's Spotify playlist "Chillout & Ambient" (read 2026-11-13), linked to their artists, labels, release years,
keys, tempo bands, measured traits and inferred style families, plus `sounds_like` edges between the nearest tracks.

**Where each number comes from.** Measured by trackcard: 48 full tracks taken from SoundCloud (the upload's length within 6 s
of Spotify's), and 47 Spotify preview clips (15-30 s, a slice of the track) for the rest. 4 tracks have neither. Arrangement
figures use only the 48 full tracks. Style families are Claude's read of the artists (`families.json`), not measured. No stem
separation in this pass. Rebuild: `python taste/graph/build_graph.py <dir with pl/meta.json, full/, pl/audio/>`.

## The target, in numbers (medians, middle half in brackets)

| what | value | use it as |
|---|---|---|
| tempo | 122 BPM (118-125); 52 of 95 tracks sit at 120-125 | default 122; under 118 only for the downtempo family |
| mode | 56 minor, 39 major | minor by default; major for the sunny vocal family |
| commonest keys | 8A A minor (14), 6A G minor (12), 5B E-flat major (7), 11A F-sharp minor (7) | start in A minor or G minor |
| loudness | -10.4 LUFS (-12 to -8.6) | master to about -10, not louder |
| dynamics | crest 11.7 dB (10.3-13.1) | keep the transients; do not crush the master |
| tonal balance | low band loudest; sub -4, mid -5, high-mid -13, air -19 dB from it | warm and bottom-heavy, soft top; no harsh hats |
| length | 5:33 (4:15-6:32) | 6 minutes plus for club versions |
| era | 2013 (2012-2014) | the 2012-15 deep-house moment |

**Arrangement (48 full tracks).** About 8 sections. The first full-energy section arrives after about 8 bars (20 s), and the
track sits at full energy for about 70% of its length, with 2 breakdowns. My read: these tracks get to the groove quickly
and stay in it. They are not long builds to one big drop. The German melodic family takes longest to arrive (median 14 bars).

## The four families (inferred), with measured settings

| family | tracks measured | tempo | mode | sound | arrangement |
|---|---|---|---|---|---|
| German and Euro melodic deep house (Kalkbrenner, Klangkarussell, Sascha Braemer, Niconé) | 37 | 122 | 24 minor : 13 major | darkest top end (air -20) | longest intro (14 bars), 2-3 breakdowns |
| Sunny deep house with a song in it (Robin Schulz, Kygo, Bakermat, Nora En Pure, Klingande) | 27 | 121 | 15 major : 12 minor | brightest of the house families, most dynamic (crest 12.1) | 8-bar intro, 2 breakdowns |
| UK and soulful vocal house (Duke Dumont, Route 94, Storm Queen, Joe Goddard) | 18 | 123 | 14 minor : 4 major | densest master (crest 10.7) | 8-bar intro, about 1 breakdown, 80% at full energy |
| Downtempo, trip-hop, global (Bonobo, Nitin Sawhney, Morcheeba, Parov Stelar) | 14 | 118, range 105-128 | 8 major : 6 minor | darkest of all (air -21.5), most dynamic | varies |

## How this fits the rest of `taste/`

- This playlist is mostly 2012-2014. The newer references in `references.json` (All Day I Dream, Spencer Brown, Essáy) carry
  the same tempo and warmth but lean major. My read: the taste has kept its tempo and warmth and moved towards organic and
  euphoric material. Weight the newer references when the two disagree.
- "Closure" (125 BPM, D minor, `ear-checks.json`) sits inside this profile on tempo and mode.

## Prompting a generator or picking sounds

1. Pick the family, then take its row above as the defaults (BPM, mode, how bright).
2. Pick 2-3 reference tracks from that family in `tracks.csv` and follow their `sounds_like` edges in `graph.json` for neighbours.
3. Keys: stay in the chosen key or a Camelot neighbour (`mixes_with` edges).
4. Arrangement: get the groove in by bar 8-16, hold full energy for about 70% of the track, 2 breakdowns.
5. Mix: warm low end, soft top, about -10 LUFS with an 11-12 dB crest.

## Still to measure

51 tracks have no full-track measurement (`full_track` empty or False in `tracks.csv`). Fetch them at home with content-watcher
(its YouTube route works there), run `trackcard analyze <file> --json --no-stems`, put the `.trackcard.json` in `full/` named by
Spotify track id, and rebuild.
