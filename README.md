# trackcard

Claude can't hear. trackcard listens for it.

`trackcard analyze bounce.wav` reads a WAV bounce on your own computer and writes `bounce.trackcard.md`: a plain-text card with the tempo, key, a bar-numbered section map, which stems play in each section, the frequency balance, loudness, and a **Splice brief** of sounds that would fill measured gaps. Paste the card into a Claude chat (or let Claude call trackcard through MCP), point at a moment ("bars 17-24 feel empty"), and Claude can pick Splice sounds that fit the actual track.

Everything runs locally. No uploads, no API keys. The only network access is the one-time download of the Demucs model weights (about 80 MB) on first use.

## Install

Python 3.11 or newer. A CPU is enough; a CUDA or Apple Silicon GPU makes stem separation faster.

```bash
pip install "trackcard[stems,loudness] @ git+https://github.com/krishanraja/music-analyzer"
```

Or with uv:

```bash
uv tool install "trackcard[stems,loudness] @ git+https://github.com/krishanraja/music-analyzer"
```

Extras:

| extra | adds | without it |
|---|---|---|
| `stems` | PyTorch + Demucs (htdemucs stem separation) | sections 3 and part of 6 are skipped, and the card says so |
| `loudness` | pyloudnorm (integrated LUFS, ITU-R BS.1770) | RMS dBFS only, and the card says so |
| `sections` | allin1 (functional section labels) | Laplacian segmentation with `section_N` labels, and the card says so |

allin1 is best-effort. It needs `madmom` (install it from GitHub: `pip install git+https://github.com/CPJKU/madmom`) and NATTEN 0.17.x built for your exact PyTorch version, which is often hard to install, especially on CPU. If `import allin1` fails for any reason, trackcard falls back and names the reason in the card.

## Use

```bash
trackcard analyze path/to/bounce.wav            # writes path/to/bounce.trackcard.md
trackcard analyze bounce.wav --json             # also writes bounce.trackcard.json
trackcard analyze bounce.wav --out cards/       # write the card into another folder
trackcard analyze bounce.wav --no-stems         # skip Demucs (fast, less detail)
trackcard analyze bounce.wav --sections laplacian   # force the fallback segmenter
trackcard analyze bounce.wav --print            # also print the card to the terminal
```

Accepted files: `.wav`, `.wave`, `.aif`, `.aiff`, `.flac`, at least 5 seconds long. Bad input gets a plain error and a non-zero exit code (1 for a problem with the file, 2 for an unexpected error). Every fallback is printed as a `note:` line and listed at the top of the card.

**Speed.** On a 4-core CPU with no GPU, a 4-5 minute track takes about 3-4.5 minutes, most of it Demucs. `--no-stems` takes well under a minute.

**Bounce tip.** Export from bar 1 of the arrangement. When a file starts with sound on a beat, trackcard treats that beat as bar 1, so its bar numbers match the DAW's.

## What's in the card

