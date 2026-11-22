# Creator Ops dashboard

Live page: https://prajyot2805.github.io/n8n-dashboard/

One page, two tabs:

- **PS5 Shorts** (n8n workflow "PS5 Shorts Pipeline"): what needs you right now, what went out today (Shorts posted, your videos, Shorts made), the next 12 posting slots with the Short that will post in each, the queue by game and days left, your latest videos, Shorts posted per day and this week's top Shorts.
- **Shadow Legion** (n8n workflow "Shadow Legion Reels", Instagram via Metricool): what needs you, today's reel, the next reels, the Metricool budget (20 a month from the 14th), every reel's Instagram numbers and the latest lessons.

## How it works

1. GitHub Actions (`.github/workflows/update.yml`) runs `update_dashboard.py` every 30 minutes.
2. The script reads only the n8n API. The latest runs of each workflow already read the Google Sheets and the Issues data table, so it takes those node outputs (`Read Videos`, `Read Shorts L1`, `Read Issues V`, `Read Sheet`). YouTube view counts come from the public video pages; a failed read keeps the last known number.
3. It writes `status.json` (only the fields the page shows) and commits it. `index.html` is a fixed page that renders `status.json` and re-checks it every 5 minutes.

The posting order shown under "Up next" follows the pipeline's own rules: a booked Short posts in its slot; open slots take two GTA 6 Shorts, then one of another game, at most 2 of one video a day (marked "Likely"). If the pipeline's slots change (`SLOT_JS` in the ps5-shorts repo), update `PS5_SLOTS_MIN` here too; same for `SL_SLOTS` and `SL_BUDGET`.

## Refreshing

GitHub runs the free 30-minute timer only every 6–8 hours, so the page says "data is old" after 90 minutes. **↻ Refresh** re-reads `status.json` straight from the GitHub API (no Pages cache). When the data is over 30 minutes old, **Pull new data on GitHub ↗** opens the workflow's Run workflow button; the page then checks every 20 s for up to 10 minutes and shows the new data when it lands. The page cannot start a run itself: that needs a token, and this page is public.

## Outside alarm

`alarm.py` messages the PS5 Tips bot when n8n does not answer, the PS5 pipeline is off or has not run for 45 min, or no Short was posted for 14 h (30 h on slow days) while some are queued; one message per problem, one when it ends. It runs after every refresh here (so every 3 to 9 h) and every 30 min on the Mac while it is on. State: `alarm.json`. Self-check: `test_alarm.py`.

## Secrets

GitHub repository secrets: `N8N_BASE_URL`, `N8N_API_KEY`, `TIPS_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Nothing secret is committed. Run locally with `python3 update_dashboard.py` (reads `~/.config/n8n/api.env`), then `python3 -m http.server` and open http://localhost:8000.
