// Starts The NWCA Wrap at 2:30pm CT on Fridays, with a 3:30pm CT backup.
// GitHub's own schedule often starts hours late, so this Cloudflare Worker presses
// "Run workflow" on time instead. Uses the same rule as .github/should_build.py:
// skip if a run since noon CT is still going or has already built the episode.
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

export default {
  async scheduled(event, env) {
    const now = new Date(event.scheduledTime);
    const ct = chicago(now);
    const hour = Number(ct.hour);
    if (ct.weekday !== "Fri" || (hour !== 14 && hour !== 15)) {
      console.log(`Skipping: ${ct.weekday} ${ct.hour}:${ct.minute} CT is not 2:30 or 3:30pm Friday`);
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
