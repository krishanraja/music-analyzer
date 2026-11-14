# Generating from Krish's taste graph

Read this before writing, arranging or choosing sounds for a new track. Rules Krish has approved are in `taste/APPROVED.md`
and win over anything here; the numbers below are evidence, not rules. The graph (`graph.json`, `tracks.csv`) holds all
346 tracks of Krish's Spotify playlist "Chillout & Ambient" (read 2026-11-13 through the Spotify Web API with his login),
linked to their artists, labels, release years, keys, tempo bands, measured traits, inferred style families, the month and
year he added each one (`added_in` edges), and `sounds_like` edges between the nearest tracks. How his taste moved over
time is in `CHRONOLOGY.md`.

**Where each number comes from.** Added dates, titles, artists, lengths and release dates: Spotify Web API. Labels: the ℗
line on each album page (the API no longer returns labels), 342 of 346. Measured by trackcard on the **full track** for 342
of 346 tracks (no stems, Laplacian sections). The audio came from SoundCloud or YouTube, accepted only when the file's real
length was within 6 s of Spotify's, and each match was checked independently for the right recording (21 wrong uploads
caught on 19 tracks; all 19 replaced with the right recording). Each track's `audio_source` and `measured_from` say where its audio came from. No preview clips are
used. 4 tracks have no audio. Stems (Demucs) on 5 tracks only, so drop counts are sparse. Style families are Claude's read
of the artists (`families.json`), not measured.

Rebuild: `python taste/graph/build_graph.py <folder of <spotify id>.trackcard.json + fetch_log.jsonl>`, then
`python taste/graph/chronology.py`.

## The target, in numbers (medians over 342 full tracks, middle half in brackets)

| what | value | use it as |
|---|---|---|
| tempo | 122 BPM (118-125); 179 of 342 tracks sit at 120-125 | default 122; under 118 only for the downtempo family |
| mode | 192 minor, 150 major | minor slightly more often; major is common, especially in the newer families |
| commonest keys | 6A G minor (40), 4A F minor (26), 8A A minor (25), 8B C major (24), 5B E-flat major (23) | start in G minor, F minor or A minor |
| loudness | -9.9 LUFS (-11.1 to -8.7) | master to about -10, not louder |
| dynamics | crest 11.7 dB (10.7-12.9) | keep the transients; do not crush the master |
| tonal balance | low band loudest; mid -4, sub -7, high-mid -13, air -20 dB (each band relative to the track's loudest) | warm and bottom-heavy, soft top; no harsh hats |
| length | 4:33 (3:35-6:04) | 4-5 minutes; 6 plus for club versions |
| era | released 2014 (2013-2019); added a median of 2 years after release | the 2012-15 deep-house core, with newer additions |

**Arrangement (342 full tracks).** About 7 sections. The first full-energy section arrives after a median of 12 bars
(24 s), and the track sits at full energy for about 69% of its length, with 2 breakdowns. My read: these tracks get to the
groove quickly and stay in it. They are not long builds to one big drop. Arrangement figures come from the full mix without
stems.

## The families (inferred), with measured settings

| family | tracks measured | tempo (range) | mode | sound | arrangement |
|---|---|---|---|---|---|
| German and Euro melodic deep house (Worakls, Klangkarussell, Fritz Kalkbrenner, Joachim Pastor) | 72 | 120.5 (100-128) | 47 minor : 25 major | dark top end (air -20.8) | longest intro (16 bars), longest tracks (6:06), 2 breakdowns |
| Sunny deep house with a song in it (Kygo, Parra for Cuva, Bakermat, Robin Schulz, Nora En Pure) | 66 | 122 (99-154) | 35 minor : 31 major | brightest of the house families (air -18.3) | 9-bar intro, 2 breakdowns |
| UK and soulful vocal house (RÜFÜS DU SOL, Flight Facilities, Claptone, Duke Dumont) | 58 | 122 (100-133) | 34 minor : 24 major | bright (air -18.5) | 10-bar intro, 2 breakdowns, 72% at full energy |
| Melodic progressive (Above & Beyond, anamē, 16BL, Marsh, Amonita) | 43 | 122 (85-138) | 23 major : 20 minor | air -19.3, crest 11.6 | quickest of the melodic families to arrive (8 bars), 2 breakdowns, 5:02 |
| Future bass and Aussie indie electronic (Flume, ODESZA, Chet Faker, Hermitude) | 37 | 120 (87-160) | 19 major : 18 minor | crest 11.3 | 16-bar intro, shortest at full energy (62%) |
| Downtempo, trip-hop and global fusion (Nitin Sawhney, Parov Stelar, Pretty Lights) | 33 | 114 (84-160) | 19 minor : 14 major | darkest (air -22.5), most dynamic (crest 12.9) | 1 breakdown |
| Organic, Afro and sunrise house (Vandelux, nimino, VICTHOR, Sébastien Léger) | 16 | 121 (114-126) | 8 : 8 | air -20.2, crest 11.8 | longest build (22 bars), least time at full energy (57%) |
| Groove-led tech house (PAWSA, Levi, Funk Cartel, SOFI TUKKER) | 11 | 125 (108-132) | 8 minor : 3 major | densest master (crest 9.6) | full energy from the start, 1 breakdown, 3:23 |

## How his taste moved, and how it fits the rest of `taste/`

Full detail is in `CHRONOLOGY.md`. In short (measured, families inferred): the tempo held at about 122 BPM in every year
from 2014 to 2026, and since 2024 it has been tighter than ever (73% of additions at 120-126). The style moved from German
melodic deep house (37% of 2014's tracks, 3% of 2026's) to melodic progressive and organic house (together 59% of 2026's
additions). Additions got shorter (5:36 in 2014, 4:02 in 2024-2026). Major share rose only a little (42% to 51%).

- The 2026 references in `references.json` (Spencer Brown, Sébastien Léger, Jody Wisternoff, Essáy, JAIV) belong to the
  rising families. My read: weight the 2024-2026 rows and the newer references when they disagree with the 2014 core. On mode,
  the measured playlist backs only a lean towards major, not a switch.
- "Closure" (125 BPM, D minor, `ear-checks.json`) sits inside this profile on tempo and mode.

## Prompting a generator or picking sounds

1. Pick the family, then take its row above as the defaults (BPM, mode, how bright). For something current, start from
   melodic progressive or organic house.
2. Pick 2-3 reference tracks from that family in `tracks.csv` (sort by `added_at` for his recent picks) and follow their
   `sounds_like` edges in `graph.json` for neighbours.
3. Keys: stay in the chosen key or a Camelot neighbour (`mixes_with` edges).
4. Arrangement: get the groove in by bar 8-16, hold full energy for about 70% of the track, 2 breakdowns.
5. Mix: warm low end, soft top, about -10 LUFS with an 11-12 dB crest.

## Still to measure

- 4 tracks have no audio (`measured_from` = none in `tracks.csv`): Milky Chance "Stolen Dance (FlicFlac Club Edit)", Set Mo
  "Comfort You (Extended Mix)", Crusy "Kiki", Shouse and Adam Ten "Sunrise (Adam Ten Remix)". No upload within 6 s of
  Spotify's length was found on SoundCloud or YouTube.
- Stems: run `trackcard analyze <wav> --json --sections laplacian` (with stems) to add drops and stem densities. On this
  machine that takes about 35 hours for all 342 tracks.
