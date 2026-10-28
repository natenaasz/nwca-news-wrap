# The NWCA Wrap

Every Friday: website RSS → Claude writes a script → TTS voices it → MP3 + podcast feed on R2 → Spotify picks it up.

## One-time setup

1. **New GitHub repo:** push this folder to it.
2. **R2:** make a public bucket (or reuse nwca-resources with a custom domain), create an R2 API token with read/write on it.
   Upload 3000x3000 cover art and note its URL.
3. **Repo → Settings → Secrets and variables → Actions**
   - Secrets: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` or `OPENAI_API_KEY` (none needed for `kokoro`), `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
   - Variables: `SITE_RSS_URL`, `TTS_PROVIDER` (`elevenlabs`, `openai`, or `kokoro` — free, runs on the runner),
     `ELEVENLABS_VOICE_ID` or `KOKORO_VOICE` (e.g. `am_michael`),
     `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_PUBLIC_BASE`, `PODCAST_EMAIL`, `PODCAST_COVER_URL`
4. **Repo → Settings → Environments → New "podcast"** → add yourself as a required reviewer
   (you'll get an email each Friday to listen and approve). Remove the reviewer later to go fully automatic.
5. **Run once manually** (Actions tab → Weekly News Wrap → Run workflow) so the feed exists.
6. **Spotify for Creators** → "I already have a podcast" → paste `R2_PUBLIC_BASE/news-wrap/feed.xml`
   → verify via the code emailed to `PODCAST_EMAIL`. Done; new episodes auto-appear.

## How articles are gathered
The site's RSS feed only lists its 10 newest posts, so **Collect Articles** runs hourly and saves
every new article to `data/articles.json` (kept 35 days). The Friday build uses those saved articles
plus the live feed, and skips any story that already aired in an earlier episode.

## Optional
- `assets/intro.mp3` / `assets/outro.mp3` get stitched on automatically if present.
- Add names to `pronunciations.json` when the voice mispronounces them.
- Test locally: `python news_wrap.py build` and listen to `output/*.mp3` before publishing anything.
