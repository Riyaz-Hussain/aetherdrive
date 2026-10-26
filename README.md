<div align="center">

# AetherDrive

### Intelligent Cloud Ingestion Engine & Automated Content Sanitizer

[![Docker Image](https://img.shields.io/badge/Docker-ghcr.io-2496ED?logo=docker&logoColor=white)](https://ghcr.io/riyaz-hussain/aetherdrive)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Alpine.js](https://img.shields.io/badge/Alpine.js-8BC0D0?logo=alpinedotjs&logoColor=white)](https://alpinejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

A containerized ingestion suite engineered to pull Google Drive directories, contextually strip spam watermarks and tracking domains, repair missing file extensions via binary magic-byte inspection, selectively purge promotional bloat, and maintain an audit history through an interactive glassmorphic dashboard.

---

## Table of Contents

- [Overview & Core Value Proposition](#overview--core-value-proposition)
- [Key Features & Capabilities](#key-features--capabilities)
- [Deep-Dive Architecture](#deep-dive-architecture)
- [Deployment & Quick Start](#deployment--quick-start)
- [User Interface & Dashboard Guide](#user-interface--dashboard-guide)
- [Engine Mechanics: Sanitization & Heuristics](#engine-mechanics-sanitization--heuristics)
- [API Reference](#api-reference)
- [Configuration & Environment](#configuration--environment)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Third-Party Credits & Attributions](#third-party-credits--attributions)
- [License](#license)

---

## Overview & Core Value Proposition

Shared cloud storage repositories (such as community course dumps, resource archives, and shared Google Drive directories) frequently present several recurring operational challenges:

- **Polluted Naming Conventions:** Files and directories are often branded with tracking URLs, domain watermarks (`*.vercel.app`, `@telegram_channels`), and marketing spam.
- **Redundant Directory Nesting:** Drive exports often wrap contents in deeply nested, redundant subfolders bearing identical names.
- **Missing or Truncated File Extensions:** Raw media files are frequently saved without extensions (e.g., raw MP4 streams downloaded as generic binary blobs), breaking playback on media servers like Plex, Jellyfin, or local players.
- **Promotional Bloat:** Repositories often bundle 50GB+ of irrelevant promotional materials (`500$ Bonus Pack`, `Free Reels Bundle`, `Join VIP Channel`) alongside legitimate content.
- **Bandwidth Waste:** Re-running standard sync scripts often redownloads already ingested files.

**AetherDrive resolves these challenges automatically.** It acts as an automated ingestion proxy: scanning remote folder structures, calculating differential execution plans, sanitizing filenames, restoring extensions on disk using magic-byte checks, and logging every run to an SQLite datastore.

---

## Key Features & Capabilities

### 1. Dual-Tier Pre-Execution Scanning

- **Dry-Run Analysis:** Before downloading a single byte, inspect the remote folder tree and view an actionable execution plan.
- **Smart Path Preview:** Displays both original polluted remote paths and cleaned target local paths side-by-side.

### 2. Context-Aware Bloat Filtration

- **Pattern Match Engine:** Targets and prunes known promotional keywords (`*bonus*`, `*500$*`, `*reels bundle*`, `*join telegram*`, `*vip access*`).
- **Context Preservation:** Distinguishes between spam markers and valid curriculum topics (e.g., retains `WhatsApp Marketing Blueprint` while stripping `Join WhatsApp Group for Freebies`).

### 3. Automated Path & File Sanitization

- **Domain & Handle Stripping:** Removes domain patterns (`*.vercel.app`, `*.blogspot.com`), URL paths, social handles (`@ChannelName`), and bracket noise.
- **Subfolder Collapse:** Detects duplicate nested folder hierarchies generated during Google Drive batch packaging and flattens them into clean directories.

### 4. Magic-Byte Binary Extension Repair

- **File Signature Inspection:** Automatically reads binary magic numbers from initial file bytes after transfer.
- **Automatic Renaming:** Restores missing or incorrect extensions for common media types, including:
  - **Video:** `.mp4`, `.mkv`, `.avi`, `.mov`
  - **Audio:** `.mp3`, `.wav`, `.aac`
  - **Archives:** `.zip`, `.tar.gz`, `.7z`, `.rar`
  - **Documents:** `.pdf`, `.docx`
  - **Images:** `.png`, `.jpg`, `.webp`

### 5. Idempotent Disk Synchronization

- **Stem-Based Fuzzy Comparison:** Compares incoming files against existing disk files in `./downloads` using fuzzy stem normalization.
- **Bandwidth & Storage Protection:** Prevents duplicate downloads even if previous runs applied different naming sanitization rules.

### 6. Real-Time SSE Dashboard

- **Reactive Glassmorphism Interface:** Built with Alpine.js and Tailwind CSS with real-time UI updates.
- **Dual Progress Metrics:** Real-time visibility into overall batch completion and active single-file transfer progress.
- **Live Event Stream:** Direct Server-Sent Events (SSE) pipe log output from the backend straight into the UI console.

### 7. SQLite Audit & Execution History

- **Persistent State:** Tracks job metrics, timestamps, targeted links, files processed, and statuses in `.aether_history.db`.
- **Slide-out History Drawer:** Review previous jobs, re-inject folder URLs into the scan input with one click, copy links, or selectively purge history records.

---

## Deep-Dive Architecture

```text
aetherdrive/
├── backend/
│   ├── app.py              # FastAPI application server, SSE pub/sub, SQLite operations
│   ├── core.py             # Heuristic parser, regex sanitizers, magic-byte inspection
│   └── requirements.txt    # Python runtime dependencies
├── frontend/
│   └── index.html          # Alpine.js state engine with Tailwind CSS interface
├── downloads/              # Mounted volume for completed and processed files
├── docker-compose.yml      # Container orchestration specification
└── Dockerfile              # Multi-architecture container build recipe
```

### Data Flow Lifecycle

```text
[Google Drive URL]
        │
        ▼
[FastAPI /api/scan] ──► [Inspect Folder Metadata & Structure]
        │
        ▼
[Engine Sanitization] ──► Strip Domains, URL Watermarks & Bloat Patterns
        │
        ▼
[Local Disk Comparison] ──► Match against existing files in ./downloads
        │
        ▼
[Frontend Review] ──► Visual Plan Display (Download vs Skipped)
        │
        ▼ (User Confirms Ingestion)
[FastAPI /api/download] ──► Background Download Worker
        │
        ├──► Real-Time SSE Logs & Counters ──► [Browser UI]
        │
        ▼
[Post-Download Magic Byte Repair] ──► Inspect Hex Signatures & Fix Extensions
        │
        ▼
[SQLite History DB] ──► Record Execution Log (.aether_history.db)
```

---

## Deployment & Quick Start

### Option 1: Docker Compose (Recommended)

Save the following configuration as `docker-compose.yml`:

```yaml
services:
  aetherdrive:
    image: ghcr.io/riyaz-hussain/aetherdrive:latest
    container_name: aetherdrive
    ports:
      - "8080:8080"
    volumes:
      - ./downloads:/downloads
    restart: unless-stopped
```

Start the service:

```bash
docker compose up -d
```

### Option 2: Docker CLI

```bash
docker run -d \
  --name aetherdrive \
  -p 8080:8080 \
  -v $(pwd)/downloads:/downloads \
  --restart unless-stopped \
  ghcr.io/riyaz-hussain/aetherdrive:latest
```

Open [http://localhost:8080](http://localhost:8080) in your web browser.

---

## User Interface & Dashboard Guide

### 1. The Ingestion Form

- **Folder Link:** Enter any public or shared Google Drive folder URL.
- **Exclude Folders:** Comma-separated wildcards to ignore entire subtrees (e.g., `*Bonus*, *Archive*`).
- **Exclude Files:** Pre-populated with heuristic defaults (`*500$*`, `*bundle*`, `*join telegram*`). Adjust as needed.

### 2. Execution Summary Cards

- **To Download:** Displays count and total estimated size of sanitized, approved files.
- **Skipped / Filtered:** Quantifies files bypassed due to bloat filters or disk-sync matching.

### 3. Execution Plan Breakdown

- **Green Badges (Download):** Highlights new items, showing sanitized folder paths and target file names.
- **Yellow/Red Badges (Skipped):** Explicitly labels why an item was ignored (e.g., `Matched Bloat Rule`, `File Already On Disk`).

### 4. Interactive Slide-Out History Drawer

Click **History** in the top-right header to slide open previous jobs.

- **Use Link:** Injects a historical URL directly into the scan input.
- **Copy Link:** Copies the folder URL to your system clipboard.
- **Delete / Clear All:** Cleanly manage individual or batch audit entries.

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

During post-processing, files missing a valid extension have their first 16 bytes inspected:

| File Type | Header Magic Bytes (Hex) | Restored Extension |
| :--- | :--- | :--- |
| MP4 Video | `....ftypmp42` or `....ftypisom` | `.mp4` |
| MKV Video | `1A 45 DF A3` | `.mkv` |
| ZIP Archive | `50 4B 03 04` | `.zip` |
| PDF Document | `25 50 44 46` (`%PDF`) | `.pdf` |
| PNG Image | `89 50 4E 47 0D 0A 1A 0A` | `.png` |
| JPEG Image | `FF D8 FF` | `.jpg` |

---

## API Reference

The backend exposes an asynchronous OpenAPI interface:

| Method | Route | Description |
| :--- | :--- | :--- |
| `POST` | `/api/scan` | Analyzes Drive folder link, runs filters, and returns an execution plan. |
| `POST` | `/api/download` | Initiates asynchronous background downloading for approved plan items. |
| `GET` | `/api/events` | SSE endpoint pushing real-time progress, counters, and log output. |
| `GET` | `/api/history` | Fetches historical runs from `.aether_history.db`. |
| `DELETE` | `/api/history/{id}` | Deletes a specific historical record. |
| `DELETE` | `/api/history` | Purges all records from the history database. |

---

## Configuration & Environment

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `PORT` | Integer | `8080` | Internal and exposed service port. |
| `/downloads` | Volume Path | Required | Target host filesystem directory for downloaded files. |
| `SQLITE_DB` | Path | `/downloads/.aether_history.db` | Location of the persistent audit database. |

---

## Troubleshooting & FAQ

**Q: The download fails with "Too many requests" or rate-limiting errors.**

A: Google Drive applies temporary quota restrictions on heavily shared public files. Wait a few hours or switch network paths. AetherDrive automatically implements exponential backoff retries when fetching metadata.

**Q: Why did a folder remain empty in the "Exclude Folders" input?**

A: The heuristic engine auto-populates terms only when it discovers known promotional markers. If promotional text is located entirely in file names, the file filter populates while the folder filter remains empty.

**Q: How do I change the downloads directory on my host machine?**

A: Edit the left side of the volume mapping in `docker-compose.yml`:

```yaml
volumes:
  - /Volumes/MyExternalDrive/Media:/downloads
```

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
