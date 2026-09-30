# transcript-extractor

A small self-hosted web app that saves YouTube transcripts into your Obsidian vault.

- **Transcripts page:** paste one or more YouTube links (watch, `youtu.be`, Shorts, live) to save their transcripts
- **Search:** find videos by keyword, tick the ones you want, and save them
- **Schedules:** keyword searches that run automatically (every N hours, daily at a set time, or a cron expression) and save any videos you don't have yet
- **Claude routine:** builds a prompt for a scheduled task in the Claude desktop app, which summarises the new notes in your vault and emails you a digest

The app does **not** summarise anything itself. Every note it writes is marked
`summary_status: pending`, and your Claude routine picks those up.

```
 server / NAS                         your computer
┌──────────────────────┐   sync    ┌─────────────────────────────────────┐
│ transcript-extractor │ ────────▶ │ Obsidian vault / YouTube/*.md        │
│  (web UI, schedules) │           │        ▲                             │
└──────────────────────┘           │        │ reads + writes summaries    │
                                   │ Claude desktop scheduled task ──────┼──▶ Gmail digest
                                   └─────────────────────────────────────┘
```

## Notes it writes

`<vault>/YouTube/2026-09-30 Video title.md`:

```markdown
---
title: Video title
channel: Channel name
url: https://www.youtube.com/watch?v=dQw4w9WgXcQ
video_id: dQw4w9WgXcQ
published: '2026-09-30'
saved: '2026-09-30T08:00:00Z'
language: en
auto_generated: true
source: 'watch: AI news'
summary_status: pending
tags:
- youtube
- transcript
---

# Video title

![](https://www.youtube.com/watch?v=dQw4w9WgXcQ)

**Channel:** Channel name · **Published:** 2026-09-30 · **Transcript:** English (auto-generated)

## Summary

<!-- summary:pending -->

## Transcript

**[00:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s)** First minute of speech…
**[01:02](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=62s)** Next minute…
```

The `![](…)` line shows the video as an embedded player in Obsidian. The routine
replaces `<!-- summary:pending -->` (invisible in reading view) with the summary and sets
`summary_status: done`, so you can list outstanding notes with a Dataview query or
a Bases filter on that property.

The app remembers every video it has handled in its own database, so moving,
renaming or deleting notes in your vault never causes a video to be re-saved.

## Running it on a server or NAS (Docker)

```bash
git clone https://github.com/the-games-guy/transcript-extractor.git
cd transcript-extractor
cp .env.example .env          # set YOUTUBE_API_KEY, APP_PASSWORD, APP_TIMEZONE, OBSIDIAN_VAULT_NAME
mkdir -p vault data           # create these yourself so they aren't owned by root
docker compose up -d --build
```

Then open `http://<server>:8000` and log in with `APP_USERNAME` / `APP_PASSWORD`.

`docker-compose.yml` mounts two folders:

| Mount | What |
|---|---|
| `./vault` → `/vault` | Where notes are written (inside `NOTES_FOLDER`, default `YouTube`). Point this at the folder you sync to your computer. |
| `./data` → `/data` | The app's SQLite database (schedules, history). |

Set `user:` in the compose file to your NAS user's UID:GID so the files aren't owned by root.

### Without Docker

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install .
cp .env.example .env
transcript-extractor          # serves on HOST:PORT (default 0.0.0.0:8000 from .env)
```

### Settings

| Variable | Default | Purpose |
|---|---|---|
| `YOUTUBE_API_KEY` | — | Needed for Search and Schedules (not for pasted links). Google Cloud Console → enable **YouTube Data API v3** → create an API key. |
| `APP_PASSWORD` | — | Turns on a browser login. **Set this** on anything other than localhost. |
| `APP_USERNAME` | `admin` | Login username. |
| `APP_TIMEZONE` | `UTC` | Time zone for "daily at" / cron schedules and displayed times, e.g. `Europe/London`. |
| `VAULT_DIR` | `./vault` | Folder the app writes into (`/vault` in Docker). |
| `NOTES_FOLDER` | `YouTube` | Subfolder of `VAULT_DIR` for notes. |
| `OBSIDIAN_VAULT_NAME` | folder name | Your vault's name, for "open in Obsidian" links in the UI. |
| `NOTE_TAGS` | `youtube,transcript` | Tags on every note. |
| `TRANSCRIPT_LANGUAGES` | `en` | Preferred transcript languages, in order. Other languages are translated when YouTube allows it. |
| `DATA_DIR` | `./data` | Database location (`/data` in Docker). |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Where the web server listens. |

## Getting notes into your vault

Your vault lives on your computer (Obsidian Sync / iCloud), so the server needs a way to
hand files over. The simplest reliable option is **[Syncthing](https://syncthing.net/)**:

1. Install Syncthing on the NAS (most NAS app stores have it) and on your computer.
2. On the NAS, share the folder mounted at `/vault` (e.g. `./vault/YouTube`).
3. On your computer, accept it into a folder **inside** your vault, e.g. `<vault>/YouTube`.
4. Set the NAS side to **Send Only**. The routine edits notes on your computer to add
   summaries, and Send Only stops those edits being pushed back or overwritten.

If you use Obsidian Sync or iCloud as well, they'll carry the synced notes to your other
devices as usual. Alternatively, if your NAS exposes an SMB share and the computer keeps
it mounted, you can point a folder in your vault at it, but a disconnected share
means missed notes, so Syncthing is more forgiving.

## The Claude routine (summaries + email)

Open the **Claude routine** page in the app. Enter your local vault path and email address,
choose whether to send the email, create a draft, or skip email, and copy the generated
prompt. Then, in the Claude desktop app, create a **scheduled task** with that prompt,
give it access to your vault folder, and connect the **Gmail** connector if you want the
email.

Each run, the task:

1. finds notes with `summary_status: pending`
2. writes a TL;DR, key points, notable quotes and a "worth watching?" line into each note's
   `## Summary` section, and sets `summary_status: done`
3. sends (or drafts) one digest email with all the new summaries

Because it works from `summary_status` rather than dates, nothing is lost if your
computer was asleep: the next run catches up. Desktop scheduled tasks only run while your
computer is on and the Claude app is open.

## Notes and limits

- YouTube often blocks transcript requests from cloud/datacenter IPs (`RequestBlocked` /
  `IpBlocked`). A home NAS is usually fine. Blocked or network failures show as
  **Failed** and are retried automatically (up to 5 attempts) and with the **Retry** button.
  Videos without captions show as **No transcript**.
- Each search costs 100 of the free 10,000 daily YouTube API units (about 100 searches
  a day across Search and Schedules). Schedules can't run more often than every 15 minutes.
- The UI has no per-form CSRF protection. Keep it on your LAN/VPN behind `APP_PASSWORD`
  rather than exposing it to the internet.

## Development

```bash
pip install -e ".[dev]"
pytest
```
