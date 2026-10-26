import asyncio
import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.core import (
    parse_drive_folder,
    download_folder_plan,
    apply_heuristics,
    repair_extensions,
    fetch_folder_root_name,
    extract_drive_folder_id,
)

app = FastAPI(title="AetherDrive")

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/downloads"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "aether_history.db"

app.mount("/static", StaticFiles(directory=BASE_DIR / "frontend"), name="static")

event_subscribers: List[asyncio.Queue] = []
cancel_event = asyncio.Event()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            folder_url TEXT NOT NULL,
            total_files INTEGER DEFAULT 0,
            downloaded_files INTEGER DEFAULT 0,
            skipped_files INTEGER DEFAULT 0,
            status TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

async def broadcast_event(data: Dict[str, Any]):
    message = json.dumps(data)
    for q in event_subscribers:
        await q.put(message)

class ScanRequest(BaseModel):
    folder_url: str
    destination_folder: Optional[str] = ""
    exclusions: Optional[str] = ""

class DownloadRequest(BaseModel):
    plan: List[Dict[str, Any]]
    folder_url: str

class ExclusionsUpdate(BaseModel):
    exclusions: List[str]

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "frontend" / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

@app.get("/api/events")
async def events():
    queue: asyncio.Queue = asyncio.Queue()
    event_subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield dict(data=data)
        except asyncio.CancelledError:
            event_subscribers.remove(queue)

    return EventSourceResponse(event_generator())

@app.get("/api/settings/exclusions")
async def get_exclusions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'custom_exclusions'")
    row = cursor.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return []
    return []

@app.post("/api/settings/exclusions")
async def save_exclusions(req: ExclusionsUpdate):
    clean_list = list(set([x.strip() for x in req.exclusions if x.strip()]))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value) VALUES ('custom_exclusions', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (json.dumps(clean_list),))
    conn.commit()
    conn.close()
    return clean_list

@app.post("/api/scan")
async def scan_folder(req: ScanRequest):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'custom_exclusions'")
        row = cursor.fetchone()
        conn.close()

        stored_exclusions = []
        if row:
            try:
                stored_exclusions = json.loads(row[0])
            except Exception:
                pass

        merged_exclusions = list(set(
            stored_exclusions + [p.strip() for p in (req.exclusions or "").split(",") if p.strip()]
        ))

        folder_id = extract_drive_folder_id(req.folder_url)
        detected_root = fetch_folder_root_name(folder_id) if folder_id else ""
        root_name = req.destination_folder.strip() or detected_root

        raw_items = await parse_drive_folder(req.folder_url, custom_root=root_name)
        plan = apply_heuristics(
            raw_items,
            download_dir=DOWNLOAD_DIR,
            exclusions=",".join(merged_exclusions),
        )
        return {
            "root_folder_name": root_name,
            "plan": plan
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def run_download_job(plan: List[Dict[str, Any]], folder_url: str):
    global cancel_event
    cancel_event.clear()

    to_download = [p for p in plan if p.get("action") == "download"]
    total = len(to_download)
    downloaded = 0
    status = "completed"

    await broadcast_event({"type": "start", "total": total})

    try:
        for item in to_download:
            if cancel_event.is_set():
                status = "cancelled"
                await broadcast_event({"type": "log", "message": "⚠️ Download job was cancelled by user."})
                break

            target_path = DOWNLOAD_DIR / item["sanitized_path"]

            await broadcast_event({
                "type": "progress",
                "current_file": item["sanitized_path"],
                "index": downloaded + 1,
                "total": total,
                "percentage": int(((downloaded) / max(total, 1)) * 100)
            })

            success, detail = await download_folder_plan(item, target_path, cancel_event)

            if cancel_event.is_set():
                status = "cancelled"
                break

            if success:
                repair_extensions(target_path)
                downloaded += 1
                await broadcast_event({
                    "type": "log",
                    "message": f"✓ Saved: {item['sanitized_path']} ({detail})"
                })
            else:
                await broadcast_event({
                    "type": "log",
                    "message": f"❌ Failed: {item['sanitized_path']} | Reason: {detail}"
                })

        if status != "cancelled":
            if total > 0 and downloaded == 0:
                status = "failed"
            elif downloaded < total:
                status = "partial"
            else:
                status = "completed"

            await broadcast_event({
                "type": "finished",
                "status": status,
                "downloaded": downloaded,
                "total": total
            })
        else:
            await broadcast_event({
                "type": "cancelled",
                "status": "cancelled",
                "downloaded": downloaded,
                "total": total
            })

    except Exception as e:
        status = "failed"
        await broadcast_event({"type": "error", "message": str(e)})
    finally:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO history (timestamp, folder_url, total_files, downloaded_files, skipped_files, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            folder_url,
            total,
            downloaded,
            len(plan) - total,
            status
        ))
        conn.commit()
        conn.close()

@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_download_job, req.plan, req.folder_url)
    return {"status": "started"}

@app.post("/api/cancel")
async def cancel_download():
    global cancel_event
    cancel_event.set()
    await broadcast_event({"type": "log", "message": "🛑 Cancellation requested..."})
    return {"status": "cancelling"}

@app.get("/api/history")
async def get_history():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM history ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.delete("/api/history/{record_id}")
async def delete_history(record_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.delete("/api/history")
async def clear_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history")
    conn.commit()
    conn.close()
    return {"status": "cleared"}
