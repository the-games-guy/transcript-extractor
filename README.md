# transcript-extractor

A command-line app that:

- accepts YouTube URLs (watch, `youtu.be`, Shorts, embed, live, or a bare video ID)
- searches YouTube by keyword
- runs **scheduled watches** that look for new videos on a regular basis
- fetches each video's transcript
- saves the transcript as a Markdown file, with timestamps that link back into the video
- summarizes the video with Claude and emails the summary (the `.md` file is attached)

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env              # then fill in your keys
cp watches.example.yaml watches.yaml
```

| Setting | Used for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Summaries | <https://console.anthropic.com/> |
| `YOUTUBE_API_KEY` | Keyword search and watches | Google Cloud Console → enable **YouTube Data API v3** → create an API key |
| `SMTP_*`, `EMAIL_FROM`, `EMAIL_TO` | Sending email | For Gmail: `smtp.gmail.com`, port 587, and an [App Password](https://myaccount.google.com/apppasswords) |

You don't need a YouTube API key to process a URL: transcripts come from
[`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api), and
titles fall back to YouTube's keyless oEmbed endpoint.

## Usage

### Process a URL

```bash
yt-transcript process "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-transcript process https://youtu.be/abc123def45 https://youtu.be/xyz987uvw65   # multiple URLs -> one email
yt-transcript process <url> --no-email          # just save the Markdown (with summary)
yt-transcript process <url> --no-summary --no-email   # transcript only, no API calls
yt-transcript process <url> --to someone@example.com
```

### Search by keyword

```bash
yt-transcript search "rust async tutorial" -n 5 --days 30 --order viewCount
yt-transcript search "rust async tutorial" -n 3 --process   # process every result too
```

### Scheduled watches

Define watches in `watches.yaml` (see `watches.example.yaml` for all options):

```yaml
watches:
  - name: ai-news
    query: "AI news this week"
    every: 6h          # or: cron: "0 7 * * 1" with timezone: America/New_York
    max_results: 5
    lookback: 2d       # only videos published in this window
    order: date
```

```bash
yt-transcript watch list
yt-transcript watch run            # long-running scheduler (Ctrl-C to stop)
yt-transcript watch run --once     # one pass over every watch, then exit
yt-transcript watch run --once --name ai-news
```

Each run searches YouTube, skips videos it has already handled (tracked in
`STATE_FILE`, default `.state/processed.json`), processes the new ones, and sends
**one digest email per watch**. Videos with no captions are recorded and skipped from
then on; network or rate-limit failures are left unrecorded so the next run retries them.

If you'd rather use the system scheduler than keep a process running, call `--once`
from cron:

```cron
0 */6 * * *  cd /path/to/transcript-extractor && .venv/bin/yt-transcript watch run --once >> watch.log 2>&1
```

## Output

Transcripts are saved to `TRANSCRIPTS_DIR` (default `transcripts/`) as
`YYYY-MM-DD_<title-slug>_<video-id>.md`:

```markdown
---
title: "Video title"
video_id: dQw4w9WgXcQ
url: https://www.youtube.com/watch?v=dQw4w9WgXcQ
channel: "Channel"
language: en
auto_generated: true
---

# Video title

## Summary
**TL;DR** ...

## Transcript
**[00:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s)** First minute of speech...
**[01:02](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=62s)** Next minute...
```

## Summaries

Summaries use Claude (`claude-opus-5-5` by default; override with `ANTHROPIC_MODEL`)
with adaptive thinking at medium effort. Each one has a TL;DR, key points, notable
quotes, and a "worth watching?" line. The request opts into the API's server-side
refusal fallback (`fallbacks: "default"`), so if a safety classifier declines a
transcript, the API retries on a recommended model instead of failing. If
summarizing fails anyway, the transcript is still saved and the email says why the
summary is missing.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Notes

- YouTube sometimes blocks transcript requests from cloud/datacenter IPs
  (`RequestBlocked` / `IpBlocked`). Run from a home connection, or configure a proxy
  as described in the `youtube-transcript-api` docs.
- A YouTube Data API search costs 100 quota units; the free daily quota is 10,000
  (about 100 searches a day). Keep that in mind when choosing watch intervals.
