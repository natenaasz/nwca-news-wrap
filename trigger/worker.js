// Starts The NWCA Wrap at 2:30pm CT on Fridays, with a 2:45pm CT backup, so the preview email
// arrives before 3pm. GitHub's own schedule often starts hours late, so this Cloudflare Worker
// presses "Run workflow" on time instead. Uses the same rule as .github/should_build.py:
// skip if a run since noon CT is still going or has already built the episode.
//
// It also sends the preview email: the build POSTs {subject, text, html} to /notify with the
// shared NOTIFY_SECRET, and the EMAIL binding (locked to one verified address) delivers it.
const REPO = "natenaasz/nwca-news-wrap";
const WORKFLOW = "weekly-news-wrap.yml";

function chicago(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.map((p) => [p.type, p.value]));
}

async function github(env, path, init = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "nwca-wrap-trigger",
    },
  });
  if (!res.ok) throw new Error(`GitHub ${init.method || "GET"} ${path}: ${res.status} ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

const FROM = { email: "wrap@nwcatech.org", name: "The NWCA Wrap" };
const TO = "nnaasz@nwca.cc";  // must match the EMAIL binding's destination_address

async function notify(request, env) {
  if (request.method !== "POST" || new URL(request.url).pathname !== "/notify") {
    return new Response("Not found", { status: 404 });
  }
  if (!env.NOTIFY_SECRET || request.headers.get("Authorization") !== `Bearer ${env.NOTIFY_SECRET}`) {
    return new Response("Unauthorized", { status: 401 });
  }
  const { subject, text, html } = await request.json();
  if (!subject || !text) return new Response("subject and text are required", { status: 400 });
  try {
    const { messageId } = await env.EMAIL.send({ from: FROM, to: TO, subject, text, html });
    console.log(`Sent "${subject}" (${messageId})`);
    return Response.json({ messageId });
  } catch (e) {
    console.error(`Email failed: ${e.code} ${e.message}`);
    return new Response(`Email failed: ${e.code} ${e.message}`, { status: 502 });
  }
}

export default {
  fetch: notify,

  async scheduled(event, env) {
    const now = new Date(event.scheduledTime);
    const ct = chicago(now);
    const hour = Number(ct.hour);
    if (ct.weekday !== "Fri" || hour !== 14) {
      console.log(`Skipping: ${ct.weekday} ${ct.hour}:${ct.minute} CT is not 2:30 or 2:45pm Friday`);
      return;
    }
    const noon = new Date(now.getTime() - ((hour - 12) * 60 + Number(ct.minute)) * 60_000);
    const { workflow_runs: runs } = await github(env,
      `/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?created=%3E%3D${noon.toISOString()}&per_page=20`);
    for (const run of runs) {
      if (run.status !== "completed") {
        console.log(`Skipping: run ${run.id} is already ${run.status}`);
        return;
      }
      const { jobs } = await github(env, `/repos/${REPO}/actions/runs/${run.id}/jobs`);
      if (jobs.some((j) => j.name === "build" && j.conclusion === "success")) {
        console.log(`Skipping: run ${run.id} already built this week's episode`);
        return;
      }
    }
    await github(env, `/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main" }),
    });
    console.log(`Started The NWCA Wrap at ${ct.hour}:${ct.minute} CT`);
  },
};
