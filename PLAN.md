# trackcard: build plan

## Context
Krish produces electronic music without Ableton and uses Claude + the Splice connector to find sounds. Claude can't hear, so it can't tell what a bounce contains. `trackcard` is Claude's ears: it listens to a WAV locally and writes a plain-text card (structure, stems per section, frequency gaps, loudness, and a Splice brief) that Claude reads to pick sounds that fit the real track. Every decision serves one feeling: he points at a moment ("bar 33, the drop"), says what he wants, and Claude answers as if it had listened. So the card must be bar-addressable, honest about fallbacks, and the Splice brief must be actionable by a zero-context AI.

## Sources (read, verified)
- **Target repo**: `/home/user/music-analyzer` (krishanraja/music-analyzer) is empty, no commits. It *is* the new empty folder; build there on branch `claude/blissful-noether-ay5cf4`.
- **MixMind** (Intellligent15/MixMind--AI-DJ, no license) cloned read-only to scratchpad. Methods to reimplement in my own code:
  - `analysis/service.py`: tempo octave correction (hints x0.5, x2/3, x1.5, x2 when outside 85-170; score = mean onset strength at beats, 0.6 penalty out of range); downbeat phase from <250 Hz mel onset envelope; 1 Hz RMS.
  - `analysis/key.py`: HPSS harmonic, chroma_cens, RMS-weighted mean, Krumhansl-Kessler 24 profiles, relative-pair tiebreak (<0.05 gap, louder tonic wins), Camelot maps.
  - `analysis/allin1_source.py`: import-guarded allin1, return None on any failure.
  - `analysis/sections/librosa_laplacian.py`: beat-sync CQT, recurrence + path_enhance, normalized Laplacian, eigengap k in [3,6], KMeans, median filter, merge <8 s.
  - `stems/service.py`: htdemucs via `demucs.apply`, chain (GPU shifts=2, GPU shifts=0, CPU shifts=0), 10 Hz RMS+peak envelopes, soundfile load (no torchaudio).
- **Stem MCP** is `tolutronics/audio-processing-mcp` (`src/stem_mcp`, MIT per README) cloned read-only. Take only the MCP server skeleton (`mcp.server.Server`, list_tools/call_tool, stdio) and the Demucs idea. Ignore its analyze_audio, create_loop, separate_vocal_ranges. Attribution in NOTICE.
- Environment: Python 3.11 at `/usr/bin/python3.11`, uv present, 4 CPU, 15 GB RAM, no GPU. PyPI and the Demucs weight host (dl.fbaipublicfiles.com) reachable.

## Repo layout (kept small)
```
PLAN.md  README.md  NOTICE  LICENSE (MIT)  pyproject.toml  .gitignore
src/trackcard/
  __init__.py
  audio.py        load WAV (soundfile), mono 22050 for analysis, clear errors for missing/unsupported/silent/too-short files
  tempo.py        beat track, octave correction, downbeat phase, BPM confidence note, bar grid (4/4 assumed)
  key.py          K-S key + mode + Camelot + confidence (score margin)
  sections.py     allin1 path -> Laplacian fallback; returns sections + method + fallback reason; snaps boundaries to nearest downbeat for bar numbers
  stems.py        htdemucs separation, device fallback chain, 10 Hz RMS/peak envelopes; graceful "stems unavailable" result if demucs/torch missing or fails
  spectrum.py     per-section band energy: sub 20-60, low 60-250, mid 250-2k, high-mid 2k-8k, air 8k-20k; relative dB + plain flags
  loudness.py     RMS dBFS per bar/second, peak, crest factor, per-section level, LUFS-ish via pyloudnorm if installed else RMS (labelled)
  brief.py        gap/weak-layer rules -> Splice brief rows (BPM range, key + compatible Camelot, layer type from the fixed list, tag candidates)
  card.py         TrackCard dataclass -> markdown + json writers
  pipeline.py     analyze(path, options) -> TrackCard; collects every fallback into card.notes
  cli.py          `trackcard analyze PATH [--json] [--out DIR] [--no-stems] [--sections auto|allin1|laplacian]`, `trackcard mcp`
  mcp_server.py   one tool `analyze_track(path, include_json?)` returning card markdown
tests/
  conftest.py     synth helpers: click track at known BPM, chord progression in known key, multi-section synthetic track
  test_tempo.py test_key.py test_sections.py test_spectrum.py test_brief.py test_card.py test_cli.py test_mcp.py
  test_stems.py   envelope math on synthetic tensors; real Demucs run marked `slow`
  test_e2e_fixture.py  runs on first tests/fixtures/*.wav, skipped with a clear reason if none
  fixtures/.gitkeep   (audio gitignored)
```
Dependencies: core `numpy, scipy, librosa, soundfile, scikit-learn, click, mcp`; extra `[stems]` = `torch, demucs`; extra `[sections]` = `allin1` (documented as best-effort: needs madmom/natten); extra `[loudness]` = `pyloudnorm`; `[dev]` = pytest. Default `pip install trackcard[stems]` recommended in README. Python >=3.11.

