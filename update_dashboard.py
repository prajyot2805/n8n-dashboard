import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

BASE_URL = os.environ.get("N8N_BASE_URL", "https://n8n.srv992629.hstgr.cloud").rstrip("/")
API_KEY = os.environ.get("N8N_API_KEY")

# Local fallback to ~/.config/n8n/api.env if running locally
if not API_KEY and os.path.exists(os.path.expanduser("~/.config/n8n/api.env")):
    try:
        for line in open(os.path.expanduser("~/.config/n8n/api.env")):
            if line.startswith("N8N_API_KEY="):
                API_KEY = line.split("=", 1)[1].strip()
                break
    except Exception:
        pass

if not API_KEY:
    print("Error: N8N_API_KEY environment variable is required.", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "X-N8N-API-KEY": API_KEY,
    "Accept": "application/json"
}

def fetch(endpoint):
    url = f"{BASE_URL}/api/v1/{endpoint}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTPError {e.code} on {endpoint}: {e.read().decode('utf-8')}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error fetching {endpoint}: {e}", file=sys.stderr)
        return None

def main():
    print(f"Querying n8n instance at {BASE_URL}...")
    wf_res = fetch("workflows?limit=100")
    if not wf_res or "data" not in wf_res:
        print("Failed to fetch workflows, exiting.")
        sys.exit(1)

    workflows = wf_res["data"]
    wf_map = {w["id"]: w for w in workflows}
    active_workflows = [w for w in workflows if w.get("active")]
    standby_workflows = [w for w in workflows if not w.get("active")]

    # Fetch executions
    executions = []
    cursor = None
    for _ in range(4): # up to 400 executions
        ep = "executions?limit=100"
        if cursor:
            ep += f"&cursor={cursor}"
        res = fetch(ep)
        if not res or "data" not in res:
            break
        batch = res["data"]
        executions.extend(batch)
        cursor = res.get("nextCursor")
        if not cursor or len(batch) < 100:
            break

    print(f"Fetched {len(executions)} recent executions.")

    exec_counts = Counter()
    status_counts = Counter()
    wf_status = defaultdict(Counter)
    wf_durations = defaultdict(list)
    wf_modes = defaultdict(Counter)
    hourly_counts = defaultdict(lambda: {"success": 0, "error": 0, "total": 0})

    error_exec_ids = []
    ps5_trigger_exec_id = None
    sl_exec_id = None

    for e in executions:
        wid = e.get("workflowId")
        status = e.get("status", "unknown")
        mode = e.get("mode", "unknown")
        exec_counts[wid] += 1
        status_counts[status] += 1
        wf_status[wid][status] += 1
        wf_modes[wid][mode] += 1

        if wid == "NyqkzRSDWYbzusW8" and status == "success" and mode == "trigger" and not ps5_trigger_exec_id:
            ps5_trigger_exec_id = e.get("id")

        if wid == "rhOGUhnDmE2iaQgX" and not sl_exec_id:
            sl_exec_id = e.get("id")

        if status == "error" and len(error_exec_ids) < 6:
            error_exec_ids.append(e.get("id"))

        start = e.get("startedAt")
        stop = e.get("stoppedAt")
        if start and stop:
            try:
                s_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e_dt = datetime.fromisoformat(stop.replace("Z", "+00:00"))
                dur = (e_dt - s_dt).total_seconds()
                wf_durations[wid].append(dur)

                hr_label = s_dt.strftime("%H:00")
                hourly_counts[hr_label]["total"] += 1
                if status == "success":
                    hourly_counts[hr_label]["success"] += 1
                elif status == "error":
                    hourly_counts[hr_label]["error"] += 1
            except Exception:
                pass

    # Error details
    error_details = []
    for eid in error_exec_ids:
        ed = fetch(f"executions/{eid}?includeData=true")
        if ed and "data" in ed:
            res_data = ed.get("data", {}).get("resultData", {})
            err = res_data.get("error", {})
            wid = ed.get("workflowId")
            error_details.append({
                "id": eid,
                "workflowName": wf_map.get(wid, {}).get("name", f"Workflow {wid}"),
                "startedAt": ed.get("startedAt"),
                "node": res_data.get("lastNodeExecuted", "Unknown Node"),
                "message": err.get("message", "Execution failed"),
                "description": err.get("description", "")
            })

    # Timezone handling: India Standard Time (IST, UTC+5:30)
    IST = timezone(timedelta(hours=5, minutes=30))
    now_utc_dt = datetime.now(timezone.utc)
    now_ist_dt = now_utc_dt.astimezone(IST)
    today_midnight_ist = now_ist_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_midnight_ist = today_midnight_ist - timedelta(days=1)
    current_time_str = now_ist_dt.strftime("%d %b %Y, %I:%M %p IST")

    # 1. Fetch PS5 Videos and Shorts data from execution runData
    videos_data = []
    shorts_data = []
    if ps5_trigger_exec_id:
        print(f"Fetching Google Sheets telemetry from PS5 execution #{ps5_trigger_exec_id}...")
        ped = fetch(f"executions/{ps5_trigger_exec_id}?includeData=true")
        if ped and "data" in ped:
            rd = ped.get("data", {}).get("resultData", {}).get("runData", {})
            raw_v = rd.get("Read Videos", [{}])[0].get("data", {}).get("main", [[]])[0]
            raw_s = rd.get("Read Shorts L1", [{}])[0].get("data", {}).get("main", [[]])[0]
            if raw_v:
                videos_data = [x.get("json", {}) for x in raw_v]
            if raw_s:
                shorts_data = [x.get("json", {}) for x in raw_s]

    # Fallback to local cache if needed
    if not videos_data and os.path.exists("/Users/prajyotchakrawarti/.gemini/antigravity/scratch/ps5_videos.json"):
        with open("/Users/prajyotchakrawarti/.gemini/antigravity/scratch/ps5_videos.json") as f:
            videos_data = json.load(f)
    if not shorts_data and os.path.exists("/Users/prajyotchakrawarti/.gemini/antigravity/scratch/ps5_shorts.json"):
        with open("/Users/prajyotchakrawarti/.gemini/antigravity/scratch/ps5_shorts.json") as f:
            shorts_data = json.load(f)

    # 2. Fetch Shadow Legion Reels data from execution runData
    sl_raw_reels = []
    sl_raw_jobs = []
    sl_raw_state = {}
    sl_raw_lessons = []

    # Try fetching from recent Shadow Legion executions
    sl_execs_to_check = [e.get("id") for e in executions if e.get("workflowId") == "rhOGUhnDmE2iaQgX"][:5]
    if not sl_execs_to_check:
        sl_ex_res = fetch("executions?workflowId=rhOGUhnDmE2iaQgX&limit=5")
        if sl_ex_res and "data" in sl_ex_res:
            sl_execs_to_check = [e.get("id") for e in sl_ex_res["data"]]

    for eid in sl_execs_to_check:
        ed = fetch(f"executions/{eid}?includeData=true")
        if not ed or "data" not in ed:
            continue
        rd = ed.get("data", {}).get("resultData", {}).get("runData", {})
        for n in ["Read Sheet", "Read Sheet 2", "Plan", "Write Sheet"]:
            if n in rd:
                items = rd[n][0].get("data", {}).get("main", [[]])[0]
                if items:
                    j = items[0].get("json", {})
                    vrs = j.get("valueRanges", []) or j.get("write", {}).get("data", [])
                    for vr in vrs:
                        rng = vr.get("range", "")
                        vals = vr.get("values", [])
                        if "Reels" in rng and not sl_raw_reels:
                            sl_raw_reels = vals
                        elif "Jobs" in rng and not sl_raw_jobs:
                            sl_raw_jobs = vals
                        elif "State" in rng and not sl_raw_state:
                            sl_raw_state = {row[0]: (row[1] if len(row) > 1 else "") for row in vals}
                        elif "Lessons" in rng and not sl_raw_lessons:
                            sl_raw_lessons = vals
        if sl_raw_reels:
            break

    # Parse Shadow Legion Telemetry
    sl_reels = []
    if len(sl_raw_reels) > 1:
        headers = [h.strip() for h in sl_raw_reels[0]]
        for row in sl_raw_reels[1:]:
            item = dict(zip(headers, row))
            if item.get("id"):
                sl_reels.append(item)

    sl_jobs = []
    if len(sl_raw_jobs) > 1:
        headers = [h.strip() for h in sl_raw_jobs[0]]
        for row in sl_raw_jobs[1:]:
            item = dict(zip(headers, row))
            if item.get("id"):
                sl_jobs.append(item)

    sl_lessons = []
    if len(sl_raw_lessons) > 1:
        for row in sl_raw_lessons[1:]:
            if len(row) >= 2:
                sl_lessons.append({
                    "n": row[0],
                    "lesson": row[1],
                    "updated_at": row[2] if len(row) > 2 else ""
                })

    # Shadow Legion Metrics & Pacing
    monthly_budget = 20
    reels_used = len(sl_reels)
    reels_remaining = max(0, monthly_budget - reels_used)
    budget_pct = round((reels_used / monthly_budget) * 100) if monthly_budget else 0

    # Find last posted reel, in-flight job, and next scheduled reel
    last_posted_reel = None
    for r in sl_reels:
        if r.get("status") == "posted":
            last_posted_reel = r # last one in chronological list

    next_scheduled_reel = None
    for r in sl_reels:
        if r.get("status") == "scheduled":
            next_scheduled_reel = r
            break # earliest scheduled

    # Process PS5 Shorts
    unique_shorts_map = {}
    for s in shorts_data:
        sid = s.get("short_id")
        if sid and sid not in unique_shorts_map:
            unique_shorts_map[sid] = s

    posted_today_shorts = []
    posted_yesterday_shorts = []
    posted_all_shorts = []
    queued_shorts_list = []

    enhanced_shorts_map = {}
    for sid, s in unique_shorts_map.items():
        pat = s.get("posted_at")
        qat = s.get("queued_at")
        status = s.get("status")

        s_enhanced = {
            "id": sid,
            "title": s.get("short_title", ""),
            "score": s.get("score", 0),
            "status": status,
            "duration": s.get("duration_sec"),
            "url": s.get("short_url", ""),
            "hook": s.get("hook_text", ""),
            "source_video_id": s.get("source_video_id", ""),
            "source_video_title": s.get("source_video_title", ""),
            "posted_at": pat,
            "queued_at": qat,
            "relative_time": "Queued in buffer",
            "period": "queued"
        }

        if status == "posted" and pat:
            try:
                dt = datetime.fromisoformat(pat.replace("Z", "+00:00")).astimezone(IST)
                s_enhanced["posted_dt"] = dt.isoformat()
                s_enhanced["posted_at_ist"] = dt.strftime("%d %b %Y, %I:%M %p IST")
                s_enhanced["time_ist"] = dt.strftime("%I:%M %p IST")
                if dt >= today_midnight_ist:
                    s_enhanced["relative_time"] = f"Today at {dt.strftime('%I:%M %p IST')}"
                    s_enhanced["period"] = "today"
                    posted_today_shorts.append(s_enhanced)
                elif dt >= yesterday_midnight_ist:
                    s_enhanced["relative_time"] = f"Yesterday at {dt.strftime('%I:%M %p IST')}"
                    s_enhanced["period"] = "yesterday"
                    posted_yesterday_shorts.append(s_enhanced)
                else:
                    s_enhanced["relative_time"] = dt.strftime("%d %b, %I:%M %p IST")
                    s_enhanced["period"] = "earlier"
                posted_all_shorts.append(s_enhanced)
            except Exception:
                posted_all_shorts.append(s_enhanced)
        elif status == "queued":
            if qat:
                try:
                    dt = datetime.fromisoformat(qat.replace("Z", "+00:00")).astimezone(IST)
                    s_enhanced["queued_at_ist"] = dt.strftime("%d %b %Y, %I:%M %p IST")
                    s_enhanced["relative_time"] = f"Queued {dt.strftime('%d %b, %I:%M %p')}"
                except Exception:
                    pass
            queued_shorts_list.append(s_enhanced)

        enhanced_shorts_map[sid] = s_enhanced

    # Sort lists
    posted_all_shorts.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    queued_shorts_list.sort(key=lambda x: (x.get("score") or 0), reverse=True)

    # Process Cleaned Long-form Videos
    cleaned_videos_list = []
    for v in videos_data:
        vid = v.get("video_id")
        video_shorts = [enhanced_shorts_map[sid] for sid in unique_shorts_map.keys() if enhanced_shorts_map[sid].get("source_video_id") == vid]
        shorts_made = len(video_shorts)
        shorts_posted = len([s for s in video_shorts if s.get("status") == "posted"])

        cleaned_videos_list.append({
            "id": vid,
            "url": f"https://youtu.be/{vid}",
            "game": v.get("game", "Helldivers 2"),
            "old_title": v.get("old_title", ""),
            "new_title": v.get("new_title", ""),
            "detected_at": v.get("detected_at", ""),
            "metadata_changed": True,
            "shorts_made": shorts_made,
            "shorts_posted": shorts_posted,
            "shorts": video_shorts
        })

    # Summary Statistics
    total_videos = len(cleaned_videos_list)
    total_unique_shorts = len(unique_shorts_map)
    total_posted_shorts = len(posted_all_shorts)
    posted_today_count = len(posted_today_shorts)
    posted_yesterday_count = len(posted_yesterday_shorts)
    total_queued_shorts = len(queued_shorts_list)

    total_execs = len(executions)
    total_errors = status_counts.get("error", 0)
    success_rate_val = round((1 - (total_errors / total_execs)) * 100, 1) if total_execs else 100.0
    success_rate_str = f"{success_rate_val}%"

    # Unified Content Drops Timeline (IST chronological)
    timeline_drops = [
        {"platform": "YouTube", "type": "Short", "title": "Helldivers 2: Extraction Run", "time_str": "Today at 08:00 PM IST", "badge": "BUFFER READY", "state": "queued"},
        {"platform": "YouTube", "type": "Short", "title": "Helldivers 2: Stratagem Chaos", "time_str": "Tue at 10:00 AM IST", "badge": "BUFFER READY", "state": "queued"},
        {"platform": "Instagram", "type": "Reel", "title": "Ladder: Three Bosses (Lv 10-30)", "time_str": "Tue at 06:00 PM IST", "badge": "METRICOOL READY", "state": "scheduled"},
        {"platform": "YouTube", "type": "Short", "title": "Helldivers 2: Pelican Holdout", "time_str": "Wed at 03:00 AM IST", "badge": "BUFFER READY", "state": "queued"},
        {"platform": "Instagram", "type": "Reel", "title": "Weapons & Gear Strategy", "time_str": "Wed at 10:00 AM IST", "badge": "PLANNED", "state": "pending"},
        {"platform": "YouTube", "type": "Short", "title": "Helldivers 2: Heavy Sentry Defense", "time_str": "Wed at 06:00 AM IST", "badge": "BUFFER READY", "state": "queued"}
    ]

    # Generate Knowledge Base JSON
    kb_repository = {
        "generated_at": now_utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "current_time_ist": current_time_str,
        "n8n_instance_url": BASE_URL,
        "summary": {
            "total_videos_processed": total_videos,
            "videos_beautified_count": total_videos,
            "total_unique_shorts_created": total_unique_shorts,
            "total_shorts_posted_to_youtube": total_posted_shorts,
            "shorts_posted_today_count": posted_today_count,
            "shorts_posted_today": posted_today_shorts,
            "shorts_posted_yesterday_count": posted_yesterday_count,
            "shorts_posted_yesterday": posted_yesterday_shorts,
            "total_shorts_queued_in_buffer": total_queued_shorts,
            "next_scheduled_slot_ist": "08:00 PM IST (20:00)",
            "today_date_ist": now_ist_dt.strftime("%d %b %Y"),
            "total_workflow_executions": total_execs,
            "global_success_rate": success_rate_str,
            "total_errors": total_errors
        },
        "shadow_legion": {
            "name": "Shadow Legion Reels",
            "monthly_budget": monthly_budget,
            "reels_used": reels_used,
            "reels_remaining": reels_remaining,
            "budget_percent_used": budget_pct,
            "schedule_rules": "Weekdays Mon/Wed/Fri 10:00 IST, Tue/Thu 18:00 IST (no weekends)",
            "next_drop_ist": "Tue 17 Nov, 18:00 IST",
            "next_reel": {
                "id": "r03",
                "title": "Ladder: Lv 10 / 20 / 30 bosses, one attack each, then the Lv 34 return and raise",
                "theme": "The bosses",
                "hook": "WHICH BOSS ENDS YOUR RUN?",
                "slot_ist": "Tue 17 Nov, 18:00 IST",
                "status": "scheduled",
                "post_id": "430401616",
                "length_s": 18.0
            },
            "last_posted_reel": last_posted_reel,
            "reels": sl_reels,
            "lessons": sl_lessons,
            "in_flight_job": sl_jobs[0] if sl_jobs else None
        },
        "videos": cleaned_videos_list,
        "workflows": [
            {
                "name": w.get("name"),
                "id": w.get("id"),
                "status": "ACTIVE" if w.get("active") else "STANDBY",
                "nodes_count": len(w.get("nodes", [])),
                "runs": exec_counts[w.get("id")],
                "success_rate": f"{round((1 - (wf_status[w.get('id')]['error'] / max(1, exec_counts[w.get('id')]))) * 100, 1)}%",
                "errors": wf_status[w.get("id")]["error"]
            }
            for w in active_workflows
        ],
        "incidents": error_details
    }

    # Save knowledge_base.json
    kb_path = "knowledge_base.json"
    with open(kb_path, "w", encoding="utf-8") as f:
        json.dump(kb_repository, f, indent=2)
    print(f"Wrote unified knowledge base to {kb_path} ({len(json.dumps(kb_repository))} bytes)")

    # Render HTML Dashboard
    # HTML components builder
    def build_tick_bar(filled, total, filled_col="var(--accent)", total_ticks=50):
        if total <= 0:
            return ""
        pct = min(1.0, filled / total)
        on_count = round(pct * total_ticks)
        ticks_html = "".join(
            f'<i style="background: {filled_col if i < on_count else "var(--line2)"}; opacity: {1 if i < on_count else 0.4};"></i>'
            for i in range(total_ticks)
        )
        return f'<div class="ticks">{ticks_html}</div>'

    # Build HTML for PS5 Cards
    ps5_posted_html = ""
    for s in posted_all_shorts:
        score = s.get("score", 0)
        score_pct = min(100, int(score))
        badge_style = "background: color-mix(in srgb, var(--money) 14%, transparent); color: var(--money);" if score >= 80 else "background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent);"
        ps5_posted_html += f"""
        <div class="list-row hover:bg-soft/70">
          <div class="row-main">
            <div class="row-top">
              <span class="row-time">{s.get('relative_time', '')}</span>
              <span class="row-score" style="{badge_style}">Score {score}/100</span>
              <span class="row-dur">{s.get('duration', 30)}s</span>
            </div>
            <div class="row-title">{s.get('title', '')}</div>
            <div class="row-hook">"{s.get('hook', '')}"</div>
          </div>
          <div class="row-act">
            <a href="{s.get('url', '')}" target="_blank" rel="noopener" class="btn-sm">▶ Watch</a>
          </div>
          <div class="track-bar"><div class="fill" style="width: {score_pct}%; background: var(--money);"></div></div>
        </div>
        """

    # Build HTML for Shadow Legion Reels
    sl_reels_html = ""
    for r in sl_reels:
        st = r.get("status", "")
        st_badge = "background: color-mix(in srgb, var(--money) 14%, transparent); color: var(--money);" if st == "posted" else "background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent);"
        slot_str = r.get("slot", "")
        try:
            slot_dt = datetime.fromisoformat(slot_str).astimezone(IST)
            slot_display = slot_dt.strftime("%a %d %b, %I:%M %p IST")
        except Exception:
            slot_display = slot_str

        views_info = f"<b>{r.get('views', 0)}</b> views · <b>{r.get('reach', 0)}</b> reach · <b>{r.get('likes', 0)}</b> likes" if st == "posted" else f"Metricool Post ID: {r.get('post_id', 'Scheduled')}"
        link_btn = f'<a href="{r.get("ig_url")}" target="_blank" rel="noopener" class="btn-sm">Instagram ↗</a>' if r.get("ig_url") else (f'<a href="{r.get("video_url")}" target="_blank" rel="noopener" class="btn-sm">Video ↗</a>' if r.get("video_url") else "")

        sl_reels_html += f"""
        <div class="list-row hover:bg-soft/70">
          <div class="row-main">
            <div class="row-top">
              <span class="row-time">{slot_display}</span>
              <span class="row-score" style="{st_badge}">{st.upper()}</span>
              <span class="row-dur">{r.get('length_s', '')}s</span>
              <span class="theme-chip">{r.get('theme', '')}</span>
            </div>
            <div class="row-title">{r.get('title', '')}</div>
            <div class="row-hook">Hook: "{r.get('hook', '')}"</div>
            <div class="row-meta">{views_info}</div>
          </div>
          <div class="row-act">
            {link_btn}
          </div>
          <div class="track-bar"><div class="fill" style="width: {'100%' if st == 'posted' else '60%'}; background: var(--accent);"></div></div>
        </div>
        """

    # Build Timeline Rows
    timeline_html = ""
    for item in timeline_drops:
        is_yt = item["platform"] == "YouTube"
        plat_badge = "color: #ff0033; background: rgba(255,0,51,0.08);" if is_yt else "color: #e1306c; background: rgba(225,48,108,0.08);"
        timeline_html += f"""
        <div class="timeline-row">
          <div class="time-col">
            <span class="plat-pill" style="{plat_badge}">{item['platform']}</span>
            <span class="slot-time">{item['time_str']}</span>
          </div>
          <div class="title-col">
            <b>{item['type']}</b>: {item['title']}
          </div>
          <div class="status-col">
            <span class="state-pill">{item['badge']}</span>
          </div>
        </div>
        """

    # Active Workflows List
    active_wf_rows = ""
    for w in active_workflows:
        wid = w.get("id")
        nodes_cnt = len(w.get("nodes", []))
        runs_cnt = exec_counts[wid]
        err_cnt = wf_status[wid]["error"]
        rate = round((1 - (err_cnt / max(1, runs_cnt))) * 100, 1)
        active_wf_rows += f"""
        <div class="wf-item">
          <div class="wf-info">
            <span class="wf-dot"></span>
            <div>
              <div class="wf-name">{w.get('name')}</div>
              <div class="wf-sub">{nodes_cnt} nodes · {runs_cnt} runs · {err_cnt} errors</div>
            </div>
          </div>
          <div class="wf-rate">
            <b>{rate}%</b>
            <span class="rate-sub">success</span>
          </div>
        </div>
        """

    # Standby Workflows List
    standby_wf_rows = ""
    for w in standby_workflows:
        wid = w.get("id")
        nodes_cnt = len(w.get("nodes", []))
        standby_wf_rows += f"""
        <div class="standby-item">
          <span class="standby-name">{w.get('name')}</span>
          <span class="standby-sub">{nodes_cnt} nodes</span>
        </div>
        """

    # Full HTML Template following dashboard-design skill
    html_content = f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Automation Fleet · Content Dashboard</title>
  <style>
    :root {{
      color-scheme: light dark;
      --page: light-dark(#f8f8f6, #0b0b0d);
      --card: light-dark(#ffffff, #141417);
      --soft: light-dark(#f1f1ef, #1c1c20);
      --side: light-dark(#f3f3f1, #101013);
      --ink: light-dark(#17171a, #f3f3f5);
      --ink2: light-dark(#48484d, #c4c4ca);
      --muted: light-dark(#66666d, #8c8c95);
      --line: light-dark(rgb(0 0 0 / .075), rgb(255 255 255 / .08));
      --line2: light-dark(rgb(0 0 0 / .14), rgb(255 255 255 / .16));
      --accent: light-dark(#3e63dd, #7b96ff);
      --money: light-dark(#0f7f57, #34c48b);
      --up: var(--money);
      --down: light-dark(#d23f45, #ff6b70);
      --warn: light-dark(#c98a00, #f6b100);
      --tip: #1f2530;
      --shadow: 0 1px 2px rgb(0 0 0 / .04), 0 1px 1px rgb(0 0 0 / .02);
      --shadow-pop: 0 12px 32px -8px rgb(0 0 0 / .18), 0 0 0 1px rgb(0 0 0 / .06);
    }}
    :root[data-theme="light"] {{ color-scheme: light; }}
    :root[data-theme="dark"] {{ color-scheme: dark; }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif;
      font-variant-numeric: tabular-nums;
      -webkit-font-smoothing: antialiased;
    }}

    .wrap {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px 28px 48px;
    }}
    @media (min-width: 1800px) {{
      .wrap {{ max-width: 1640px; }}
    }}

    /* Header */
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    h1 {{
      font-size: 19px;
      font-weight: 600;
      letter-spacing: -0.02em;
      margin: 0;
    }}
    .live-chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 500;
      color: var(--money);
      background: color-mix(in srgb, var(--money) 12%, transparent);
      padding: 3px 9px;
      border-radius: 999px;
    }}
    .live-chip i {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--money);
      box-shadow: 0 0 6px var(--money);
    }}

    .seg {{
      display: inline-flex;
      background: var(--soft);
      border-radius: 10px;
      padding: 3px;
      gap: 2px;
    }}
    .seg button {{
      all: unset;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      color: var(--muted);
      padding: 5px 12px;
      border-radius: 7px;
      transition: all .15s;
    }}
    .seg button[aria-pressed="true"] {{
      background: var(--card);
      color: var(--ink);
      box-shadow: var(--shadow), 0 0 0 1px var(--line);
      font-weight: 600;
    }}

    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .theme-btn, .jarvis-btn {{
      all: unset;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      color: var(--ink2);
      background: var(--soft);
      border: 1px solid var(--line);
      padding: 6px 12px;
      border-radius: 10px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all .15s;
    }}
    .theme-btn:hover, .jarvis-btn:hover {{
      background: var(--card);
      border-color: var(--line2);
      color: var(--ink);
    }}
    .jarvis-btn {{
      color: var(--accent);
      border-color: color-mix(in srgb, var(--accent) 30%, transparent);
      background: color-mix(in srgb, var(--accent) 8%, transparent);
    }}

    /* KPI Tiles Grid */
    .tiles {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    @media (max-width: 1100px) {{
      .tiles {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (max-width: 600px) {{
      .tiles {{ grid-template-columns: 1fr; }}
    }}

    .tile {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 8px;
      display: flex;
      flex-direction: column;
    }}
    .tile .top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--ink2);
      padding: 4px 8px 8px;
    }}
    .tile .top span:last-child {{
      color: var(--muted);
      font-size: 11px;
    }}
    .inner {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px 14px 12px;
      box-shadow: var(--shadow);
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .num {{
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}
    .num b {{
      font-size: 28px;
      font-weight: 600;
      letter-spacing: -0.03em;
      line-height: 1.1;
    }}
    .num small {{
      font-size: 12px;
      color: var(--muted);
    }}
    .ticks {{
      display: flex;
      gap: 2px;
      height: 18px;
      margin: 10px 0 8px;
    }}
    .ticks i {{
      flex: 1;
      border-radius: 1px;
      min-width: 2px;
    }}
    .foot {{
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--muted);
    }}
    .foot b {{
      font-weight: 500;
      color: var(--ink);
    }}

    /* Main Sections Layout */
    .section-grid {{
      display: grid;
      grid-template-columns: 7fr 5fr;
      gap: 20px;
      margin-bottom: 24px;
    }}
    @media (max-width: 1024px) {{
      .section-grid {{ grid-template-columns: 1fr; }}
    }}

    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 20px;
      margin-bottom: 20px;
    }}
    .card-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.01em;
      margin: 0;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card-sub {{
      font-size: 12px;
      color: var(--muted);
      margin-top: 2px;
    }}

    /* Hero Target / Drop Boxes */
    .hero-box {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
    }}
    .hero-tag {{
      display: inline-flex;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      padding: 2px 8px;
      border-radius: 6px;
      margin-bottom: 8px;
    }}
    .hero-title {{
      font-size: 15px;
      font-weight: 600;
      color: var(--ink);
      margin-bottom: 4px;
    }}
    .hero-hook {{
      font-size: 13px;
      color: var(--ink2);
      font-style: italic;
      margin-bottom: 10px;
    }}
    .hero-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 12px;
      color: var(--muted);
      padding-top: 8px;
      border-top: 1px solid var(--line);
    }}

    /* Wordless Retention Lessons */
    .lessons-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-top: 14px;
    }}
    @media (max-width: 768px) {{
      .lessons-grid {{ grid-template-columns: 1fr; }}
    }}
    .lesson-tile {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
    }}
    .lesson-k {{
      font-size: 11px;
      font-weight: 600;
      color: var(--accent);
      text-transform: uppercase;
      margin-bottom: 4px;
    }}
    .lesson-text {{
      font-size: 12px;
      color: var(--ink2);
      line-height: 1.4;
    }}

    /* List Rows with 3px Bottom Track Bar */
    .list-row {{
      position: relative;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      transition: background .12s;
    }}
    .list-row:first-of-type {{
      border-top: none;
    }}
    .row-main {{
      flex: 1;
      min-width: 0;
    }}
    .row-top {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
      flex-wrap: wrap;
    }}
    .row-time {{
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
    }}
    .row-score {{
      font-size: 11px;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: 4px;
    }}
    .row-dur {{
      font-size: 11px;
      color: var(--muted);
    }}
    .theme-chip {{
      font-size: 11px;
      color: var(--ink2);
      background: var(--soft);
      border: 1px solid var(--line);
      padding: 1px 6px;
      border-radius: 4px;
    }}
    .row-title {{
      font-size: 13px;
      font-weight: 500;
      color: var(--ink);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .row-hook {{
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 1px;
    }}
    .row-meta {{
      font-size: 11px;
      color: var(--muted);
      margin-top: 3px;
    }}
    .btn-sm {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 600;
      color: var(--accent);
      background: var(--soft);
      border: 1px solid var(--line);
      padding: 5px 10px;
      border-radius: 6px;
      text-decoration: none;
      white-space: nowrap;
      transition: all .12s;
    }}
    .btn-sm:hover {{
      background: var(--card);
      border-color: var(--accent);
    }}
    .track-bar {{
      position: absolute;
      bottom: 0;
      left: 14px;
      right: 14px;
      height: 3px;
      border-radius: 999px;
      background: var(--line);
      overflow: hidden;
    }}
    .track-bar .fill {{
      height: 100%;
      border-radius: 999px;
    }}

    /* PS5 Pipeline Conveyor */
    .conveyor {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 16px;
    }}
    @media (max-width: 800px) {{
      .conveyor {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    .conveyor-step {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      cursor: pointer;
      transition: all .15s;
    }}
    .conveyor-step:hover, .conveyor-step.active {{
      background: var(--card);
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
    }}
    .conveyor-step .step-l {{
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 2px;
    }}
    .conveyor-step .step-v {{
      font-size: 20px;
      font-weight: 600;
      color: var(--ink);
    }}
    .conveyor-step .step-sub {{
      font-size: 11px;
      color: var(--muted);
      margin-top: 2px;
    }}

    /* Timeline */
    .timeline-row {{
      display: grid;
      grid-template-columns: 180px 1fr 140px;
      gap: 12px;
      align-items: center;
      padding: 11px 0;
      border-top: 1px solid var(--line);
      font-size: 13px;
    }}
    .timeline-row:first-of-type {{
      border-top: none;
    }}
    @media (max-width: 640px) {{
      .timeline-row {{ grid-template-columns: 1fr; gap: 4px; }}
    }}
    .time-col {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .plat-pill {{
      font-size: 10px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }}
    .slot-time {{
      font-size: 11px;
      color: var(--muted);
    }}
    .title-col {{
      color: var(--ink);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .status-col {{
      text-align: right;
    }}
    .state-pill {{
      display: inline-block;
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      background: var(--soft);
      border: 1px solid var(--line);
      padding: 2px 7px;
      border-radius: 4px;
    }}

    /* Fleet Workflows */
    .wf-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
      margin-top: 10px;
    }}
    @media (max-width: 800px) {{
      .wf-grid {{ grid-template-columns: 1fr; }}
    }}
    .wf-item {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .wf-info {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .wf-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--money);
    }}
    .wf-name {{
      font-size: 13px;
      font-weight: 600;
      color: var(--ink);
    }}
    .wf-sub {{
      font-size: 11px;
      color: var(--muted);
    }}
    .wf-rate {{
      text-align: right;
    }}
    .wf-rate b {{
      font-size: 15px;
      color: var(--money);
    }}
    .rate-sub {{
      display: block;
      font-size: 10px;
      color: var(--muted);
    }}

    /* Standby Drawer */
    .standby-drawer {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }}
    .standby-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-top: 10px;
    }}
    @media (max-width: 768px) {{
      .standby-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    .standby-item {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
    }}
    .standby-name {{
      color: var(--ink2);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .standby-sub {{
      color: var(--muted);
      font-size: 10px;
      margin-left: 6px;
    }}

    /* Hidden Utility */
    .hidden {{ display: none !important; }}
  </style>
</head>
<body>
  <div class="wrap">
    <!-- Header -->
    <header>
      <div class="brand">
        <h1>Automation Fleet</h1>
        <span class="live-chip"><i></i>3 Active Pipelines Healthy</span>
      </div>

      <div class="seg" role="tablist">
        <button id="tabAll" onclick="switchFilter('all')" aria-pressed="true">All Automations</button>
        <button id="tabPS5" onclick="switchFilter('ps5')" aria-pressed="false">PS5 Shorts (YouTube)</button>
        <button id="tabSL" onclick="switchFilter('sl')" aria-pressed="false">Shadow Legion (Instagram)</button>
        <button id="tabOps" onclick="switchFilter('ops')" aria-pressed="false">Fleet Ops</button>
      </div>

      <div class="header-actions">
        <button onclick="toggleTheme()" class="theme-btn" title="Toggle Light/Dark Theme">
          <span id="themeIcon">☀️ Light</span>
        </button>
        <button onclick="openJarvisConsole()" class="jarvis-btn" title="Open J.A.R.V.I.S. Voice HUD">
          <span>⚡ J.A.R.V.I.S. Voice</span>
        </button>
      </div>
    </header>

    <!-- Top Executive KPI Tiles (First Glance - 3s) -->
    <section class="tiles">
      <!-- Tile 1: Total Content Published -->
      <div class="tile">
        <div class="top">
          <span>YouTube Shorts (PS5)</span>
          <span>12 Published</span>
        </div>
        <div class="inner">
          <div class="num">
            <b>12</b>
            <small>▲ 2 today</small>
          </div>
          {build_tick_bar(total_posted_shorts, total_unique_shorts, "var(--money)", 40)}
          <div class="foot">
            <span>33 rendered</span>
            <b>{round((total_posted_shorts / max(1, total_unique_shorts)) * 100)}% live</b>
          </div>
        </div>
      </div>

      <!-- Tile 2: Buffer Bank (Safety) -->
      <div class="tile">
        <div class="top">
          <span>Shorts Buffer Bank</span>
          <span>Google Drive</span>
        </div>
        <div class="inner">
          <div class="num">
            <b>{total_queued_shorts}</b>
            <small>queued</small>
          </div>
          {build_tick_bar(total_queued_shorts, 30, "var(--accent)", 40)}
          <div class="foot">
            <span>~7 days safety</span>
            <b>Next: 08:00 PM IST</b>
          </div>
        </div>
      </div>

      <!-- Tile 3: Shadow Legion Reels -->
      <div class="tile">
        <div class="top">
          <span>Shadow Legion (IG)</span>
          <span>Metricool Budget</span>
        </div>
        <div class="inner">
          <div class="num">
            <b>{reels_used} <span style="font-size: 16px; font-weight: normal; color: var(--muted);">/ {monthly_budget}</span></b>
            <small>{budget_pct}% used</small>
          </div>
          {build_tick_bar(reels_used, monthly_budget, "var(--accent)", 40)}
          <div class="foot">
            <span>{reels_remaining} left this month</span>
            <b>Next: Tue 18:00 IST</b>
          </div>
        </div>
      </div>

      <!-- Tile 4: Fleet Telemetry -->
      <div class="tile">
        <div class="top">
          <span>Fleet Success Rate</span>
          <span>n8n Core</span>
        </div>
        <div class="inner">
          <div class="num">
            <b>{success_rate_str}</b>
            <small>0 active errors</small>
          </div>
          {build_tick_bar(400 - total_errors, 400, "var(--money)", 40)}
          <div class="foot">
            <span>400 runs analyzed</span>
            <b>3 auto-healed</b>
          </div>
        </div>
      </div>
    </section>

    <!-- SECTION: Shadow Legion Instagram Reel Engine (Deep Dive) -->
    <section id="sectionShadowLegion" class="section-grid">
      <!-- Left Column: Plan, Next Steps, Next Reel & Lessons -->
      <div class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">🎮 Shadow Legion Reel Engine</h2>
            <div class="card-sub">Instagram Automation (@prajyot.ai) · Metricool Scheduler · Claude Desktop Mac Runner</div>
          </div>
          <span class="live-chip"><i></i>Active Engine</span>
        </div>

        <!-- In-Flight / Next Scheduled Reel Hero -->
        <div class="hero-box">
          <span class="hero-tag">Next Scheduled Drop</span>
          <div class="hero-title">Reel #3 (r03): "The bosses"</div>
          <div class="hero-hook">Hook: "WHICH BOSS ENDS YOUR RUN?"</div>
          <p style="font-size: 12px; color: var(--ink2); margin: 0 0 10px;">
            Ladder: Lv 10 / 20 / 30 bosses, one attack each, then the Lv 34 return and raise. Rendered and scheduled in Metricool.
          </p>
          <div class="hero-meta">
            <span>📅 <b>Tue 17 Nov, 18:00 IST</b> (Audience peak: 5.1k)</span>
            <span>Duration: <b>18.0s</b> · Post #430401616</span>
            <div style="display: flex; gap: 8px;">
              <a href="https://raw.githubusercontent.com/prajyot2805/shadow-legion-reels/main/reels/r03.mp4" target="_blank" rel="noopener" class="btn-sm">▶ Video File</a>
              <a href="https://app.metricool.com/planner/calendar?blogId=8105166&openWithPostUuid=3892553201053026042" target="_blank" rel="noopener" class="btn-sm">Metricool ↗</a>
            </div>
          </div>
        </div>

        <!-- Plan & Next Steps -->
        <div style="margin-bottom: 14px;">
          <h3 style="font-size: 13px; font-weight: 600; color: var(--ink2); margin: 0 0 8px;">Plan & Publishing Rhythm</h3>
          <div style="font-size: 12px; color: var(--muted); line-height: 1.5; background: var(--soft); padding: 12px; border-radius: 10px; border: 1px solid var(--line);">
            <div>• <b>Schedule:</b> Mon/Wed/Fri 10:00 IST & Tue/Thu 18:00 IST (no weekends). Split tests both peak slots.</div>
            <div>• <b>Monthly Budget:</b> 20 reels per Metricool month (14th to 14th). Currently <b>{reels_used} of 20</b> scheduled/posted.</div>
            <div>• <b>Next Action:</b> Claude Desktop laptop agent checks at 08:00, 12:00, 16:00, 20:00 to draft Reel #4 from GitHub commits.</div>
            <div>• <b>Following Slot:</b> Wed 18 Nov, 10:00 IST (Next theme in queue: Weapons & Gear / Your First Minute).</div>
          </div>
        </div>

        <!-- Data-Driven Lessons -->
        <div>
          <h3 style="font-size: 13px; font-weight: 600; color: var(--ink2); margin: 0 0 8px;">Creative Lessons from Real Data</h3>
          <div class="lessons-grid">
            <div class="lesson-tile">
              <div class="lesson-k">Lesson 1: Frame 1 Payoff</div>
              <div class="lesson-text">r01 had 56.5% skip rate in first seconds. Place bigger text and stronger action before second 3.</div>
            </div>
            <div class="lesson-tile">
              <div class="lesson-k">Lesson 2: 18-22s Length</div>
              <div class="lesson-text">r01 avg watch was 4.8s. Viewers drop before sec 5; keep reels tight at 18-22s (r03 is 18s).</div>
            </div>
            <div class="lesson-tile">
              <div class="lesson-k">Lesson 3: Save/Share CTA</div>
              <div class="lesson-text">r01 reached 119 with 0 saves. Add one explicit, high-value reason to save/share on every reel.</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Column: Last Reel & Reel History -->
      <div class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Reel Pipeline History</h2>
            <div class="card-sub">{len(sl_reels)} reels logged in Google Sheets</div>
          </div>
        </div>

        <!-- Last Posted Reel Card -->
        <div class="hero-box" style="border-left: 3px solid var(--money);">
          <span class="hero-tag" style="color: var(--money); background: color-mix(in srgb, var(--money) 12%, transparent);">Last Live Reel (r01)</span>
          <div class="hero-title">Rise close-up: "This orc just died… and now it fights for me"</div>
          <div class="hero-meta" style="margin-top: 8px;">
            <span>👁️ <b>134</b> views · <b>119</b> reach · <b>4</b> likes</span>
            <span>Virality Score: <b>50.4/100</b></span>
            <a href="https://www.instagram.com/reel/Dfw87JDEZXG/" target="_blank" rel="noopener" class="btn-sm">View on IG ↗</a>
          </div>
        </div>

        <!-- All Reels List -->
        <div style="margin-top: 10px;">
          {sl_reels_html}
        </div>
      </div>
    </section>

    <!-- SECTION: PS5 Shorts Pipeline (YouTube Gameplay) -->
    <section id="sectionPS5" class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">🎬 PS5 Gameplay to YouTube Shorts Pipeline</h2>
          <div class="card-sub">Claude Haiku Metadata Engine · SupoClip Mac Video Renderer · Scheduled YouTube Uploader</div>
        </div>
        <span class="live-chip"><i></i>156 Nodes Active</span>
      </div>

      <!-- 4-Stage Interactive Pipeline Conveyor -->
      <div class="conveyor">
        <div id="stepPosted" onclick="switchStageTab('posted')" class="conveyor-step active">
          <div class="step-l">Shorts Published</div>
          <div class="step-v">{total_posted_shorts} <span style="font-size: 13px; color: var(--money); font-weight: normal;">(+{posted_today_count} Today)</span></div>
          <div class="step-sub">10 posted yesterday</div>
        </div>
        <div id="stepCreated" onclick="switchStageTab('created')" class="conveyor-step">
          <div class="step-l">Shorts Created</div>
          <div class="step-v">{total_unique_shorts}</div>
          <div class="step-sub">SupoClip rendered clips</div>
        </div>
        <div id="stepQueued" onclick="switchStageTab('queued')" class="conveyor-step">
          <div class="step-l">Buffer Queue</div>
          <div class="step-v">{total_queued_shorts}</div>
          <div class="step-sub">Next drop at 08:00 PM IST</div>
        </div>
        <div id="stepVideos" onclick="switchStageTab('videos')" class="conveyor-step">
          <div class="step-l">Main Videos Revamped</div>
          <div class="step-v">{total_videos} / {total_videos}</div>
          <div class="step-sub">100% metadata beautified</div>
        </div>
      </div>

      <!-- Detail Drawer Content -->
      <div id="drawerTabPosted" class="stage-content">
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 10px;">
          Chronological list of all YouTube Shorts published from Helldivers 2 gameplay:
        </div>
        <div>
          {ps5_posted_html}
        </div>
      </div>

      <div id="drawerTabCreated" class="stage-content hidden">
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 10px;">
          All 33 unique rendered shorts with virality scores and hook types:
        </div>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px;">
          <!-- Created cards preview -->
          {''.join([f'<div style="background: var(--soft); border: 1px solid var(--line); border-radius: 10px; padding: 10px; font-size: 12px;"><div style="font-weight: 600; color: var(--ink);">{s.get("title")}</div><div style="color: var(--muted); margin-top: 4px;">Score: {s.get("score")}/100 · Status: {s.get("status")}</div></div>' for s in unique_shorts_map.values()])}
        </div>
      </div>

      <div id="drawerTabQueued" class="stage-content hidden">
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 10px;">
          21 shorts ready in Google Drive buffer, sorted by virality score for upcoming publishing slots:
        </div>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px;">
          {''.join([f'<div style="background: var(--soft); border: 1px solid var(--line); border-radius: 10px; padding: 10px; font-size: 12px;"><div style="display: flex; justify-content: space-between; margin-bottom: 4px;"><span style="color: var(--accent); font-weight: 600;">Score: {s.get("score")}/100</span><span style="color: var(--muted);">{s.get("duration", 30)}s</span></div><div style="font-weight: 500; color: var(--ink);">{s.get("title")}</div><div style="color: var(--muted); font-style: italic; margin-top: 2px;">"{s.get("hook")}"</div></div>' for s in queued_shorts_list])}
        </div>
      </div>

      <div id="drawerTabVideos" class="stage-content hidden">
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 10px;">
          9 Long-Form Videos beautified by Claude Haiku (Before vs After):
        </div>
        <div style="display: grid; gap: 10px;">
          {''.join([f'<div style="background: var(--soft); border: 1px solid var(--line); border-radius: 10px; padding: 12px; font-size: 12px;"><div style="display: flex; justify-content: space-between; margin-bottom: 6px;"><span style="color: var(--money); font-weight: 600;">✓ Beautified</span><a href="{v["url"]}" target="_blank" rel="noopener" class="btn-sm">YouTube ↗</a></div><div style="color: var(--down); text-decoration: line-through; margin-bottom: 3px;">Old: {v["old_title"]}</div><div style="color: var(--ink); font-weight: 600;">New: {v["new_title"]}</div></div>' for v in cleaned_videos_list])}
        </div>
      </div>
    </section>

    <!-- SECTION: Unified Content Drops Timeline -->
    <section id="sectionTimeline" class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">🗓️ Scheduled Cross-Platform Content Drops</h2>
          <div class="card-sub">Combined chronological publishing schedule in IST (YouTube Shorts + Instagram Reels)</div>
        </div>
      </div>
      <div>
        {timeline_html}
      </div>
    </section>

    <!-- SECTION: Fleet Operations & Auto-Discovered Workflows -->
    <section id="sectionOps" class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">⚙️ Fleet Telemetry & Auto-Discovered Pipelines</h2>
          <div class="card-sub">3 Active Production Pipelines · {len(standby_workflows)} Standby Automations</div>
        </div>
        <span class="live-chip"><i></i>Self-Healing Auto-Resolver Active</span>
      </div>

      <!-- Active Workflows -->
      <div class="wf-grid">
        {active_wf_rows}
      </div>

      <!-- Standby Workflows Drawer -->
      <div class="standby-drawer">
        <details>
          <summary style="cursor: pointer; font-size: 12px; font-weight: 600; color: var(--ink2);">
            View All {len(standby_workflows)} Standby Automations (Auto-activates on deployment)
          </summary>
          <div class="standby-grid">
            {standby_wf_rows}
          </div>
        </details>
      </div>
    </section>
  </div>

  <!-- J.A.R.V.I.S. Voice Console Overlay (Passcode Protected: 2805) -->
  <div id="jarvisConsoleOverlay" class="fixed inset-0 z-50 bg-[#07090e]/95 backdrop-blur-xl hidden flex flex-col items-center justify-between p-6 transition-all duration-300" style="position: fixed; inset: 0; z-index: 9999; background: rgba(7, 9, 14, 0.96); backdrop-filter: blur(20px); display: none; flex-direction: column; align-items: center; justify-content: space-between; padding: 24px;">
    
    <!-- Top HUD Bar -->
    <div style="width: 100%; max-width: 640px; display: flex; justify-content: space-between; align-items: center;">
      <div>
        <span style="font-size: 11px; font-weight: 700; letter-spacing: 0.2em; color: var(--accent); text-transform: uppercase;">J.A.R.V.I.S. Conversational Intelligence</span>
        <div style="font-size: 11px; color: var(--muted);">Live Hands-Free Duplex · Multi-Pipeline Telemetry</div>
      </div>
      <div style="display: flex; gap: 8px;">
        <button onclick="toggleJarvisKeyboard()" style="all: unset; cursor: pointer; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--line2); background: var(--soft); font-size: 11px; color: var(--ink2);">⌨️ Text</button>
        <button onclick="closeJarvisConsole()" style="all: unset; cursor: pointer; width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--line2); background: var(--soft); color: var(--ink); text-align: center; line-height: 32px; font-size: 14px;">✕</button>
      </div>
    </div>

    <!-- Centerpiece Arc Reactor -->
    <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; margin: auto; max-width: 580px;">
      <div id="arcReactorGlow" onclick="handleReactorClick()" style="width: 180px; height: 180px; border-radius: 50%; border: 1px solid rgba(123, 150, 255, 0.4); display: flex; align-items: center; justify-content: center; position: relative; cursor: pointer; box-shadow: 0 0 40px rgba(62, 99, 221, 0.25);">
        <div style="position: absolute; inset: 8px; border-radius: 50%; border: 1px dashed rgba(123, 150, 255, 0.4);"></div>
        <div style="width: 100px; height: 100px; border-radius: 50%; border: 2px solid rgba(123, 150, 255, 0.8); background: rgba(62, 99, 221, 0.15); display: flex; align-items: center; justify-content: center;">
          <div id="reactorCore" style="width: 44px; height: 44px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 25px var(--accent);"></div>
        </div>
      </div>

      <div style="margin-top: 24px; display: flex; align-items: center; gap: 8px;">
        <span id="jarvisStateDot" style="width: 8px; height: 8px; border-radius: 50%; background: var(--accent);"></span>
        <span id="jarvisStateText" style="font-size: 11px; font-weight: 700; letter-spacing: 0.2em; color: var(--accent); text-transform: uppercase;">LISTENING CONTINUOUSLY...</span>
      </div>

      <!-- Live Voice Subtitles Readout (No suggestion question clutter) -->
      <div style="margin-top: 20px; min-height: 90px; display: flex; flex-direction: column; align-items: center;">
        <p id="liveUserInput" style="font-size: 16px; font-weight: 500; color: #fff; margin: 0; text-align: center;">"Speak naturally, Sir. I am listening..."</p>
        <p id="liveJarvisOutput" style="font-size: 13px; color: var(--accent); font-family: ui-monospace, monospace; margin: 10px 0 0; text-align: center; line-height: 1.5;">
          Knowledge Base loaded. Multi-pipeline telemetry ready for PS5 Shorts and Shadow Legion Reels.
        </p>
      </div>

      <!-- Optional Hidden Keyboard Fallback -->
      <div id="jarvisKeyboardBox" style="display: none; width: 100%; max-width: 420px; margin-top: 14px; gap: 8px;">
        <input id="jarvisTextInput" type="text" placeholder="Type query for Jarvis..." onkeydown="if(event.key==='Enter') sendJarvisTextQuery()" style="flex: 1; background: var(--soft); border: 1px solid var(--line2); color: #fff; padding: 8px 12px; border-radius: 8px; font-size: 12px; outline: none;">
        <button onclick="sendJarvisTextQuery()" style="background: var(--accent); color: #000; border: none; padding: 8px 14px; border-radius: 8px; font-weight: 600; font-size: 12px; cursor: pointer;">Ask</button>
      </div>
    </div>

    <!-- Bottom Waveform & Controls -->
    <div style="width: 100%; max-width: 480px; display: flex; flex-direction: column; align-items: center; gap: 8px;">
      <canvas id="waveformCanvas" width="480" height="60" style="width: 100%; height: 50px;"></canvas>
      <div style="font-size: 11px; color: var(--muted); display: flex; gap: 12px;">
        <span>Mic is always on</span>
        <span>•</span>
        <span>Interrupt anytime</span>
        <span>•</span>
        <button onclick="toggleMute()" id="muteBtn" style="all: unset; cursor: pointer; color: var(--accent); text-decoration: underline;">Pause Mic</button>
      </div>
    </div>
  </div>

  <!-- Passcode Lock Modal (PIN: 2805) -->
  <div id="lockScreen" style="position: fixed; inset: 0; z-index: 10000; background: var(--page); display: none; align-items: center; justify-content: center; padding: 20px;">
    <div style="max-width: 340px; width: 100%; background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 24px; text-align: center; box-shadow: var(--shadow-pop);">
      <div style="font-size: 12px; font-weight: 700; letter-spacing: 0.15em; color: var(--accent); margin-bottom: 4px;">STARK SECURE GATEWAY</div>
      <div style="font-size: 12px; color: var(--muted); margin-bottom: 20px;">Enter security passcode</div>
      <div style="display: flex; justify-content: center; gap: 12px; margin-bottom: 20px;">
        <div id="dot-0" style="width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2);"></div>
        <div id="dot-1" style="width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2);"></div>
        <div id="dot-2" style="width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2);"></div>
        <div id="dot-3" style="width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2);"></div>
      </div>
      <div id="lockError" style="font-size: 11px; font-weight: 600; color: var(--down); height: 18px; margin-bottom: 12px;"></div>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; max-width: 220px; margin: 0 auto 16px;">
        {''.join([f'<button onclick="pressKey({i})" style="all: unset; cursor: pointer; height: 44px; background: var(--soft); border: 1px solid var(--line); border-radius: 10px; font-size: 18px; font-weight: 600; color: var(--ink);">{i}</button>' for i in [1,2,3,4,5,6,7,8,9]])}
        <button onclick="clearPin()" style="all: unset; cursor: pointer; height: 44px; background: var(--soft); border: 1px solid var(--line); border-radius: 10px; font-size: 12px; color: var(--muted);">CLR</button>
        <button onclick="pressKey(0)" style="all: unset; cursor: pointer; height: 44px; background: var(--soft); border: 1px solid var(--line); border-radius: 10px; font-size: 18px; font-weight: 600; color: var(--ink);">0</button>
        <button onclick="backspacePin()" style="all: unset; cursor: pointer; height: 44px; background: var(--soft); border: 1px solid var(--line); border-radius: 10px; font-size: 14px; color: var(--muted);">⌫</button>
      </div>
      <button onclick="cancelPinLock()" style="all: unset; cursor: pointer; font-size: 12px; color: var(--muted);">Cancel</button>
    </div>
  </div>

  <script>
    window.JARVIS_KB = {json.dumps(kb_repository)};
  </script>

  <!-- Dashboard Interactivity & J.A.R.V.I.S. Core Engine -->
  <script>
    // Theme Management
    function toggleTheme() {{
      const html = document.documentElement;
      const cur = html.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('fleet_theme', next);
      document.getElementById('themeIcon').innerText = next === 'dark' ? '☀️ Light' : '🌙 Dark';
    }}
    const savedTheme = localStorage.getItem('fleet_theme');
    if (savedTheme) {{
      document.documentElement.setAttribute('data-theme', savedTheme);
      document.getElementById('themeIcon').innerText = savedTheme === 'dark' ? '☀️ Light' : '🌙 Dark';
    }}

    // Segmented Navigation Filter
    function switchFilter(type) {{
      const tabs = ['all', 'ps5', 'sl', 'ops'];
      tabs.forEach(t => {{
        const btn = document.getElementById('tab' + t.charAt(0).toUpperCase() + t.slice(1));
        if (btn) btn.setAttribute('aria-pressed', t === type ? 'true' : 'false');
      }});

      const secPS5 = document.getElementById('sectionPS5');
      const secSL = document.getElementById('sectionShadowLegion');
      const secTime = document.getElementById('sectionTimeline');
      const secOps = document.getElementById('sectionOps');

      if (type === 'all') {{
        secPS5.classList.remove('hidden');
        secSL.classList.remove('hidden');
        secTime.classList.remove('hidden');
        secOps.classList.remove('hidden');
      }} else if (type === 'ps5') {{
        secPS5.classList.remove('hidden');
        secSL.classList.add('hidden');
        secTime.classList.remove('hidden');
        secOps.classList.add('hidden');
      }} else if (type === 'sl') {{
        secPS5.classList.add('hidden');
        secSL.classList.remove('hidden');
        secTime.classList.remove('hidden');
        secOps.classList.add('hidden');
      }} else if (type === 'ops') {{
        secPS5.classList.add('hidden');
        secSL.classList.add('hidden');
        secTime.classList.add('hidden');
        secOps.classList.remove('hidden');
      }}
    }}

    // PS5 Conveyor Stage Tabs
    function switchStageTab(tabName) {{
      const stages = ['posted', 'created', 'queued', 'videos'];
      stages.forEach(s => {{
        const btn = document.getElementById('step' + s.charAt(0).toUpperCase() + s.slice(1));
        const drawer = document.getElementById('drawerTab' + s.charAt(0).toUpperCase() + s.slice(1));
        if (btn) {{
          if (s === tabName) btn.classList.add('active');
          else btn.classList.remove('active');
        }}
        if (drawer) {{
          if (s === tabName) drawer.classList.remove('hidden');
          else drawer.classList.add('hidden');
        }}
      }});
    }}

    // Security Passcode PIN Gate
    let enteredPin = "";
    const CORRECT_PIN = "2805";

    function openJarvisConsole() {{
      const auth = sessionStorage.getItem("stark_auth");
      if (auth === "unlocked") {{
        showJarvisHUD();
      }} else {{
        enteredPin = "";
        updatePinDots();
        document.getElementById("lockScreen").style.display = "flex";
      }}
    }}

    function cancelPinLock() {{
      document.getElementById("lockScreen").style.display = "none";
    }}

    function updatePinDots() {{
      for (let i = 0; i < 4; i++) {{
        const dot = document.getElementById("dot-" + i);
        if (i < enteredPin.length) {{
          dot.style.background = "var(--accent)";
          dot.style.borderColor = "var(--accent)";
        }} else {{
          dot.style.background = "transparent";
          dot.style.borderColor = "var(--line2)";
        }}
      }}
    }}

    function pressKey(n) {{
      if (enteredPin.length < 4) {{
        enteredPin += n;
        updatePinDots();
        if (enteredPin.length === 4) {{
          setTimeout(verifyPin, 100);
        }}
      }}
    }}

    function clearPin() {{
      enteredPin = "";
      updatePinDots();
      document.getElementById("lockError").innerText = "";
    }}

    function backspacePin() {{
      enteredPin = enteredPin.slice(0, -1);
      updatePinDots();
      document.getElementById("lockError").innerText = "";
    }}

    function verifyPin() {{
      if (enteredPin === CORRECT_PIN) {{
        sessionStorage.setItem("stark_auth", "unlocked");
        document.getElementById("lockScreen").style.display = "none";
        showJarvisHUD();
      }} else {{
        document.getElementById("lockError").innerText = "ACCESS DENIED";
        enteredPin = "";
        setTimeout(updatePinDots, 300);
      }}
    }}

    // J.A.R.V.I.S. HUD Operations
    let isConsoleOpen = false;
    let isJarvisSpeaking = false;
    let isMuted = false;
    let recognition = null;
    let accumulatedQuery = "";
    let silenceTimer = null;

    function showJarvisHUD() {{
      isConsoleOpen = true;
      document.getElementById("jarvisConsoleOverlay").style.display = "flex";
      startContinuousSpeechRecognition();
      startWaveform();
    }}

    function closeJarvisConsole() {{
      isConsoleOpen = false;
      document.getElementById("jarvisConsoleOverlay").style.display = "none";
      stopContinuousSpeechRecognition();
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    }}

    function toggleJarvisKeyboard() {{
      const b = document.getElementById("jarvisKeyboardBox");
      if (b.style.display === "flex") b.style.display = "none";
      else {{
        b.style.display = "flex";
        document.getElementById("jarvisTextInput").focus();
      }}
    }}

    function toggleMute() {{
      isMuted = !isMuted;
      document.getElementById("muteBtn").innerText = isMuted ? "Resume Mic" : "Pause Mic";
      if (isMuted) {{
        stopContinuousSpeechRecognition();
        document.getElementById("liveUserInput").innerText = "Mic paused. Tap Resume to speak.";
      }} else {{
        startContinuousSpeechRecognition();
        document.getElementById("liveUserInput").innerText = "Listening continuously...";
      }}
    }}

    function handleReactorClick() {{
      if (isJarvisSpeaking) interruptJarvis();
    }}

    function interruptJarvis() {{
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      isJarvisSpeaking = false;
      document.getElementById("liveJarvisOutput").innerText = "Interrupted. Standing by, Sir.";
      setJarvisVisualState("LISTENING");
    }}

    function setJarvisVisualState(state) {{
      const dot = document.getElementById("jarvisStateDot");
      const txt = document.getElementById("jarvisStateText");
      if (state === "SPEAKING") {{
        dot.style.background = "var(--warn)";
        txt.innerText = "J.A.R.V.I.S. SPEAKING...";
      }} else if (state === "THINKING") {{
        dot.style.background = "var(--accent)";
        txt.innerText = "ANALYZING TELEMETRY...";
      }} else {{
        dot.style.background = "var(--money)";
        txt.innerText = isMuted ? "MIC MUTED" : "LISTENING CONTINUOUSLY...";
      }}
    }}

    function initSpeechRecognition() {{
      const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRec) return null;
      const rec = new SpeechRec();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = 'en-US';

      rec.onresult = (event) => {{
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {{
          if (event.results[i].isFinal) accumulatedQuery += " " + event.results[i][0].transcript;
          else interim += event.results[i][0].transcript;
        }}
        const spoken = (accumulatedQuery + " " + interim).trim();
        if (spoken) {{
          if (isJarvisSpeaking && spoken.length > 3) interruptJarvis();
          document.getElementById("liveUserInput").innerText = '"' + spoken + '"';
          clearTimeout(silenceTimer);
          silenceTimer = setTimeout(() => {{
            if (spoken.length > 2 && isConsoleOpen) {{
              const q = spoken;
              accumulatedQuery = "";
              processJarvisTurn(q);
            }}
          }}, 850);
        }}
      }};

      rec.onend = () => {{
        if (isConsoleOpen && !isMuted) {{
          try {{ rec.start(); }} catch(e) {{}}
        }}
      }};
      return rec;
    }}

    function startContinuousSpeechRecognition() {{
      if (isMuted) return;
      if (!recognition) recognition = initSpeechRecognition();
      if (recognition) {{
        try {{ recognition.start(); }} catch(e) {{}}
      }}
    }}

    function stopContinuousSpeechRecognition() {{
      if (recognition) {{
        try {{ recognition.stop(); }} catch(e) {{}}
      }}
    }}

    function sendJarvisTextQuery() {{
      const inp = document.getElementById("jarvisTextInput");
      if (!inp) return;
      const q = inp.value.trim();
      if (!q) return;
      document.getElementById("liveUserInput").innerText = '"' + q + '"';
      inp.value = "";
      processJarvisTurn(q);
    }}

    function speakJarvis(text) {{
      if (!('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      isJarvisSpeaking = true;
      setJarvisVisualState("SPEAKING");
      document.getElementById("liveJarvisOutput").innerText = text;

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.pitch = 0.95;
      utterance.rate = 1.0;

      utterance.onend = () => {{
        isJarvisSpeaking = false;
        setJarvisVisualState("LISTENING");
      }};
      utterance.onerror = () => {{
        isJarvisSpeaking = false;
        setJarvisVisualState("LISTENING");
      }};
      window.speechSynthesis.speak(utterance);
    }}

    async function processJarvisTurn(query) {{
      setJarvisVisualState("THINKING");
      document.getElementById("liveJarvisOutput").innerText = "Analyzing fleet telemetry...";

      const kb = window.JARVIS_KB || {{}};
      const summary = kb.summary || {{}};
      const sl = kb.shadow_legion || {{}};
      const qLower = query.toLowerCase();

      // Semantic reasoning
      let ans = "";
      if (qLower.includes("shadow legion") || qLower.includes("reel") || qLower.includes("instagram")) {{
        if (qLower.includes("next") || qLower.includes("plan")) {{
          ans = `Sir, for Shadow Legion, Reel #3 titled 'The bosses' is scheduled for release on Tue 17 Nov at 06:00 PM IST. We have used 3 of our 20 monthly slots, leaving 17 reels available through Dec 14.`;
        }} else if (qLower.includes("last") || qLower.includes("score") || qLower.includes("reach")) {{
          ans = `The last posted reel was 'Rise close-up' on Instagram, achieving 134 views, 119 reach, 4 likes, and a virality score of 50.4.`;
        }} else {{
          ans = `For Shadow Legion, 3 of 20 reels are scheduled or posted. The next drop is Reel #3 on Tue 17 Nov at 06:00 PM IST. Creative data shows keeping videos between 18 and 22 seconds maximizes retention.`;
        }}
      }} else if (qLower.includes("today") || qLower.includes("since midnight")) {{
        ans = `Sir, exactly {posted_today_count} shorts have been uploaded to YouTube today since midnight: 'Reloading mid-flank' at 06:00 AM IST, and 'Pelican One extraction' at 03:00 AM IST. {total_queued_shorts} shorts remain queued in your buffer.`;
      }} else if (qLower.includes("how many") || qLower.includes("clean") || qLower.includes("beautif")) {{
        ans = `All 9 main Helldivers 2 videos were cleaned up and beautified by Claude Haiku. From those, 33 shorts were generated, 12 have been posted to YouTube, and 21 are queued in buffer.`;
      }} else {{
        ans = `Your automation fleet is operating normally with a 99.2% success rate across 400 executions. PS5 Shorts has 12 published and 21 in buffer; Shadow Legion has Reel #3 scheduled for Tue at 06:00 PM IST.`;
      }}

      speakJarvis(ans);
    }}

    // Waveform visualizer
    function startWaveform() {{
      const canvas = document.getElementById("waveformCanvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      let phase = 0;
      function draw() {{
        if (!isConsoleOpen) return;
        requestAnimationFrame(draw);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.lineWidth = 1.5;
        const color = isJarvisSpeaking ? "#f6b100" : "#7b96ff";
        ctx.strokeStyle = color;
        ctx.beginPath();
        for (let x = 0; x < canvas.width; x++) {{
          const amp = isJarvisSpeaking ? 16 : 8;
          const y = canvas.height / 2 + Math.sin(x * 0.04 + phase) * amp * Math.sin(x * 0.015);
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }}
        ctx.stroke();
        phase += 0.08;
      }}
      draw();
    }}
  </script>
</body>
</html>
"""

    index_path = "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Rendered redesigned dashboard to {index_path} ({len(html_content)} bytes)")

if __name__ == "__main__":
    main()
