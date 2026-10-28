<div align="center">

# ⚡ AetherDrive

### Cloud Downloader, Folder Tree Crawler & Path-Sanitizing Engine

[![Docker Image](https://img.shields.io/badge/Docker-ghcr.io-2496ED?logo=docker&logoColor=white)](https://ghcr.io/riyaz-hussain/aetherdrive)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Alpine.js](https://img.shields.io/badge/Alpine.js-8BC0D0?logo=alpinedotjs&logoColor=white)](https://alpinejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

**AetherDrive** is a cloud downloader, folder tree crawler, and path-sanitizing engine built with **FastAPI**, **Alpine.js**, and **Tailwind CSS**. It is designed to inspect public Google Drive folders, strip aggressive promo watermarks, detect spam/bloatware, avoid downloading duplicate files already on disk, and manage transfers through Docker.

---

## Table of Contents

- [Key Features & Capabilities](#key-features--capabilities)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Usage Guide](#usage-guide)
- [Engine Mechanics: Sanitization & Heuristics](#engine-mechanics-sanitization--heuristics)
- [Execution Audit Log & Data Retention](#execution-audit-log--data-retention)
- [API Reference](#api-reference)
- [Configuration & Environment](#configuration--environment)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Tech Stack](#tech-stack)
- [Third-Party Credits & Attributions](#third-party-credits--attributions)
- [License](#license)

---

## Key Features & Capabilities

### 1. Smart Folder Tree Scraping & Heuristics

- **Dual-Engine Folder Scraper:** Crawls embedded Google Drive views and metadata hierarchies without requiring official Google Cloud API keys.
- **Aggressive Path & Watermark Sanitization:** Strips domain URLs (`.com`, `.net`, `.app`, `.vercel.app`), social media handles (`@username`), promotional copy artifacts (`Copy of...`, `Copy (1) of...`), and channel watermarks while preserving valid directory structures.
- **Bloatware & Spam Filtering:** Built-in wildcard filters automatically isolate promo files, giveaways, and keygens into a separate tab without downloading them.
- **On-Disk Deduplication:** Inspects `/downloads/` against clean paths and file sizes before initiating transfers to skip files that already exist locally.
- **Binary File Extension Repair:** Inspects magic byte signatures (`ftyp`, `RIFF`, `PK\x03\x04`, `Rar!`, `ID3`, etc.) post-download to repair missing or broken extensions.

### 2. Interactive Planning & Execution Workspace

- **4-Way Filter Dashboard:** Filter scanned items across **Total Scanned**, **Approved for Download**, **Skipped Normal Files (On Disk)**, and **Skipped Bloat Files**.
- **Editable Destination Folder:** Automatically infers the folder title, strips redundant suffixes (`Drive`, `- Google Drive`), and allows real-time destination renaming before download.
- **Disk Existence Indicator:** Live status badge informs whether the destination is an **Existing Folder (on disk)** or a **New Folder (will be created)**.
- **Selective Test Downloads:** Checkbox multi-select, a master select-all toggle, and single-file download triggers let you test downloads on specific files before committing to a full folder batch.
- **Instant Re-Apply Rules Engine:** Caches the remote scan in memory so you can tweak exclusions, change the destination name, or re-check disk status in under 100ms without re-crawling Google Drive.

### 3. Connection Resilience & Audit History

- **Sleep & Disconnect Resilience:** Active background downloads and console logs survive browser refreshes, connection drops, and laptop sleep cycles via server-side session persistence.
- **Master Plan Preservation:** Retains the full inventory of scanned items—including all skipped bloatware and on-disk files—in server memory across reconnects and reloads.
- **Persistent Session Resume Banner:** Displays a completion summary banner when returning to finished jobs, with a single manual **Start New Task** trigger to clear the workspace.
- **Execution Audit Log:** SQLite-backed audit drawer storing dates, folder names, Drive URLs, status outcomes, and file transfer metrics indefinitely until manually purged.
- **Live SSE Terminal:** Streams real-time transfer progress, speeds, file counts, and console events directly to the browser UI.

---

## Architecture

```text
aetherdrive/
├── backend/
│   ├── app.py              # FastAPI server, SSE streaming, SQLite audit store, session cache
│   └── core.py             # Drive scraper, regex sanitizers, magic bytes repair, heuristics
├── frontend/
│   └── index.html          # Single-page interface built with Alpine.js & Tailwind CSS
├── Dockerfile              # Multi-stage container definition (port 8070)
├── docker-compose.yml      # Compose spec with /downloads and /appdata mounts
└── README.md
```

### Data Flow Lifecycle

```text
[Google Drive URL]
        │
        ▼
[Folder Scrape] ──► [Inspect Folder Metadata & Structure]
        │
        ▼
[Engine Sanitization] ──► Strip Domains, Handles, Watermarks & Bloat Patterns
        │
        ▼
[Local Disk Comparison] ──► Match against existing files in /downloads
        │
        ▼
[Planning Workspace] ──► 4-Way Plan View (Approved / On Disk / Bloat / Total)
        │
        ▼ (User Executes Download)
[Background Download Worker]
        │
        ├──► Real-Time SSE Logs & Counters ──► [Browser UI]
        │
        ▼
[Post-Download Magic Byte Repair] ──► Inspect Signatures & Fix Extensions
        │
        ▼
[SQLite Audit Log] ──► Record Execution History (/appdata/aether_history.db)
```

---

## Quick Start

### Run with Docker Compose

Save the following configuration as `docker-compose.yml`:

```yaml
services:
  aetherdrive:
    image: ghcr.io/riyaz-hussain/aetherdrive:latest
    container_name: aetherdrive
    ports:
      - "8070:8070"
    environment:
      - DATA_DIR=/appdata
      - DOWNLOAD_DIR=/downloads
    volumes:
      # Replace the left-hand paths with folders on your host
      - /volume/DownloadsFolder:/downloads
      - /volume/docker/appdata/aetherdrive:/appdata
    restart: unless-stopped
```

Start the service:

```bash
docker compose up -d
```

Access the web interface at `http://<your-host-ip>:8070`.

---

## Usage Guide

1. **Scan a Folder:** Paste any public Google Drive folder link and click **Inspect & Generate Plan**.
2. **Review Plan:** Inspect the 4 tab categories to verify which files are approved, skipped on disk, or flagged as bloatware.
3. **Customize Destination:** Edit the **Destination Folder** field if you want a custom subfolder name under `/downloads/`.
4. **Adjust Exclusions:** Click **Exclusions** in the top navigation to add persistent patterns (e.g., `*trailer*`, `*promo*`) that persist in SQLite.
5. **Re-Apply Rules:** Click **Re-Apply Rules** to instantly recalculate the plan against local disk contents without re-scraping Google Drive.
6. **Download:** Click **Execute Download** for all approved files, or select individual checkboxes to run a partial test download.
7. **Start Fresh:** When a task completes, review the summary and click **Start New Task** to clear the workspace for your next link.

---

## Engine Mechanics: Sanitization & Heuristics

### Path Cleaning Rules

The sanitizer transforms messy directory structures into readable local folders:

**Original:**

```text
[Course Masterclass] @BestCourses_Official - https___downloadcourse.vercel.app/01 - Introduction.mp4
```

**Cleaned:**

```text
Course Masterclass/01 - Introduction.mp4
```

### Magic-Byte Header Table

During post-processing, files missing a valid extension have their first bytes inspected:

| File Type | Header Magic Bytes | Restored Extension |
| :--- | :--- | :--- |
| MP4 Video | `....ftypmp42` or `....ftypisom` | `.mp4` |
| MKV Video | `1A 45 DF A3` | `.mkv` |
| ZIP Archive | `50 4B 03 04` (`PK\x03\x04`) | `.zip` |
| RAR Archive | `Rar!` | `.rar` |
| MP3 Audio | `ID3` | `.mp3` |
| WAV Audio | `RIFF` | `.wav` |
| PDF Document | `25 50 44 46` (`%PDF`) | `.pdf` |
| PNG Image | `89 50 4E 47 0D 0A 1A 0A` | `.png` |
| JPEG Image | `FF D8 FF` | `.jpg` |

---

## Execution Audit Log & Data Retention

- **Storage Location:** All historical runs are recorded in `/appdata/aether_history.db` (SQLite).
- **Retention Policy:** Indefinite (no automatic rolling TTL or expiration).
- **Management:** View, reload links, or delete specific entries directly from the **History** drawer. You can also purge all past records with **Clear All History Records**.

---

## API Reference

The backend exposes an asynchronous OpenAPI interface:

| Method | Route | Description |
| :--- | :--- | :--- |
| `POST` | `/api/scan` | Analyzes Drive folder link, runs filters, and returns an execution plan. |
| `POST` | `/api/download` | Initiates asynchronous background downloading for approved plan items. |
| `GET` | `/api/events` | SSE endpoint pushing real-time progress, counters, and log output. |
| `GET` | `/api/history` | Fetches historical runs from the audit database. |
| `DELETE` | `/api/history/{id}` | Deletes a specific historical record. |
| `DELETE` | `/api/history` | Purges all records from the history database. |

---

## Configuration & Environment

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `PORT` | Integer | `8070` | Internal and exposed service port. |
| `DOWNLOAD_DIR` | Path | `/downloads` | Container path where completed files are written. Mount a host folder here. |
| `DATA_DIR` | Path | `/appdata` | Container path for the SQLite audit database and persistent state. Mount a host folder here. |

---

## Troubleshooting & FAQ

**Q: The download fails with "Too many requests" or rate-limiting errors.**

A: Google Drive applies temporary quota restrictions on heavily shared public files. Wait a few hours or switch network paths.

**Q: How do I change the downloads directory on my host machine?**

A: Edit the left side of the `/downloads` volume mapping in `docker-compose.yml`:

```yaml
volumes:
  - /Volumes/MyExternalDrive/Media:/downloads
```

**Q: Will my history survive a container update?**

A: Yes, as long as `/appdata` is mapped to a host folder. The audit log lives in `/appdata/aether_history.db`.

---

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn, SQLite3, `sse-starlette`, `gdown`
- **Frontend:** Alpine.js, Tailwind CSS (CDN)
- **Deployment:** Docker, GitHub Actions (CI/CD Container Registry), Portainer

---

## Third-Party Credits & Attributions

AetherDrive is built upon open-source software:

- [gdown](https://github.com/wkentaro/gdown) by Kentaro Wada (MIT License)
- [FastAPI](https://fastapi.tiangolo.com/) by Sebastián Ramírez (MIT License)
- [sse-starlette](https://github.com/sysid/sse-starlette) (BSD-3-Clause License)
- [Alpine.js](https://alpinejs.dev/) by Caleb Porzio (MIT License)
- [Tailwind CSS](https://tailwindcss.com/) by Tailwind Labs (MIT License)
- [Uvicorn](https://www.uvicorn.org/) by Encode (BSD-3-Clause License)

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for complete terms and copyright details.