1. **Basics.** Duration; BPM as measured plus the likely whole-number project tempo, with a confidence note; 4/4 (assumed); how bar 1 was chosen; key with major/minor and its Camelot code, with a confidence level and the runner-up key; harmonically compatible keys (Camelot neighbours and relative key).
2. **Section map.** One row per section: label, bar range, time range, length, and energy (the section's RMS level against the loudest section: peak, mid or low). The method line says whether labels came from allin1 or the Laplacian fallback.
3. **Stem activity.** Demucs htdemucs splits the bounce into drums, bass, vocals and other. Per section and stem: *active* (sounding in 45% or more of the section's 100 ms frames), *sparse* (10-45%), or *absent*, with the percentage. A frame counts as sounding when the stem is louder than -50 dBFS and within 30 dB of its own loud moments.
4. **Frequency balance per section.** Energy in sub (20-60 Hz), low (60-250 Hz), mid (250 Hz-2 kHz), high-mid (2-8 kHz) and air (8-20 kHz), in dB relative to the section's loudest band, measured at the file's native sample rate. Plain flags such as "nothing above 8 kHz", "no sub", "thin low end", "sub-heavy", "mids are scooped", "dull presence range".
5. **Loudness and dynamics.** Peak dBFS, RMS dBFS, integrated LUFS (if pyloudnorm is installed), crest factor, clipped-sample count, level per section with the change from the previous one, and level every 8 bars.
6. **Splice brief.** One entry per gap, each with the gap in plain words, the measurement behind it, a BPM range (project tempo ±2), the key with compatible alternatives (or "any" for unpitched layers), a layer type from Splice's instrument list, candidate tags, and a one-line instruction an AI with no context can follow. Sections with the same label that trigger the same rule share one entry.

The brief comes from fixed rules, applied per section, at most three per section. Missing drums or bass only count as a gap in peak sections, because quieter sections are often deliberate breakdowns:

| rule | condition | layer |
|---|---|---|
| R1 | drums absent in a peak section (within 3 dB of the loudest) | drums |
| R2 | bass absent in a peak section | bass |
| R3 | bass active but no sub (<60 Hz) in a peak section | bass (sub) |
| R4 | nothing or very little above 8 kHz, section within 8 dB of the loudest | percussion |
| R5 | drums only sparse in a peak section | percussion |
| R6 | mids scooped, or neither "other" nor "vocals" is active | chords (loud section) or pads |
| R7 | the next section is 4 dB or more louder | fx (risers into it) |
| R8 | at most one stem active in a section more than 3 dB below the loudest | pads |
| R9 | vocals absent in every section | vocals (one optional, track-level entry) |

Layer types are drawn from: drums, percussion, bass, guitar, keys, strings, vocals, chords, leads, pads, brass-and-woodwinds, synth, fx. The current rules use drums, percussion, bass, chords, pads, fx and vocals.

## Use it with Claude Desktop (MCP)

`trackcard mcp` runs a small MCP server on stdio with one tool, `analyze_track`. Its arguments: `path` (required, absolute path to the WAV), `stems` (default true), `sections` (`auto`, `allin1` or `laplacian`), `include_json` (default false) and `save` (default true: also writes the `.trackcard.md` next to the WAV). It returns the card as text.

Add it to `claude_desktop_config.json`, using the full path to the `trackcard` executable (`which trackcard` shows it):

```json
{
  "mcpServers": {
    "trackcard": {
      "command": "/full/path/to/trackcard",
      "args": ["mcp"]
    }
  }
}
```

Stem separation on a CPU takes minutes. If Claude Desktop gives up waiting, ask it to call `analyze_track` with `"stems": false`, or run `trackcard analyze` in a terminal and paste the card.

## How it works

| what | method | fallback |
|---|---|---|
| audio | soundfile; mono 22.05 kHz copy for beats, key and sections; native-rate copy for spectrum and loudness | clear error for missing, unreadable, too-short or silent files |
| tempo | librosa beat tracking. If the BPM is outside 85-170, beat tracking is re-run with hints at x0.5, x2/3, x1.5 and x2, and the candidate whose beats hit the strongest onsets wins (out-of-range candidates are penalised). BPM is the slope of a line fitted through the beat times. | reported in the card when the octave correction fires |
| off-beat check | if the kick/bass band (<250 Hz) is 1.5x stronger half a beat after the tracked beats, the grid moves by half a beat | reported when it fires |
| bar 1 | if the file starts with sound within 50 ms and a beat sits within a quarter beat of it, that beat is bar 1 | otherwise beat 1 is the grid phase with the strongest kick/bass (<250 Hz) onsets; the card says which rule ran and warns when the two disagree |
| key | harmonic/percussive separation, chroma_cens on the harmonic part, RMS-weighted average, correlation with the 24 Krumhansl-Kessler profiles. If the relative major/minor scores are within 0.05, the louder tonic wins. Camelot code from the result. | tie-break is reported |
| sections | allin1 (labels such as intro, verse, chorus, break, drop) | Laplacian segmentation: beat-synchronous CQT recurrence matrix with path enhancement plus an MFCC timbre path, spectral clustering with k chosen by eigengap in 3-6, sections under 8 s merged into a neighbour. Boundaries snap to the nearest downbeat (within half a bar), then to a 4-bar phrase start (within one bar). |
| stems | Demucs htdemucs. With a GPU: shifts=2, then shifts=0 on out-of-memory, then CPU. Without a GPU: CPU, shifts=0. 10 Hz RMS and peak envelopes per stem. | if PyTorch/Demucs are missing or the model can't load, the card says so and the stem-based rules are skipped |
| loudness | pyloudnorm integrated LUFS | RMS dBFS |

## Limits (read these)

- trackcard measures; it does not hear. Labels from the fallback segmenter are cluster numbers, not musical functions. Energy levels hint at where the drops are, but that is an inference.
- 4/4 is assumed. Tempo changes within a track are not modelled; one BPM is reported.
- The vocals stem on an instrumental track often picks up vocal-like synths and leads. Treat "vocals active" as "something in the vocal range", not proof of a singer.
- Bar numbers depend on the bar-1 rule above. If the bounce starts mid-bar or with a pickup, numbers can be off by a beat or two; the card warns when its two downbeat signals disagree.
- The Splice brief's tags are candidates to try, not a fixed vocabulary.

## Tests

```bash
pip install -e ".[stems,loudness,dev]"
pytest -m "not slow"     # fast suite: synthetic audio only
pytest -m slow           # real Demucs run, plus the end-to-end test
```

The fast suite generates its own audio: click tracks at known tempos (including the half-time case), chord progressions in known keys and modes, and a three-part track with known boundaries and stems. The end-to-end test runs the full pipeline on the first WAV it finds in `tests/fixtures/`. That folder is gitignored, and so is every audio file: put your own bounce there, it will never be committed. With no WAV present the test is skipped and says why.

## Licence and credits

MIT. See `LICENSE`, and `NOTICE` for attributions (Stem MCP's MCP server skeleton and Demucs approach, methods reimplemented from MixMind, Demucs, allin1, librosa, pyloudnorm).