## Card contents (section order fixed as specified)
Header line: file, analysed date, methods used, any fallbacks in a "Read this first" notes block.
1. **Basics**: duration, BPM + confidence note (octave-corrected? beat interval stability), 4/4 assumed, key + mode, Camelot, key confidence.
2. **Section map**: table `# | label | bars | time range | length`; method line ("allin1" or "librosa Laplacian fallback: section_N = similar material, not intro/drop"). Bars 1-indexed from detected first downbeat.
3. **Stem activity**: per section per stem: active/sparse/absent + density (% of 100 ms frames above a per-stem threshold relative to the stem's own track peak and an absolute floor) + relative level. Says plainly when stems were skipped.
4. **Frequency balance**: per section band shares in dB relative to that section's loudest band, plus flags: "nothing above 8 kHz", "sub-heavy / no sub", "mids scooped", etc.
5. **Loudness and dynamics**: overall peak dBFS, RMS/LUFS, crest factor, per-section level and change vs previous ("+6 dB into drop"), a compact per-8-bar level strip.
6. **Splice brief**: per section with a gap/weak layer: what's missing and why (evidence), layer type (from fixed list), BPM range (exact ±3, plus half/double-time variant where sensible), key + compatible keys (Camelot neighbours), candidate tags, and a one-line instruction a zero-context AI can execute. Rules are deterministic and listed in README (e.g. drums absent in a high-energy section -> drums/percussion; no air -> fx/hats/shakers; bass absent -> bass; vocals absent across track -> vocals optional; thin mids -> chords/pads/keys).

## Build order (tests after each stage)
1. PLAN.md, pyproject, .gitignore, LICENSE, NOTICE skeleton; venv with python3.11 via uv.
2. Analysis core: audio, tempo, key, spectrum, loudness + tests (click track at 128 BPM and at 70 BPM -> corrected to 140; Am and E-major chord progressions).
3. Stems: envelopes + device chain; install torch CPU + demucs; real slow test on a synthetic 20 s clip.
4. Section map: try installing allin1; Laplacian fallback tested on a synthetic A/B/A track with known boundaries; bar snapping.
5. Card writer (md + json) + brief rules + tests.
6. CLI (click) + test via CliRunner, error messages for bad paths.
7. MCP server (low-level `mcp.server.Server`, stdio) + test calling the handler in-process.
8. README written last against the actual code; every claim traceable to code.
9. Commit and push to `claude/blissful-noether-ay5cf4` (no PR).

## Verification
- `pytest` green: synthetic BPM within ±1 BPM, key and mode exact on chord progressions, Laplacian boundaries within 2 s on the synthetic A/B/A track, CLI writes `.trackcard.md` + `.json`, MCP tool returns the card.
- Real Demucs run on a synthetic bounce to confirm the model download and CPU path.
- **Fixture WAV**: none exists in the repo yet. If Krish uploads one to `tests/fixtures/` before the end, I run the e2e test and `trackcard analyze` on it and show the card. Otherwise I render a synthesized multi-section demo bounce in the scratchpad (never committed), show that card, and state plainly that the real-WAV e2e test was skipped and is unverified.
- Final summary: what works, what fell back (expected: allin1 likely uninstallable on this CPU box, so Laplacian; pyloudnorm optional), what I could not verify (GPU path, allin1 labels, real-track accuracy without his ear).
