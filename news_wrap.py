"""
The NWCA Wrap — RSS -> Claude script -> TTS audio -> R2 -> podcast feed (Spotify).

Usage:
  python news_wrap.py build     # fetch articles, write script, generate audio into ./output
  python news_wrap.py publish   # upload audio to R2 and regenerate the podcast feed
  python news_wrap.py all       # build + publish in one go
  python news_wrap.py trailer assets/trailer.mp3   # one-time: add the show trailer to the feed
  python news_wrap.py collect   # save new site articles to data/articles.json (run hourly)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

import boto3
import feedparser
import requests
from anthropic import Anthropic

# ---------- Config (all via environment variables / GitHub secrets) ----------
SITE_RSS_URL = os.environ["SITE_RSS_URL"]            # e.g. https://nwcaonline.com/rss
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs")  # "elevenlabs", "openai" or "kokoro"
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "onyx")
KOKORO_VOICE = os.getenv("KOKORO_VOICE") or "am_michael"  # free, runs locally; see kokoro VOICES.md

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE", "").rstrip("/")  # e.g. https://podcast.nwcaonline.com
R2_PREFIX = os.getenv("R2_PREFIX", "news-wrap").strip("/")

PODCAST = {
    "title": os.getenv("PODCAST_TITLE", "The NWCA Wrap"),
    "author": os.getenv("PODCAST_AUTHOR", "National Wrestling Coaches Association"),
    "email": os.getenv("PODCAST_EMAIL", ""),
    "description": os.getenv(
        "PODCAST_DESCRIPTION",
        "The NWCA Wrap is your weekly rundown of everything happening across the National Wrestling Coaches Association. Every Friday, catch up on the week's top stories, rankings, awards, and announcements from the NWCA, all in under 10 minutes.",
    ),
    "site": os.getenv("PODCAST_SITE", "https://nwcaonline.com"),
    "cover": os.getenv("PODCAST_COVER_URL", ""),  # 3000x3000 JPG/PNG, required by Spotify/Apple
    "category": os.getenv("PODCAST_CATEGORY", "Sports"),
    "subcategory": os.getenv("PODCAST_SUBCATEGORY", "Wrestling"),
}

INTRO_MP3 = Path("assets/intro.mp3")   # optional music bumpers
OUTRO_MP3 = Path("assets/outro.mp3")
OUT = Path("output")
PRONUNCIATIONS = Path("pronunciations.json")  # {"Naasz": "Nahz", ...}


# ---------- 1. Fetch the week's articles ----------
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "America/Chicago"))
CUTOFF_HOUR = int(os.getenv("CUTOFF_HOUR", "14"))  # Friday cutoff, local time (matches the 2pm CT run)
# nwcaonline.com stamps pubDates with Eastern wall-clock time but always labels them "EST",
# even during daylight time, so ignore the label and read the clock time in this zone.
SITE_TZ = ZoneInfo(os.getenv("SITE_TZ", "America/New_York"))


def published_at(entry):
    raw = entry.get("published") or entry.get("updated")
    t = parsedate(raw) if raw else None  # drops the (wrong) zone label
    if t:
        return datetime(*t[:6], tzinfo=SITE_TZ)
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def friday_window(now=None):
    """Return (start, end) covering last Friday CUTOFF_HOUR -> this Friday CUTOFF_HOUR, local time.
    'This Friday' is the most recent Friday cutoff at or before now, so manual runs midweek
    re-cover the last completed Friday-to-Friday week. No gaps, no overlaps between episodes."""
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    days_since_fri = (now.weekday() - 4) % 7  # Monday=0 ... Friday=4
    end = (now - timedelta(days=days_since_fri)).replace(
        hour=CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    if end > now:  # it's Friday but before the cutoff hour
        end -= timedelta(days=7)
    start = end - timedelta(days=7)
    if os.getenv("WINDOW_END_NOW"):  # one-off catch-up run: also take articles posted after the cutoff
        end = now
    return start, end


def published_links():
    """Article links already used in a published episode, so a catch-up run's extra
    articles aren't repeated the following week."""
    if not R2_PUBLIC_BASE:
        return set()
    try:
        r = requests.get(f"{R2_PUBLIC_BASE}/{R2_PREFIX}/episodes.json", timeout=30)
        if r.status_code == 404:
            return set()
        r.raise_for_status()
        return {l["link"] for ep in r.json() for l in ep.get("links", [])}
    except (requests.RequestException, ValueError) as err:
        print(f"WARNING: couldn't read published episodes ({err}); repeats won't be skipped.")
        return set()


