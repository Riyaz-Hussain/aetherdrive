import os
import re
import sqlite3
import datetime
import asyncio
from typing import List
from fastapi import FastAPI, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import gdown

from backend.core import (
    build_regex,
    auto_detect_skip_patterns,
    sanitize_name,
    sanitize_relative_path,
    clean_existing_disk_files,
    file_or_variant_exists,
    download_file_with_retry,
    detect_file_extension
)

app = FastAPI(title="AetherDrive")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
DB_PATH = os.path.join(DOWNLOAD_DIR, ".aether_history.db")

def init_db():
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    folder_url TEXT NOT NULL,
                    total_queued INTEGER NOT NULL,
                    total_skipped INTEGER NOT NULL,
                    downloaded_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    status TEXT NOT NULL
                )
            """)
            conn.commit()
    except Exception as e:
        print(f"DB Init Error: {e}")

init_db()

def log_job_start(url: str, queued: int, skipped: int) -> int:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO job_history (created_at, folder_url, total_queued, total_skipped, downloaded_count, failed_count, status)
                VALUES (?, ?, ?, ?, 0, 0, 'In Progress')
            """, (now, url, queued, skipped))
            conn.commit()
            return cursor.lastrowid
    except Exception:
        return 0

def update_job_finish(job_id: int, downloaded: int, failed: int):
    if not job_id:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            status = "Completed" if failed == 0 else ("Partial" if downloaded > 0 else "Failed")
            cursor.execute("""
                UPDATE job_history 
                SET downloaded_count = ?, failed_count = ?, status = ?
                WHERE id = ?
            """, (downloaded, failed, status, job_id))
            conn.commit()
    except Exception as e:
        print(f"DB Update Error: {e}")

def get_recent_history(limit: int = 50):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM job_history ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(ix) for ix in rows]
    except Exception:
        return []

def delete_single_history(item_id: int):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM job_history WHERE id = ?", (item_id,))
            conn.commit()
            return True
    except Exception:
        return False

def clear_all_history_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM job_history")
            conn.commit()
            return True
    except Exception:
        return False

event_queue = asyncio.Queue()

async def emit_event(event_type: str, data: dict):
    await event_queue.put({"type": event_type, "data": data})

class ScanRequest(BaseModel):
    url: str
    skip_folder: str = ""
    skip_file: str = ""

class DownloadItem(BaseModel):
    id: str
    original_path: str
    clean_path: str

class DownloadRequest(BaseModel):
    url: str
    items: List[DownloadItem]
    skipped_count: int = 0

@app.get("/api/history")
async def fetch_history():
    history = await asyncio.to_thread(get_recent_history)
    return {"success": True, "history": history}

@app.delete("/api/history/{item_id}")
async def delete_history_item(item_id: int):
    ok = await asyncio.to_thread(delete_single_history, item_id)
    return {"success": ok}

@app.delete("/api/history")
async def clear_all_history():
    ok = await asyncio.to_thread(clear_all_history_db)
    return {"success": ok}

