# 📊 n8n Real-Time Analytics & Performance Dashboard

An automated, self-updating analytics dashboard for cloud-hosted [n8n](https://n8n.io) instances.

🔗 **Live Public Dashboard:** [https://prajyot2805.github.io/n8n-dashboard/](https://prajyot2805.github.io/n8n-dashboard/)

---

## ⚡ Features

- **Automated Hourly Sync:** Runs via GitHub Actions every hour (or manually on-demand).
- **Execution Distribution:** Tracks execution counts, peak hours, and workload share across pipelines.
- **Workflow Health Matrix:** Filter active vs. inactive pipelines, review node counts, average run durations, and trigger types.
- **Error & Incident Diagnostics:** Automatic extraction of execution failures, failing node names, and root-cause error messages.
- **Secure Architecture:** The n8n API key is stored securely in GitHub Repository Secrets (`N8N_API_KEY`) and is **never** committed or exposed in the public code.

---

## 🛠️ How It Works

1. **GitHub Actions Workflow** (`.github/workflows/update.yml`) runs on an hourly cron schedule.
2. `update_dashboard.py` queries the n8n REST API (`/api/v1/workflows` and `/api/v1/executions`).
3. Aggregated metrics are pre-rendered into `index.html`.
4. Changes are committed to `main` and automatically published to GitHub Pages.

---

## 🔒 Configuration & Secrets

This repository uses GitHub Secrets to connect to n8n:
- `N8N_BASE_URL`: Base URL of your n8n instance (e.g. `https://n8n.srv992629.hstgr.cloud`).
- `N8N_API_KEY`: API Key generated from n8n Settings → n8n API.

---

*Generated and managed by Antigravity Assistant.*
