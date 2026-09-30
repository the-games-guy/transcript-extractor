# transcript-extractor

Fetches YouTube transcripts and writes them as markdown notes into
`/srv/yt-transcripts` on CT102. Native Syncthing shares that folder
(Send Only) into the Obsidian vault as `tokvault/YouTube Transcripts/`.

```bash
docker compose run --rm yt-transcripts https://youtu.be/<id> [<url> ...]
```

- One note per video: `<title> (<video_id>).md`, with frontmatter and
  minute-by-minute paragraphs that link back to that point in the video.
- Videos that already have a note are skipped (`--force` rewrites them).
- Notes are written to a `.tmp-*` file and renamed when complete, so
  Syncthing never syncs a half-written note.
- `TRANSCRIPT_LANGUAGES` (default `en`) sets preferred languages, in order.

Deployment on CT102: [docs/ct102-deploy.md](docs/ct102-deploy.md).

## Development

```bash
pip install -r requirements-dev.txt
pytest
```