@app.post("/api/scan")
async def scan_folder(req: ScanRequest):
    try:
        try:
            cleaned_actions = await asyncio.to_thread(clean_existing_disk_files, DOWNLOAD_DIR)
            for act in cleaned_actions:
                await emit_event("log", {"level": "SUCCESS", "text": f"[*] Local storage sync: {act}"})
        except Exception as disk_err:
            await emit_event("log", {"level": "INFO", "text": f"[*] Disk scan note: {str(disk_err)}"})

        await emit_event("log", {"level": "INFO", "text": "Fetching directory metadata from Google Drive..."})

        files = await asyncio.to_thread(
            gdown.download_folder,
            url=req.url,
            output=DOWNLOAD_DIR,
            skip_download=True,
            quiet=True
        )

        if not files:
            await emit_event("log", {"level": "ERROR", "text": "No files found or unable to access folder."})
            return {"success": False, "error": "No files found or unable to access folder. Ensure folder is public."}

        auto_folder_pattern, auto_file_pattern = auto_detect_skip_patterns(files)

        active_folder_filter = req.skip_folder.strip() or auto_folder_pattern
        active_file_filter = req.skip_file.strip() or auto_file_pattern

        if not req.skip_folder.strip() and auto_folder_pattern:
            await emit_event("log", {"level": "INFO", "text": f"Auto-detected folder filters: {auto_folder_pattern}"})
        if not req.skip_file.strip() and auto_file_pattern:
            await emit_event("log", {"level": "INFO", "text": f"Auto-detected file filters: {auto_file_pattern}"})

        skip_folder_regex = build_regex(active_folder_filter)
        skip_file_regex = build_regex(active_file_filter)

        to_download = []
        to_skip = []

        for f in files:
            raw_rel = f.path.replace("\\", "/")
            rel_folder = os.path.dirname(raw_rel)
            file_name = os.path.basename(raw_rel)

            if skip_folder_regex and rel_folder:
                parts = [p for p in rel_folder.split("/") if p]
                if skip_folder_regex.search(rel_folder) or any(skip_folder_regex.search(p) for p in parts):
                    to_skip.append({
                        "original_path": f.path,
                        "clean_path": sanitize_relative_path(f.path),
                        "reason": "Folder Filter Match"
                    })
                    continue

            if skip_file_regex and skip_file_regex.search(file_name):
                to_skip.append({
                    "original_path": f.path,
                    "clean_path": sanitize_relative_path(f.path),
                    "reason": "File Filter Match"
                })
                continue

            clean_rel = sanitize_relative_path(f.path)
            clean_target = os.path.join(DOWNLOAD_DIR, clean_rel)

            exists, matched_path = file_or_variant_exists(clean_target)
            if exists:
                to_skip.append({
                    "original_path": f.path,
                    "clean_path": clean_rel,
                    "reason": f"Already Exists ({os.path.basename(matched_path)})"
                })
                continue

            to_download.append({
                "id": f.id,
                "original_path": f.path,
                "clean_path": clean_rel
            })

        return {
            "success": True,
            "auto_detected_folders": auto_folder_pattern,
            "auto_detected_files": auto_file_pattern,
            "download": to_download,
            "skip": to_skip
        }

    except Exception as e:
        err_msg = str(e)
        await emit_event("log", {"level": "ERROR", "text": f"Scan failed: {err_msg}"})
        return {"success": False, "error": err_msg}

async def run_downloads_task(job_id: int, items: List[DownloadItem]):
    total = len(items)
    await emit_event("log", {"level": "INFO", "text": f"Starting batch download for {total} files..."})
    await emit_event("progress", {"current": 0, "total": total, "percent": 0, "status": "Starting..."})

    success_count = 0
    fail_count = 0

    for idx, item in enumerate(items, 1):
        target_file = os.path.join(DOWNLOAD_DIR, item.clean_path)
        try:
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
        except Exception:
            pass

        clean_filename = os.path.basename(item.clean_path)
        percent = int(((idx - 1) / total) * 100)

        await emit_event("progress", {
            "current": idx,
            "total": total,
            "percent": percent,
            "file": clean_filename,
            "status": f"Downloading ({idx}/{total}): {clean_filename}"
        })
        await emit_event("log", {"level": "INFO", "text": f"[{idx}/{total}] Downloading: {clean_filename}"})

        try:
            ok, err = await asyncio.to_thread(download_file_with_retry, item.id, target_file)
        except Exception as dl_ex:
            ok, err = False, str(dl_ex)

        if ok:
            success_count += 1
            has_ext = bool(re.search(r"\.[a-zA-Z0-9]{2,5}\$", os.path.basename(target_file)))
            if not has_ext and os.path.exists(target_file):
                ext = detect_file_extension(target_file)
                if ext:
                    new_p = target_file + ext
                    if not os.path.exists(new_p):
                        try:
                            os.rename(target_file, new_p)
                            await emit_event("log", {"level": "SUCCESS", "text": f"  ↳ Repaired extension: {os.path.basename(new_p)}"})
                        except Exception:
                            pass
            await emit_event("log", {"level": "SUCCESS", "text": f"✓ Saved: {clean_filename}"})
        else:
            fail_count += 1
            await emit_event("log", {"level": "ERROR", "text": f"✗ Failed: {clean_filename} ({err})"})

        final_percent = int((idx / total) * 100)
        await emit_event("progress", {
            "current": idx,
            "total": total,
            "percent": final_percent,
            "file": clean_filename,
            "status": "Completed" if idx == total else "Processing..."
        })

    await asyncio.to_thread(update_job_finish, job_id, success_count, fail_count)
    await emit_event("log", {"level": "INFO", "text": f"Batch complete. Downloaded {success_count} of {total} files."})

@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = await asyncio.to_thread(log_job_start, req.url, len(req.items), req.skipped_count)
    background_tasks.add_task(run_downloads_task, job_id, req.items)
    return {"status": "started", "count": len(req.items), "job_id": job_id}

@app.get("/api/events")
async def sse_events():
    async def event_generator():
        import json
        while True:
            evt = await event_queue.get()
            yield {"data": json.dumps(evt)}

    return EventSourceResponse(event_generator())

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
