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

## Reviewing an episode
By about 2:35pm CT (2:50pm if the backup run is needed) an email arrives at nnaasz@nwca.cc from
wrap@nwcatech.org with a **Listen to the preview** button, an **Approve or reject** button, the
stories, a social media post and the full script. The preview is an unlisted copy of the MP3 that's
removed once the episode publishes. Approve on the run page with **Review deployments** (signed in
to GitHub as natenaasz). Paste the Spotify episode link into the post where it says `[SPOTIFY LINK]`
once Spotify publishes it. Quiet weeks and failed builds send a short email instead.

The email goes through the `nwca-wrap-trigger` Worker: the build POSTs to `NOTIFY_URL` (repo
variable) with the shared `NOTIFY_SECRET` (repo secret and Worker secret), and the Worker's
`send_email` binding delivers it. To change the recipient, verify the new address in Cloudflare
Email Routing and update `TO` and `destination_address` in `trigger/`.

## When it runs
Every Friday at **2:30pm CT**, with a 2:45pm CT backup. GitHub's own schedule often starts hours
late, so a Cloudflare Worker (`trigger/`, deployed as `nwca-wrap-trigger` on the NWCA Cloudflare
account) presses "Run workflow" on time. It uses a fine-grained GitHub token (`nwca-wrap-trigger`,
Actions read/write on this repo only) stored as the Worker secret `GITHUB_TOKEN`; **renew it before
it expires** with `npx wrangler secret put GITHUB_TOKEN --name nwca-wrap-trigger`. GitHub's schedule
stays on as a fallback and skips itself once an episode has been built.

## Optional
- `assets/intro.mp3` / `assets/outro.mp3` get stitched on automatically if present.
- Add names to `pronunciations.json` when the voice mispronounces them.
- Test locally: `python news_wrap.py build` and listen to `output/*.mp3` before publishing anything.