# The site's feed only carries its 10 newest items, so a busy week can push early stories out
# before Friday. An hourly `collect` run saves every article it sees here (committed to the repo),
# and the build reads these saved articles alongside the live feed.
SAVED_ARTICLES = Path("data/articles.json")
KEEP_DAYS = 35


def feed_articles():
    articles = []
    for e in feedparser.parse(SITE_RSS_URL).entries:
        published = published_at(e)
        if not published or not e.get("link"):
            continue
        body = e.content[0].value if e.get("content") else e.get("summary", "")
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()[:6000]
        articles.append({"title": e.get("title", ""), "link": e["link"],
                         "published": published.isoformat(), "body": body})
    return articles


def load_saved():
    return json.loads(SAVED_ARTICLES.read_text()) if SAVED_ARTICLES.exists() else []


def collect():
    """Save any new feed articles to SAVED_ARTICLES, dropping ones older than KEEP_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    recent = lambda a: datetime.fromisoformat(a["published"]) >= cutoff  # also skips old pinned posts
    saved = {a["link"]: a for a in load_saved() if recent(a)}
    new = [a for a in feed_articles() if a["link"] not in saved and recent(a)]
    for a in new:
        saved[a["link"]] = a
        print(f"Saved: {a['title']}")
    kept = sorted(saved.values(), key=lambda a: a["published"], reverse=True)
    SAVED_ARTICLES.parent.mkdir(exist_ok=True)
    SAVED_ARTICLES.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(new)} new; {len(kept)} saved articles.")


def fetch_articles():
    start, end = friday_window()
    print(f"Window: {start:%a %b %d %I:%M%p} -> {end:%a %b %d %I:%M%p} {LOCAL_TZ.key}")
    already_aired = published_links()
    pool = {a["link"]: a for a in load_saved()}
    pool.update({a["link"]: a for a in feed_articles()})  # live feed wins for anything edited
    articles = []
    for a in sorted(pool.values(), key=lambda a: a["published"], reverse=True):
        published = datetime.fromisoformat(a["published"])
        if not (start <= published < end):
            continue
        if a["link"] in already_aired:
            print(f"Skipping (already aired): {a['title']}")
            continue
        articles.append({"title": a["title"], "link": a["link"],
                         "date": published.strftime("%B %d"), "body": a["body"]})
    return articles


# ---------- 2. Write the episode script with Claude ----------
SYSTEM_PROMPT = """You write the script for The NWCA Wrap, a short weekly audio podcast
from the National Wrestling Coaches Association. Write ONLY the words the host speaks:
no stage directions, no headings, no markdown, no URLs, no emoji.
- Warm, energetic, conversational broadcaster tone; 5 to 8 minutes spoken (about 750-1100 words).
- Open by naming the show ("This is The NWCA Wrap") with a quick welcome and the date range; close with a sign-off pointing listeners to nwcaonline.com.
- Cover every article, biggest news first, with natural transitions.
- Stick strictly to facts in the articles. Never invent results, quotes, or numbers.
- Write numbers and abbreviations the way they should be spoken (e.g. "Division One", "N-C-A-A")."""


def write_script(articles):
    client = Anthropic()
    start, end = friday_window()
    week_label = f"{start:%B %d} through {end:%B %d, %Y}"
    payload = "\n\n".join(
        f"ARTICLE {i+1}: {a['title']} ({a['date']})\n{a['body']}" for i, a in enumerate(articles)
    )
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"This episode covers {week_label}.\n\n{payload}"}],
    )
    script = "".join(b.text for b in msg.content if b.type == "text").strip()

    # Second short call for episode title + show notes
    meta = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system="Return ONLY JSON: {\"title\": str (under 70 chars), \"summary\": str (2-3 sentences)}. No markdown.",
        messages=[{"role": "user", "content": script}],
    )
    raw = "".join(b.text for b in meta.content if b.type == "text")
    info = json.loads(re.sub(r"```(json)?", "", raw).strip())
    return script, info


def apply_pronunciations(text):
    if PRONUNCIATIONS.exists():
        for word, spoken in json.loads(PRONUNCIATIONS.read_text()).items():
            text = re.sub(rf"\b{re.escape(word)}\b", spoken, text)
    return text


# ---------- 3. Text-to-speech ----------
def chunk_text(text, limit):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 > limit and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


_kokoro = None


def kokoro_chunk(text, path):
    global _kokoro
    import numpy as np
    import soundfile as sf
    if _kokoro is None:
        from kokoro import KPipeline
        _kokoro = KPipeline(lang_code="a")  # American English
    audio = np.concatenate([a for _, _, a in _kokoro(text, voice=KOKORO_VOICE)])
    wav = Path(path).with_suffix(".wav")
    sf.write(wav, audio, 24000)
    subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-ar", "44100", "-ac", "2", "-b:a", "128k", str(path)],
                   check=True, capture_output=True)
    wav.unlink()


def tts_chunk(text, path):
    if TTS_PROVIDER == "kokoro":
        kokoro_chunk(text, path)
        return
    if TTS_PROVIDER == "elevenlabs":
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
            timeout=300,
        )
    else:
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": "gpt-4o-mini-tts", "voice": OPENAI_VOICE, "input": text},
            timeout=300,
        )
    r.raise_for_status()
    Path(path).write_bytes(r.content)


def synthesize(script, out_mp3):
    limit = 4500 if TTS_PROVIDER == "elevenlabs" else 4000
    tmp = Path(tempfile.mkdtemp())
    parts = []
    if INTRO_MP3.exists():
        parts.append(INTRO_MP3.resolve())
    for i, c in enumerate(chunk_text(apply_pronunciations(script), limit)):
        p = tmp / f"part{i:03d}.mp3"
        tts_chunk(c, p)
        parts.append(p)
    if OUTRO_MP3.exists():
        parts.append(OUTRO_MP3.resolve())
    listfile = tmp / "list.txt"
    listfile.write_text("".join(f"file '{p}'\n" for p in parts))
    # Re-encode so intro/voice/outro with different bitrates join cleanly
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                    "-ar", "44100", "-ac", "2", "-b:a", "128k", str(out_mp3)],
                   check=True, capture_output=True)


def duration_seconds(mp3):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(mp3)],
                         capture_output=True, text=True, check=True).stdout
    return int(float(out.strip()))


# ---------- Build stage ----------
def build():
    OUT.mkdir(exist_ok=True)
    articles = fetch_articles()
    if not articles:
        print("No new articles this week — skipping episode.")
        (OUT / "SKIP").write_text("no articles")
        return
    print(f"Found {len(articles)} articles.")
    script, info = write_script(articles)
    (OUT / "script.txt").write_text(script)

    slug = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mp3 = OUT / f"{slug}.mp3"
    synthesize(script, mp3)

    episode = {
        "slug": slug,
        "title": info["title"],
        "summary": info["summary"],
        "links": [{"title": a["title"], "link": a["link"]} for a in articles],
        "pub_date": format_datetime(datetime.now(timezone.utc)),
        "duration": duration_seconds(mp3),
        "bytes": mp3.stat().st_size,
        "file": mp3.name,
    }
    (OUT / "episode.json").write_text(json.dumps(episode, indent=2))
    print(f"Built episode: {episode['title']} ({episode['duration']}s)")


# ---------- 4. Publish stage: R2 + podcast feed ----------
def r2():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def build_feed(episodes):
    feed_url = f"{R2_PUBLIC_BASE}/{R2_PREFIX}/feed.xml"
    p = {k: escape(v) for k, v in PODCAST.items()}
    items = []
    for ep in sorted(episodes, key=lambda e: e["slug"], reverse=True):
        url = f"{R2_PUBLIC_BASE}/{R2_PREFIX}/episodes/{ep['file']}"
        notes = escape(ep["summary"]) + "&lt;br/&gt;&lt;br/&gt;Stories this week:&lt;br/&gt;" + "".join(
            f"&lt;a href=\"{escape(l['link'])}\"&gt;{escape(l['title'])}&lt;/a&gt;&lt;br/&gt;"
            for l in ep["links"])
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{notes}</description>
      <itunes:summary>{escape(ep['summary'])}</itunes:summary>
      <enclosure url="{url}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">nwca-news-wrap-{ep['slug']}</guid>
      <pubDate>{ep['pub_date']}</pubDate>
      <itunes:duration>{ep['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <itunes:episodeType>{ep.get('type', 'full')}</itunes:episodeType>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{p['title']}</title>
    <link>{p['site']}</link>
    <language>en-us</language>
    <description>{p['description']}</description>
    <atom:link href="{feed_url}" rel="self" type="application/rss+xml"/>
    <itunes:author>{p['author']}</itunes:author>
    <itunes:owner><itunes:name>{p['author']}</itunes:name><itunes:email>{p['email']}</itunes:email></itunes:owner>
    <itunes:image href="{p['cover']}"/>
    <itunes:category text="{p['category']}"><itunes:category text="{p['subcategory']}"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
{chr(10).join(items)}
  </channel>
</rss>
"""


