# Working in music-analyzer

This repo is Krish's Music Brain: `trackcard` (src/) measures audio locally, and `taste/` holds what he and Calvin like,
including the playlist knowledge graph (`taste/graph/`, start with `GENERATION.md`).

## Getting audio and media: use content-watcher

Whenever a task needs audio, video or a post's media that is not already a local file (a YouTube video, a TikTok,
an Instagram reel, a SoundCloud or Spotify-linked track, a DJ set, a live clip), use
[krishanraja/content-watcher](https://github.com/krishanraja/content-watcher) to get it rather than writing a new fetcher:
`content-watch fetch <url>` for the media, `content-watch audio <file> --format wav` for a WAV that trackcard can read.
Its `docs/FALLBACKS.md` lists every route per platform.

- YouTube refuses cloud machines. On a cloud session, try SoundCloud first (`yt-dlp "scsearch5:<artist> <title>"`, accept
  only a hit within 6 s of the Spotify length), then hand the rest to a home computer, where content-watcher's YouTube route
  (yt-dlp with Deno and the bgutil token helper) works. Say plainly which tracks were left for home.
- Measure the full track, never a 30 s preview, when the question is about arrangement: progressive house progresses.
  A preview is a labelled fallback only.
- Never commit audio. Keep it outside the repo and delete it once its `.trackcard.json` exists if disk is tight.

## Provenance

Every taste claim says where it came from: said by Krish or Calvin, measured by trackcard (full track or preview), read from
a metadata source (Spotify, Discogs, label page), or inferred by Claude. Inferred lines say "my read".
