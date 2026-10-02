# transcript-extractor

Saves YouTube transcripts as Markdown notes into `/srv/yt-transcripts` on CT102.
Native Syncthing shares that folder (Send Only) into the Obsidian vault as
`tokvault/YouTube Transcripts/`, and a Claude routine on the Mac summarises new
notes and emails a digest.

It runs in Docker as a small web app with a login:

- **Transcripts:** paste YouTube links (watch, `youtu.be`, Shorts, live); see what's
  saved, failed, or has no captions, and retry failures
- **Search:** find videos by keyword and save the ones you tick
- **Schedules:** keyword searches that run automatically (every N hours, daily at a
  time, or cron) and save any new videos
- **Claude routine:** builds the prompt for a Claude desktop scheduled task that
  writes summaries into the notes and emails them

The original command line still works:

```bash
docker compose run --rm yt-transcripts https://youtu.be/<id> [<url> ...]   # --force rewrites
```

Deployment on CT102, including upgrading from the command-line-only version:
[docs/ct102-deploy.md](docs/ct102-deploy.md).

## Notes

One note per video, `<title> (<video_id>).md`:

```markdown
---
title: "Video title"
channel: "Channel"
url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
video_id: "dQw4w9WgXcQ"
published: 2026-09-30
language: "en"
auto_generated: true
fetched: 2026-10-02
source: "watch: AI news"
type: youtube-transcript
summary_status: pending
tags: [youtube, transcript]
---

# Video title

![](https://www.youtube.com/watch?v=dQw4w9WgXcQ)

[Channel](https://www.youtube.com/watch?v=dQw4w9WgXcQ) · Published 2026-09-30 · English (auto-generated)

## Summary

<!-- summary:pending -->

## Transcript

[0:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s) First minute of speech…

[1:02](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=62s) Next minute…
```

- The `![](…)` line embeds the video player in Obsidian.
- The Claude routine replaces `<!-- summary:pending -->` (invisible in reading view)
  with the summary and sets `summary_status: done`.
- `type: youtube-transcript` marks the note as untrusted third-party text.
- Notes are written to a `.tmp-*` file and renamed when complete, so Syncthing
  never syncs a half-written note (CT102's `.stignore` ignores `.tmp-*`).
- A video that already has a note, found by the video ID in the file name, is never
  fetched or overwritten again, so summaries and older notes are safe. Only the
  command line's `--force` rewrites one.

## Configuration

Everything is set in `.env` (copy `.env.example`):

| Variable | Purpose |
|---|---|
| `APP_UID` / `APP_GID` | The syncthing user's IDs, so Syncthing can manage the files. |
| `OUTPUT_DIR` | Host folder notes go into (`/srv/yt-transcripts`). |
| `DATA_DIR` | Host folder for the app's database (`/srv/yt-transcripts-data`). Not synced. |
| `BIND_ADDRESS` / `HOST_PORT` | Where the web UI is published: CT102's LAN IP, port 8000. |
| `APP_USERNAME` / `APP_PASSWORD` | Web login. The password is required. |
| `APP_TIMEZONE` | Time zone for schedules, e.g. `Europe/London`. |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key, for Search and Schedules (pasted links don't need it). |
| `OBSIDIAN_VAULT_NAME` / `VAULT_FOLDER` | `tokvault` / `YouTube Transcripts`: for "open in Obsidian" links and the routine prompt. |
| `TRANSCRIPT_LANGUAGES` | Preferred transcript languages, in order (default `en`). |
| `NOTE_TAGS` | Tags on every note (default `youtube,transcript`). |

## Limits

- YouTube sometimes blocks transcript requests (`RequestBlocked` / `IpBlocked`).
  Those show as **Failed** and are retried automatically (up to 5 attempts) or with
  **Retry**. Videos without captions show as **No transcript**.
- Each search costs 100 of the free 10,000 daily YouTube API units. Schedules can't
  run more often than every 15 minutes.
- The UI has a login but no CSRF tokens. Keep it on the LAN (`BIND_ADDRESS`), not
  behind Caddy or cloudflared, unless you add proper authentication in front.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
pytest
```
