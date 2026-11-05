<div align="center">

# AetherDrive

### Inspect, organize, and download Google Drive folders

[![Docker Image](https://img.shields.io/badge/Docker-GHCR-2496ED?logo=docker&logoColor=white)](https://github.com/riyaz-hussain/aetherdrive/pkgs/container/aetherdrive)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

AetherDrive is a self-hosted web application for inspecting accessible Google Drive folders and selectively downloading their contents. It cleans noisy file paths, identifies files that appear to be present in the destination already, lets you filter promotional or unwanted items, and records completed download runs for later reference.

The application is intended for folders and files you are authorized to access and download. Google Drive availability, rate limits, and access controls still apply.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start with Docker Compose](#quick-start-with-docker-compose)
- [Using AetherDrive](#using-aetherdrive)
- [Files and persistent data](#files-and-persistent-data)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Local development](#local-development)
- [Security and privacy](#security-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [License and acknowledgements](#license-and-acknowledgements)

## Features

- **Inspect before downloading.** Scan a Google Drive folder and review the discovered files before starting a transfer.
- **Clean up folder and file names.** Remove common URL/domain text, `@handles`, copy labels, bracketed annotations, and repeated adjacent path components from local paths.
- **Filter unwanted content.** Use editable, persistent wildcard rules to classify promotional or otherwise unwanted files.
- **Check the destination for existing files.** Compare scanned items with files under the selected destination and classify likely matches as already on disk.
- **Choose what to download.** Filter the plan by category, select individual items or all visible items, and execute the selected batch.
- **Rename the destination.** Set the folder name under the downloads directory before executing; reapply the plan to recalculate disk and exclusion categories.
- **Monitor a running batch.** View the current file, progress, and streamed log messages in the web interface. Cancel requests take effect between files.
- **Clean up canceled batches.** After cancellation, optionally remove files created by that run and directories that are empty afterward. Pre-existing files are preserved.
- **Reuse authorized Google Drive access.** Optionally provide a Netscape-format `cookies.txt` through the UI. The app attempts the scan with those cookies and falls back to anonymous access if that attempt does not return files.
- **Keep a run history.** Review completed batch results, search history by folder, URL, status, or date, copy or reuse a link, and remove individual or all history entries.
- **Keep source details with the files.** Each accepted download task records the Google Drive folder name, a readable local start time with timezone and UTC offset, the equivalent UTC time, and source URL in `source_url.txt` in the destination folder. Repeated links are not duplicated.
- **Use the responsive web UI.** On smaller screens the file plan switches from a table to cards.
- **Repair some file extensions.** After a successful download, inspect known file signatures and correct a mismatched or missing extension when the target extension is available.

## How it works

1. The browser sends the supplied folder URL to the FastAPI service.
2. The backend uses `gdown` to inspect the folder, optionally retrying without cookies if the cookie-based scan returns no files. No Google Cloud API key is configured by the app. It also requests the folder page to try to obtain a display title.
3. Each discovered path is sanitized and classified as **Approved**, **On Disk**, or **Bloat** using the saved wildcard rules and a local disk check.
4. The user reviews the plan, edits the destination if needed, selects files, and starts a batch.
5. The backend downloads selected files sequentially into `DOWNLOAD_DIR/<destination-folder>/`, streams events to the browser using Server-Sent Events (SSE), and attempts supported extension repairs.
6. When the worker finishes, the run is recorded in SQLite and appears in History.

The plan, active worker state, progress, and live logs are held in server memory. They are not restored after a server/container restart. The history database, exclusion rules, cookie file, and downloaded files are separate persistent data.

## Quick start with Docker Compose

### Requirements

- Docker Engine with the Compose plugin, or a compatible Docker host such as Portainer.
- A host with network access to Google Drive and the GitHub Container Registry.
- Writable host directories for downloads and AetherDrive application data.

Create a working directory and persistent folders:

```bash
mkdir -p aetherdrive/downloads aetherdrive/appdata
cd aetherdrive
```

Create `docker-compose.yml`:

```yaml
services:
  aetherdrive:
    image: ghcr.io/riyaz-hussain/aetherdrive:latest
    container_name: aetherdrive
    ports:
      - "8070:8070"
    environment:
      DATA_DIR: /appdata
      DOWNLOAD_DIR: /downloads
      TZ: Asia/Kolkata
    volumes:
      - ./downloads:/downloads
      - ./appdata:/appdata
    restart: unless-stopped
```

Start the service and view its logs:

```bash
docker compose up -d
docker compose logs -f aetherdrive
```

Open `http://<docker-host-address>:8070` in a browser. The service listens on port `8070`.

The example uses paths relative to the Compose file. For a NAS or another host, replace `./downloads` and `./appdata` with the actual host paths and ensure the container can write to both.
History stores timestamps in UTC and formats them in each viewer's browser timezone. `source_url.txt` follows the same UTC source-of-truth and shows both the downloader-local time with its timezone and offset and a readable UTC equivalent. The browser sends its local time and UTC offset, so this works without timezone data on the server. `TZ` is used as a fallback for API clients that do not send local time.

### Update the image

To pull the latest published image and recreate the container:

```bash
docker compose pull
docker compose up -d
```

The repository's GitHub Actions workflow publishes container images to GHCR for pushes to `main`, version tags, and manual workflow runs. The workflow builds for `linux/amd64` and `linux/arm64`.

## Using AetherDrive

1. **Inspect a folder:** Paste a Google Drive folder link into the URL field and choose **Inspect & Plan**. The folder must be accessible to the app, either anonymously or with a valid cookie session.
2. **Review the plan:** The counters and tabs show all scanned items, approved items, items considered on disk, and items matched by exclusion rules. Review paths, sizes, and statuses before proceeding.
3. **Choose a destination:** Edit the destination directory name. Downloads are placed underneath the configured downloads directory. Use **Re-Apply** after changing the destination or exclusion rules to recalculate the plan against disk.
4. **Select files:** Check individual items, or use the select-all checkbox to select all items in the current category view. Items classified as on disk or bloat are not selected by default, but can be selected in the plan.
5. **Execute or cancel:** Choose **Execute** to start a batch. The live stream shows the current file and log events. **Cancel** stops an in-progress transfer at the next received download chunk and prevents remaining files from starting. After cancellation, choose **Remove Created Files** to remove files created by that run and any folders that are empty afterward. Existing files are kept.
6. **Review history:** Open **History** to search and review completed runs. Copy a stored link, use it to start a new inspection, or delete history records.
7. **Start another task:** Use **Start New Task** in the completion banner to reset the current in-memory workspace.

### Exclusions

The Exclusions dialog accepts one wildcard pattern per line. Matching is case-insensitive and uses filename/path wildcard matching. Saving the rules replaces the active rule list, which is stored in the application database. The built-in defaults used when the rule table is first initialized include:

```text
*trailer*    *sample*      *preview*    *bonus*
*promo*      *reels*       *short*      *teaser*
*advert*     *watermark*   *.nfo        *.txt
*.url        *.lnk          *.exe
```

Rules classify items as **Bloat**; they do not delete files. You can edit or remove patterns in the Exclusions dialog.

### Path cleanup and disk matching

The path cleaner removes common URL/domain strings, `@handles`, `Copy of` labels, and text inside square or round brackets. It also normalizes repeated punctuation/spacing and collapses immediately repeated directory names.

The on-disk check looks for the sanitized relative path inside the selected destination. A file is treated as present when its size matches the scanned size or differs by less than 1 KiB. For a Drive item reported as zero bytes, an existing local file is considered present if it has a non-zero size. This is a practical heuristic, not a content hash; files with matching paths and similar sizes are not byte-for-byte verified.

After a successful download, extension repair recognizes PNG, JPEG, GIF, PDF, ZIP, RAR, 7z, MP4, MKV, and AVI signatures. Files shorter than 16 bytes are not inspected, and only a recognized signature can trigger a rename.

## Files and persistent data

| Container path | Contents | Persistence |
| --- | --- | --- |
| `/downloads` | Downloaded folder trees and each task's `source_url.txt` | Persist with a host mount or named volume |
| `/appdata/aether_history.db` | Execution history and exclusion rules | Persist with a host mount or named volume |
| `/appdata/cookies.txt` | Optional Google Drive cookie session | Persist with a host mount or named volume |

History is retained indefinitely unless entries are deleted through the UI or API. A history record is written when a download worker completes; a scan by itself does not create a history entry. The UI displays history timestamps in the browser's local timezone, and `source_url.txt` records the downloading user's timezone.
Run outcomes are recorded as `Success`, `Completed with errors`, or `Canceled`.

Back up both persistent locations if you want to preserve downloads and app data. Stop the container before making a raw SQLite database copy.

### Missing history after changing or updating deployments

The current application reads its database from `DATA_DIR/aether_history.db`, normally `/appdata/aether_history.db`. If the host-side `/appdata` mapping changes or points at a new empty directory, the app creates a new database and History will appear empty. The app does not search for or automatically migrate databases from older paths or schemas.

Do not delete the previous Docker volume or data directory if entries appear to be missing. Stop the service, back up the old data, and verify that the currently mounted host directory contains the database before attempting a migration. Different releases may have used different database filenames, directories, or table schemas.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `DOWNLOAD_DIR` | `/downloads` | Container directory used for destination folders and downloaded files. Mount persistent host storage here. |
| `DATA_DIR` | `/appdata` | Container directory used for the SQLite database and `cookies.txt`. Mount persistent host storage here. |

The application listens on port `8070`; the current container command uses that port. Map the host port to container port `8070` in Compose. There is no separate runtime port setting in the current server command.

## API reference

FastAPI's interactive API documentation is available at `/docs` while the service is running.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/cookies/status` | Return whether a cookie file exists, its size, and modification time. |
| `POST` | `/api/cookies` | Save cookie text from `{"cookie_content":"..."}`. |
| `DELETE` | `/api/cookies` | Delete the saved cookie file. |
| `GET` | `/api/exclusions` | Return the active wildcard rules. |
| `POST` | `/api/exclusions` | Replace rules using `{"patterns":["*sample*"]}`. |
| `POST` | `/api/scan` | Inspect a folder using `{"url":"https://drive.google.com/drive/folders/..."}`. |
| `POST` | `/api/reapply` | Re-evaluate the current in-memory scan with `{"destination_folder":"..."}`. |
| `POST` | `/api/download` | Start a batch from `destination_folder`, optional `url`, and an `items` array; items with `selected: true` are queued. |
| `POST` | `/api/cancel` | Request cancellation of the active batch. |
| `POST` | `/api/cleanup-canceled` | Remove files and folders tracked as newly created by the canceled batch. |
| `POST` | `/api/reset` | Clear the in-memory workspace when no download is running. |
| `GET` | `/api/events` | Receive `log` and `status` events over SSE. |
| `GET` | `/api/history` | Return all audit records, newest first. |
| `DELETE` | `/api/history/{record_id}` | Delete one history record. |
| `DELETE` | `/api/history` | Delete all history records. |

Example scan request:

```bash
curl -X POST http://localhost:8070/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://drive.google.com/drive/folders/your-folder-id"}'
```

The scan response includes a destination folder name, destination existence status, cookie-use status, and item records. Each item includes its Drive ID, original and sanitized paths, size, category, and selection state. The server rejects scans during an active download and rejects download requests when no items are selected.

The download endpoint returns after queueing the worker; it does not wait for the batch to finish. The worker writes the audit record after finishing its loop. `GET /api/history` returns an object with a `history` array. The SSE `log` event contains a message; the `status` event includes `is_running`, `progress`, and `completed_summary`. API responses can change as the application evolves; `/docs` reflects the running version.

## Local development

The container build uses the repository-root `requirements.txt`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p downloads appdata
export DOWNLOAD_DIR="$PWD/downloads"
export DATA_DIR="$PWD/appdata"
uvicorn backend.app:app --host 0.0.0.0 --port 8070 --reload
```

Open `http://localhost:8070`. The browser UI loads Alpine.js, Tailwind CSS, and Font Awesome from public CDNs, so the browser needs network access to those assets.

To build the image from source:

```bash
docker build -t aetherdrive:local .
```

The main UI is `frontend/index.html`. A legacy `frontend/index copy.html` file also remains in the repository; the root URL serves `index.html`.

## Security and privacy

- **No built-in user authentication or HTTPS:** Do not expose this service directly to an untrusted network or the public internet. Restrict access with a firewall or place it behind an appropriately configured authenticated HTTPS reverse proxy.
- **Protect cookie data:** A pasted Google Drive cookie file is stored as plaintext at `DATA_DIR/cookies.txt`. Treat it like a credential, limit access to the host data directory, and remove it from the UI when no longer needed.
- **Protect persistent data:** The database contains Drive URLs and execution metadata. Ensure `/appdata` backups and host permissions are appropriate for your environment.
- **Review links and content:** Only use folders and files you have permission to access and download. Respect copyright, privacy, and Google Drive terms.
- **External dependencies:** Folder inspection and downloads depend on Google Drive and `gdown`; browser styling and behavior depend on CDN-hosted front-end libraries. Changes to those services can affect availability.

## Troubleshooting

| Symptom | Things to check |
| --- | --- |
| A folder cannot be scanned or returns no files | Confirm the URL points to a folder, the folder is accessible to the app, and Google Drive has not rate-limited the request. Try a current cookie export for an account that can access it. |
| Cookie-based access is not used | Re-export a Netscape-format `cookies.txt` while signed in to Google Drive, paste it into the Cookie Session dialog, and confirm the cookie status. Cookies can expire or be rejected; the app can fall back to anonymous access. |
| Files are classified as **On Disk** | Review the sanitized path and size. Re-Apply checks the selected destination again. Matching is path/size based, not a content hash. |
| Expected files are in **Bloat** | Check the active wildcard patterns in Exclusions and remove or adjust any matching rule, then re-apply. |
| A download fails | Check the live execution log and Google Drive access/quota status. Some files may be unavailable, rate-limited, or inaccessible to the configured account. |
| History is empty after an update | Check the `/appdata` host mount and the `aether_history.db` file. The app does not migrate from old volume locations automatically; preserve the old data before changing mounts. |
| The UI loads without styling or icons | Check browser access to the Tailwind, Alpine.js, and Font Awesome CDNs and reload the page. |
| The app cannot save data | Confirm the host directories mapped to `/downloads` and `/appdata` exist and are writable by the container. |

For container diagnostics:

```bash
docker compose ps
docker compose logs --tail=200 aetherdrive
```

## Project structure

```text
.
├── backend/
│   ├── app.py                 # FastAPI routes, worker state, SSE, history and cookies
│   ├── core.py                # Drive inspection, path cleanup, exclusions and file checks
│   └── requirements.txt       # Backend dependency list
├── frontend/
│   ├── index.html             # Active single-page web interface
│   └── index copy.html        # Legacy page snapshot
├── .github/workflows/
│   └── docker-publish.yml     # Multi-architecture GHCR image workflow
├── Dockerfile
├── docker-compose.yml
├── requirements.txt           # Dependencies used by the container and local setup
└── LICENSE
```

## License and acknowledgements

AetherDrive is distributed under the [MIT License](LICENSE).

The project uses FastAPI, Uvicorn, `gdown`, `requests`, `sse-starlette`, Alpine.js, Tailwind CSS, and Font Awesome. Their respective licenses apply; consult their upstream projects for details.
