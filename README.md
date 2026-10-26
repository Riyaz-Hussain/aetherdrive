# AetherDrive

**Intelligent Cloud Ingestion Engine & Automated Content Sanitizer**

A self-hosted, containerized ingestion utility engineered to pull Google Drive directories, sanitize watermarks and domains, auto-repair missing extensions, contextually purge promotional bloat, and maintain an audit trail.

---

## Quick Start (Zero Build Required)

### Option 1: Docker Compose
Use the included docker-compose.yml file and run:
docker compose up -d

### Option 2: Docker CLI
docker run -d --name aetherdrive -p 8080:8080 -v $(pwd)/downloads:/downloads --restart unless-stopped ghcr.io/riyaz-hussain/aetherdrive:latest

Access the dashboard at http://localhost:8080

---

## Key Features
- Context-Aware Bloat Filtration: Auto-skips promotional bundles and bonuses while preserving real content.
- Metadata Sanitization: Strips spam watermarks, domains, and collapsed folder hierarchies.
- Magic Byte Repair: Auto-detects binary headers to restore missing video and archive extensions.
- Idempotent Disk Sync: Prevents duplicate downloads by matching stems against existing disk items.
- Real-Time SSE Streaming: Live progress tracking and transfer logs via Server-Sent Events.
- Persistent SQLite History: Search, reload, and manage past download tasks.

---

## License
MIT License. See LICENSE for details.
