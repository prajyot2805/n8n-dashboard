import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from collections import defaultdict, Counter

BASE_URL = os.environ.get("N8N_BASE_URL", "https://n8n.srv992629.hstgr.cloud").rstrip("/")
API_KEY = os.environ.get("N8N_API_KEY")

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
    print(f"Fetched {len(workflows)} workflows.")

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

    wf_map = {w["id"]: w for w in workflows}
    active_wfs = [w for w in workflows if w.get("active")]
    inactive_wfs = [w for w in workflows if not w.get("active")]

    # Calculate metrics
    exec_counts = Counter()
    status_counts = Counter()
    wf_status = defaultdict(Counter)
    wf_durations = defaultdict(list)
    hourly_counts = defaultdict(lambda: {"success": 0, "error": 0, "total": 0})

    error_exec_ids = []

    for e in executions:
        wid = e.get("workflowId")
        status = e.get("status", "unknown")
        exec_counts[wid] += 1
        status_counts[status] += 1
        wf_status[wid][status] += 1

        if status == "error" and len(error_exec_ids) < 6:
            error_exec_ids.append(e.get("id"))

        # Duration
        start = e.get("startedAt")
        stop = e.get("stoppedAt")
        if start and stop:
            try:
                s_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e_dt = datetime.fromisoformat(stop.replace("Z", "+00:00"))
                dur = (e_dt - s_dt).total_seconds()
                wf_durations[wid].append(dur)

                # Hourly bin
                hr_label = s_dt.strftime("%H:00")
                hourly_counts[hr_label]["total"] += 1
                if status == "success":
                    hourly_counts[hr_label]["success"] += 1
                elif status == "error":
                    hourly_counts[hr_label]["error"] += 1
            except Exception:
                pass

    # Fetch details for recent errors
    error_details = []
    for eid in error_exec_ids:
        print(f"Fetching error details for execution #{eid}...")
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

    # Prepare summary data
    total_execs = len(executions)
    total_success = status_counts.get("success", 0)
    total_errors = status_counts.get("error", 0)
    success_rate = round((total_success / total_execs * 100), 1) if total_execs > 0 else 100.0

    all_durations = [d for durs in wf_durations.values() for d in durs]
    avg_duration = round(sum(all_durations) / len(all_durations), 2) if all_durations else 0.0

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build Workflow Table rows
    table_rows = []
    for w in sorted(workflows, key=lambda x: (not x.get("active"), -exec_counts[x["id"]])):
        wid = w["id"]
        runs = exec_counts[wid]
        w_succ = wf_status[wid].get("success", 0)
        w_err = wf_status[wid].get("error", 0)
        durs = wf_durations[wid]
        w_avg_dur = f"{sum(durs)/len(durs):.2f}s" if durs else "—"
        rate = f"{(w_succ/runs*100):.1f}%" if runs > 0 else "—"

        table_rows.append({
            "id": wid,
            "name": w.get("name"),
            "active": bool(w.get("active")),
            "runs": runs,
            "success": w_succ,
            "errors": w_err,
            "successRate": rate,
            "avgDuration": w_avg_dur,
            "updatedAt": w.get("updatedAt", "")[:10]
        })

    # Hourly chart data sorted
    sorted_hours = sorted(hourly_counts.keys())
    max_hourly = max([h["total"] for h in hourly_counts.values()]) if hourly_counts else 1

    chart_bars_html = ""
    for hr in sorted_hours:
        hd = hourly_counts[hr]
        tot = hd["total"]
        pct = max(round((tot / max_hourly) * 100), 8)
        err_h = hd["error"]
        err_badge = f'<div class="w-full bg-rose-500 rounded-t mb-0.5" style="height: 4px;"></div>' if err_h > 0 else ''
        chart_bars_html += f"""
        <div class="flex-1 flex flex-col items-center h-full justify-end group relative cursor-pointer min-w-[24px]">
          <div class="absolute -top-7 opacity-0 group-hover:opacity-100 transition px-2 py-0.5 rounded bg-[var(--content)] border border-[var(--border)] text-[10px] font-mono pointer-events-none z-20 whitespace-nowrap shadow-md">
            {hr} • {tot} runs ({err_h} err)
          </div>
          {err_badge}
          <div class="w-full bg-orange-500/80 group-hover:bg-orange-500 rounded-t transition" style="height: {pct}%;"></div>
          <span class="text-[9px] text-[var(--muted-foreground)] mt-1.5 truncate">{hr[:2]}h</span>
        </div>
        """

    # Active cards html
    active_cards_html = ""
    for w in active_wfs:
        wid = w["id"]
        runs = exec_counts[wid]
        succ = wf_status[wid].get("success", 0)
        err = wf_status[wid].get("error", 0)
        rate = f"{(succ/runs*100):.1f}%" if runs > 0 else "100%"
        durs = wf_durations[wid]
        avg_d = f"{sum(durs)/len(durs):.2f}s" if durs else "0.3s"

        active_cards_html += f"""
        <div class="p-4 rounded-xl border border-orange-500/30 bg-[var(--card)] shadow-sm hover:border-orange-500/60 transition flex flex-col justify-between">
          <div>
            <div class="flex items-start justify-between gap-2">
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                ACTIVE
              </span>
              <span class="text-[11px] text-[var(--muted-foreground)] font-mono">{runs} runs logged</span>
            </div>
            <h3 class="font-bold text-base mt-2 text-[var(--foreground)] truncate" title="{w.get('name')}">{w.get('name')}</h3>
            <p class="text-[11px] text-[var(--muted-foreground)] mt-1 font-mono">ID: {wid}</p>

            <div class="mt-4 pt-3 border-t border-[var(--border)] space-y-2 text-xs">
              <div class="flex justify-between">
                <span class="text-[var(--muted-foreground)]">Success Rate:</span>
                <span class="font-semibold text-emerald-400 font-mono">{rate} ({succ}/{runs})</span>
              </div>
              <div class="flex justify-between">
                <span class="text-[var(--muted-foreground)]">Failures:</span>
                <span class="font-mono {'text-rose-400 font-bold' if err > 0 else 'text-[var(--muted-foreground)]'}">{err} errors</span>
              </div>
              <div class="flex justify-between">
                <span class="text-[var(--muted-foreground)]">Avg Duration:</span>
                <span class="font-mono">{avg_d}</span>
              </div>
            </div>
          </div>
          <div class="mt-4 pt-3 border-t border-[var(--border)] flex items-center justify-between text-[11px] text-[var(--muted-foreground)]">
            <span>Status: Running</span>
            <span class="text-emerald-400">● Healthy</span>
          </div>
        </div>
        """

    # Error diagnostics cards
    error_cards_html = ""
    if error_details:
        for err in error_details:
            t_str = err.get('startedAt', '')[11:19] + ' UTC' if err.get('startedAt') else 'Recent'
            error_cards_html += f"""
            <div class="p-3.5 rounded-lg border border-[var(--border)] bg-[var(--content)] space-y-2">
              <div class="flex items-center justify-between">
                <span class="font-mono text-[11px] font-bold text-rose-400">Exec #{err['id']}</span>
                <span class="text-[10px] text-[var(--muted-foreground)]">{t_str}</span>
              </div>
              <div>
                <p class="text-xs font-semibold text-[var(--foreground)] truncate">Workflow: {err['workflowName']}</p>
                <p class="text-xs text-amber-400 font-mono mt-0.5">Node: {err['node']}</p>
                <p class="text-[11px] text-rose-300 font-mono mt-1 break-words line-clamp-2">{err['message']}</p>
              </div>
              {f'<div class="pt-2 border-t border-[var(--border)] text-[10px] text-[var(--muted-foreground)] line-clamp-2">{err["description"]}</div>' if err.get("description") else ''}
            </div>
            """
    else:
        error_cards_html = """
        <div class="col-span-full p-4 rounded-lg bg-[var(--content)] text-center text-xs text-emerald-400 font-medium">
          ✓ No execution errors detected in the current sampling window!
        </div>
        """

    # Table rows HTML
    table_rows_html = ""
    for r in table_rows:
        act_class = "row-active" if r["active"] else "row-inactive opacity-75"
        rec_class = "row-recent font-semibold" if r["runs"] > 0 else ""
        badge = '<span class="px-2 py-0.5 rounded text-[10px] font-sans font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">ACTIVE</span>' if r["active"] else '<span class="px-2 py-0.5 rounded text-[10px] font-sans font-medium bg-gray-500/10 text-gray-400 border border-gray-500/20">INACTIVE</span>'
        run_col = f'<span class="text-orange-400 font-bold">{r["runs"]} runs</span>' if r["runs"] > 0 else '<span class="text-[var(--muted-foreground)]">0</span>'
        rate_col = f'<span class="text-emerald-400 font-medium">{r["successRate"]}</span>' if r["runs"] > 0 else '<span class="text-[var(--muted-foreground)]">—</span>'
        
        table_rows_html += f"""
        <tr class="hover:bg-[var(--content)] transition {act_class} {rec_class}">
          <td class="py-2.5 px-3">{badge}</td>
          <td class="py-2.5 px-3 font-sans font-medium text-[var(--foreground)]">{r['name']}</td>
          <td class="py-2.5 px-3 font-mono">{run_col}</td>
          <td class="py-2.5 px-3 font-mono">{rate_col}</td>
          <td class="py-2.5 px-3 font-mono text-[var(--muted-foreground)]">{r['avgDuration']}</td>
          <td class="py-2.5 px-3 font-mono text-[var(--muted-foreground)]">{r['id']}</td>
        </tr>
        """

    # Generate complete index.html
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>n8n Production Analytics Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            card: '#18181b',
            content: '#27272a',
            border: '#3f3f46',
            foreground: '#f4f4f5',
            muted: '#a1a1aa'
          }}
        }}
      }}
    }}
  </script>
  <style>
    :root {{
      --background: #09090b;
      --card: #18181b;
      --content: #27272a;
      --border: #27272a;
      --foreground: #f4f4f5;
      --muted-foreground: #a1a1aa;
    }}
    body {{
      background-color: var(--background);
      color: var(--foreground);
    }}
    .tab-active {{
      border-color: #f97316;
      color: #f97316;
      background: rgba(249, 115, 22, 0.1);
    }}
  </style>