def publish():
    if (OUT / "SKIP").exists():
        print("Nothing to publish.")
        return
    ep = json.loads((OUT / "episode.json").read_text())
    s3 = r2()
    s3.upload_file(str(OUT / ep["file"]), R2_BUCKET, f"{R2_PREFIX}/episodes/{ep['file']}",
                   ExtraArgs={"ContentType": "audio/mpeg"})

    index_key = f"{R2_PREFIX}/episodes.json"
    try:
        episodes = json.loads(s3.get_object(Bucket=R2_BUCKET, Key=index_key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        episodes = []
    episodes = [e for e in episodes if e["slug"] != ep["slug"]] + [ep]

    s3.put_object(Bucket=R2_BUCKET, Key=index_key, Body=json.dumps(episodes, indent=2),
                  ContentType="application/json")
    s3.put_object(Bucket=R2_BUCKET, Key=f"{R2_PREFIX}/feed.xml", Body=build_feed(episodes),
                  ContentType="application/rss+xml", CacheControl="max-age=300")
    print(f"Published. Feed: {R2_PUBLIC_BASE}/{R2_PREFIX}/feed.xml")


def publish_trailer(mp3_path):
    """One-time: add the show trailer to the feed (Spotify pins it as the show's preview)."""
    import shutil
    OUT.mkdir(exist_ok=True)
    dst = OUT / "trailer.mp3"
    shutil.copy(mp3_path, dst)
    ep = {
        "slug": "0000-trailer",
        "type": "trailer",
        "title": "Introducing The NWCA Wrap",
        "summary": ("All of the NWCA's weekly news, in one spot. The NWCA Wrap is a quick weekly recap of the "
                    "top stories, rankings, awards, and announcements from the National Wrestling Coaches "
                    "Association. New episodes drop every Friday afternoon. Follow now so you never miss a week, "
                    "and find full stories at nwcaonline.com."),
        "links": [],
        "pub_date": format_datetime(datetime.now(timezone.utc)),
        "duration": duration_seconds(dst),
        "bytes": dst.stat().st_size,
        "file": dst.name,
    }
    (OUT / "episode.json").write_text(json.dumps(ep, indent=2))
    (OUT / "SKIP").unlink(missing_ok=True)
    publish()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "trailer":
        publish_trailer(sys.argv[2])
        sys.exit()
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        collect()
        sys.exit()
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("build", "all"):
        build()
    if stage in ("publish", "all"):
        publish()
