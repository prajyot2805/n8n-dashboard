# Creator Ops dashboard

Live page: https://prajyot2805.github.io/n8n-dashboard/

One page, two tabs:

- **PS5 Shorts** (n8n workflow "PS5 Shorts Pipeline"): what needs you right now, what went out today (Shorts posted, your videos, Shorts made), the next 12 posting slots with the Short that will post in each, the queue by game and days left, your latest videos, Shorts posted per day and this week's top Shorts.
- **Shadow Legion** (n8n workflow "Shadow Legion Reels", Instagram via Metricool): what needs you, today's reel, the next reels, the Metricool budget (20 a month from the 14th), every reel's Instagram numbers and the latest lessons.

## How it works

1. GitHub Actions (`.github/workflows/update.yml`) runs `update_dashboard.py` every 30 minutes.
2. The script reads only the n8n API. The latest runs of each workflow already read the Google Sheets and the Issues data table, so it takes those node outputs (`Read Videos`, `Read Shorts L1`, `Read Issues V`, `Read Sheet`). YouTube view counts come from the public video pages; a failed read keeps the last known number.
3. It writes `status.json` (only the fields the page shows) and commits it. `index.html` is a fixed page that renders `status.json` and re-checks it every 5 minutes.

The posting order shown under "Up next" follows the pipeline's own rules: a booked Short posts in its slot, an unbooked GTA 6 Short takes over a slot booked for another game, and open slots take the best unbooked Short (marked "Likely"). If the pipeline's slots change (`SLOT_JS` in the ps5-shorts repo), update `PS5_SLOTS_MIN` here too; same for `SL_SLOTS` and `SL_BUDGET`.

## Secrets

GitHub repository secrets: `N8N_BASE_URL`, `N8N_API_KEY`. Nothing secret is committed. Run locally with `python3 update_dashboard.py` (reads `~/.config/n8n/api.env`), then `python3 -m http.server` and open http://localhost:8000.
