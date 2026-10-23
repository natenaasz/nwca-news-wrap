"""Decide whether this run of the weekly workflow should build an episode.

The workflow is scheduled at three UTC times so a 2:30pm CT run (plus a 3:30pm CT backup) happens
whether Chicago is on CDT or CST. A scheduled run builds only if it's Friday after the 2pm CT cutoff
and no other run this afternoon is still going or has already built the episode. A rejected episode
counts as built, so the backup doesn't rebuild it. Manual runs always build.
Writes go=true|false to $GITHUB_OUTPUT.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
CUTOFF_HOUR = 14


def gh_json(*args):
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def build_conclusion(repo, run_id):
    jobs = gh_json("run", "view", str(run_id), "-R", repo, "--json", "jobs")["jobs"]
    return next((j["conclusion"] for j in jobs if j["name"] == "build"), None)


def decide(now, runs, this_run_id, build_of):
    """runs: dicts with databaseId, status, createdAt. build_of(run_id) -> build job conclusion."""
    local = now.astimezone(CT)
    if local.weekday() != 4 or local.hour < CUTOFF_HOUR:
        return False, f"{local:%a %I:%M%p %Z} is before Friday's 2pm CT cutoff"
    noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
    for r in runs:
        if r["databaseId"] == this_run_id:
            continue
        if datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")) < noon:
            continue
        if r["status"] != "completed":
            return False, f"run {r['databaseId']} is already {r['status']}"
        if build_of(r["databaseId"]) == "success":
            return False, f"run {r['databaseId']} already built this week's episode"
    return True, f"no episode built yet this afternoon ({local:%I:%M%p %Z})"


def main():
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        go, why = True, "manual run"
    else:
        repo = os.environ["GITHUB_REPOSITORY"]
        runs = gh_json("run", "list", "-R", repo, "-w", "weekly-news-wrap.yml", "--limit", "20",
                       "--json", "databaseId,status,createdAt")
        go, why = decide(datetime.now(timezone.utc), runs, int(os.environ["GITHUB_RUN_ID"]),
                         lambda rid: build_conclusion(repo, rid))
    print(f"{'Building' if go else 'Skipping'}: {why}")
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"go={'true' if go else 'false'}\n")


if __name__ == "__main__":
    main()