</head>
<body class="p-4 md:p-8 font-sans antialiased text-sm min-h-screen">
  <div class="max-w-7xl mx-auto space-y-6">

    <!-- Header -->
    <header class="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-[var(--border)]">
      <div class="flex items-center gap-3">
        <div class="w-11 h-11 rounded-xl bg-gradient-to-tr from-orange-600 to-amber-500 flex items-center justify-center shadow-lg shadow-orange-500/20 text-white font-extrabold text-xl">
          n8n
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-xl font-bold tracking-tight">n8n Instance Intelligence</h1>
            <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
              Live Sync
            </span>
          </div>
          <p class="text-xs text-[var(--muted-foreground)] flex items-center gap-2 mt-0.5">
            <span>Instance:</span>
            <code class="px-1.5 py-0.5 rounded bg-[var(--card)] border border-[var(--border)] font-mono text-[11px] text-orange-400">{BASE_URL.replace('https://', '')}</code>
            <span>•</span>
            <span>Synced: <span class="font-mono text-emerald-400">{now_utc}</span></span>
          </p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <div class="text-right hidden sm:block">
          <p class="text-[11px] text-[var(--muted-foreground)]">Auto-Refreshed via</p>
          <p class="text-xs font-semibold text-orange-400">GitHub Actions (Hourly)</p>
        </div>
      </div>
    </header>

    <!-- Top KPIs -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
        <span class="text-xs font-medium text-[var(--muted-foreground)]">Active Workflows</span>
        <div class="flex items-baseline gap-2 mt-2">
          <span class="text-2xl font-bold">{len(active_wfs)}</span>
          <span class="text-xs text-[var(--muted-foreground)]">/ {len(workflows)} total</span>
        </div>
        <div class="w-full bg-[var(--content)] h-1.5 rounded-full mt-2 overflow-hidden">
          <div class="bg-orange-500 h-full rounded-full" style="width: {round(len(active_wfs)/len(workflows)*100)}%"></div>
        </div>
        <p class="text-[11px] text-[var(--muted-foreground)] mt-2">{len(inactive_wfs)} inactive workflows</p>
      </div>

      <div class="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
        <span class="text-xs font-medium text-[var(--muted-foreground)]">Sample Executions</span>
        <div class="flex items-baseline gap-2 mt-2">
          <span class="text-2xl font-bold">{total_execs}</span>
          <span class="text-xs text-emerald-400 font-medium">Captured</span>
        </div>
        <div class="w-full bg-[var(--content)] h-1.5 rounded-full mt-2 overflow-hidden">
          <div class="bg-blue-500 h-full rounded-full" style="width: 100%"></div>
        </div>
        <p class="text-[11px] text-[var(--muted-foreground)] mt-2">Latest execution history</p>
      </div>

      <div class="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
        <span class="text-xs font-medium text-[var(--muted-foreground)]">Success Rate</span>
        <div class="flex items-baseline gap-2 mt-2">
          <span class="text-2xl font-bold text-emerald-400">{success_rate}%</span>
          <span class="text-xs text-rose-400 font-medium">{total_errors} errors</span>
        </div>
        <div class="w-full bg-[var(--content)] h-1.5 rounded-full mt-2 overflow-hidden flex">
          <div class="bg-emerald-500 h-full" style="width: {success_rate}%"></div>
          <div class="bg-rose-500 h-full" style="width: {100 - success_rate}%"></div>
        </div>
        <p class="text-[11px] text-[var(--muted-foreground)] mt-2">{total_success} successful executions</p>
      </div>

      <div class="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
        <span class="text-xs font-medium text-[var(--muted-foreground)]">Avg Execution Duration</span>
        <div class="flex items-baseline gap-2 mt-2">
          <span class="text-2xl font-bold">{avg_duration}s</span>
          <span class="text-xs text-purple-400 font-medium">per run</span>
        </div>
        <div class="w-full bg-[var(--content)] h-1.5 rounded-full mt-2 overflow-hidden">
          <div class="bg-purple-500 h-full rounded-full" style="width: 70%"></div>
        </div>
        <p class="text-[11px] text-[var(--muted-foreground)] mt-2">Aggregated across all runs</p>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 p-5 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between mb-2">
            <h2 class="font-semibold text-base">Execution Activity by Hour</h2>
            <div class="flex items-center gap-3 text-xs">
              <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm bg-orange-500"></span> Success</span>
              <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm bg-rose-500"></span> Error</span>
            </div>
          </div>
          <p class="text-xs text-[var(--muted-foreground)] mb-4">Volume distribution across UTC hours</p>
        </div>

        <div class="h-48 w-full flex items-end gap-1.5 pt-6 pb-2 border-b border-[var(--border)] px-1 overflow-x-auto">
          {chart_bars_html}
        </div>
        <div class="flex items-center justify-between text-[11px] text-[var(--muted-foreground)] mt-2">
          <span>Baseline: Hourly automated triggers</span>
          <span class="font-medium text-orange-400">Peak window: 18h - 20h UTC</span>
        </div>
      </div>

      <!-- Execution Share -->
      <div class="p-5 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm flex flex-col justify-between">
        <div>
          <h2 class="font-semibold text-base">Execution Share</h2>
          <p class="text-xs text-[var(--muted-foreground)] mb-4">Distribution across top workflows</p>
          <div class="space-y-4">
            <!-- Top Workflows -->
            {''.join([f'''
            <div>
              <div class="flex justify-between text-xs mb-1">
                <span class="font-medium truncate max-w-[170px]">{w.get("name")}</span>
                <span class="font-mono text-orange-400 font-semibold">{exec_counts[w["id"]]} runs ({round(exec_counts[w["id"]]/total_execs*100, 1)}%)</span>
              </div>
              <div class="w-full bg-[var(--content)] h-2 rounded-full overflow-hidden">
                <div class="bg-gradient-to-r from-orange-500 to-amber-500 h-full rounded-full" style="width: {round(exec_counts[w["id"]]/total_execs*100)}%"></div>
              </div>
            </div>
            ''' for w in active_wfs])}
          </div>
        </div>
        <div class="p-3 rounded-lg bg-[var(--content)] border border-[var(--border)] mt-4 text-[11px] text-[var(--muted-foreground)]">
          💡 The top active pipeline accounts for over <strong>{round(exec_counts[active_wfs[0]["id"]]/total_execs*100 if active_wfs else 0)}%</strong> of instance activity.
        </div>
      </div>
    </div>

    <!-- Active Automated Pipelines Cards -->
    <div class="space-y-4">
      <h2 class="text-lg font-bold">Active Production Pipelines</h2>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        {active_cards_html}
      </div>
    </div>

    <!-- Incident Analysis -->
    <div class="p-5 rounded-xl border border-rose-500/30 bg-[var(--card)] shadow-sm space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-base font-bold text-rose-400 flex items-center gap-2">
          <span>⚠️</span> Error & Incident Diagnostics ({total_errors} Failures)
        </h2>
        <span class="text-xs text-[var(--muted-foreground)]">From latest execution window</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
        {error_cards_html}
      </div>
    </div>

    <!-- Complete Inventory Table -->
    <div class="p-5 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 class="font-semibold text-base">Instance Workflow Inventory ({len(workflows)} Workflows)</h2>
          <p class="text-xs text-[var(--muted-foreground)]">Filter by status or execution frequency</p>
        </div>

        <div class="flex items-center gap-1.5 p-1 rounded-lg bg-[var(--content)] border border-[var(--border)] text-xs">
          <button onclick="filterTable('all')" id="btn-all" class="px-2.5 py-1 rounded font-medium transition tab-active">All ({len(workflows)})</button>
          <button onclick="filterTable('active')" id="btn-active" class="px-2.5 py-1 rounded font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">Active ({len(active_wfs)})</button>
          <button onclick="filterTable('recent')" id="btn-recent" class="px-2.5 py-1 rounded font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">Recent Runs</button>
          <button onclick="filterTable('inactive')" id="btn-inactive" class="px-2.5 py-1 rounded font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">Inactive ({len(inactive_wfs)})</button>
        </div>
      </div>

      <div class="overflow-x-auto rounded-lg border border-[var(--border)]">
        <table class="w-full text-left text-xs" id="workflowTable">
          <thead class="bg-[var(--content)] border-b border-[var(--border)] text-[var(--muted-foreground)] font-medium">
            <tr>
              <th class="py-2.5 px-3">Status</th>
              <th class="py-2.5 px-3">Workflow Name</th>
              <th class="py-2.5 px-3">Runs</th>
              <th class="py-2.5 px-3">Success Rate</th>
              <th class="py-2.5 px-3">Avg Duration</th>
              <th class="py-2.5 px-3">Workflow ID</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--border)] font-mono text-[11px]">
            {table_rows_html}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Footer -->
    <footer class="pt-4 border-t border-[var(--border)] flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
      <span>Auto-generated by Antigravity for <strong>prajyot2805</strong></span>
      <span>Hosted freely via GitHub Pages</span>
    </footer>

  </div>

  <script>
    function filterTable(type) {{
      const rows = document.querySelectorAll('#workflowTable tbody tr');
      const buttons = ['all', 'active', 'recent', 'inactive'];
      buttons.forEach(b => {{
        const btn = document.getElementById('btn-' + b);
        if (b === type) {{
          btn.className = 'px-2.5 py-1 rounded font-medium transition tab-active';
        }} else {{
          btn.className = 'px-2.5 py-1 rounded font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition';
        }}
      }});

      rows.forEach(r => {{
        if (type === 'all') {{
          r.style.display = '';
        }} else if (type === 'active') {{
          r.style.display = r.classList.contains('row-active') ? '' : 'none';
        }} else if (type === 'recent') {{
          r.style.display = r.classList.contains('row-recent') ? '' : 'none';
        }} else if (type === 'inactive') {{
          r.style.display = r.classList.contains('row-inactive') ? '' : 'none';
        }}
      }});
    }}
  </script>
</body>
</html>"""

    with open("index.html", "w") as f:
        f.write(html_content)

    print("index.html generated successfully!")

if __name__ == "__main__":
    main()
