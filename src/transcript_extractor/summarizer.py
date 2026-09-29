"""Video summaries with Claude."""

from __future__ import annotations

import anthropic

from .transcripts import Transcript
from .youtube import Video

SYSTEM_PROMPT = """You summarize YouTube videos from their transcripts for a busy reader \
who will read the summary in an email and decide whether to watch the video.

Write in Markdown with these sections:
- **TL;DR** - two or three sentences on what the video is about and its main conclusion.
- **Key points** - five to ten bullets covering the substantive ideas, claims, data, and \
recommendations, in the order they appear. Be specific: keep names, numbers, and examples.
- **Notable quotes** - up to three short verbatim quotes worth remembering (omit the \
section if nothing stands out).
- **Worth watching?** - one sentence on who would benefit from watching the full video.

Transcripts are often auto-generated, so silently correct obvious transcription errors. \
Summarize only what the transcript says; don't add outside facts. Write the summary in \
English even when the transcript is in another language."""


class SummaryError(RuntimeError):
    pass


def summarize(
    video: Video,
    transcript: Transcript,
    *,
    model: str,
    client: anthropic.Anthropic | None = None,
) -> str:
    client = client or anthropic.Anthropic()
    header = "\n".join(
        line
        for line in [
            f"Title: {video.title}" if video.title else "",
            f"Channel: {video.channel}" if video.channel else "",
            f"URL: {video.url}",
        ]
        if line
    )
    user_content = (
        f"{header}\n\n<transcript>\n{transcript.text}\n</transcript>\n\n"
        "Summarize this video."
    )

    # Streaming keeps long transcripts clear of HTTP timeouts. `fallbacks="default"`
    # lets the API retry on a recommended model if a safety classifier declines.
    with client.beta.messages.stream(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise SummaryError("The model declined to summarize this video.")
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise SummaryError(f"Empty summary (stop_reason={message.stop_reason}).")
    return text
