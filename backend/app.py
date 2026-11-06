import os
import json
import sqlite3
import datetime
import asyncio
import threading
import errno
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.core import (
    DATA_DIR, DOWNLOAD_DIR, COOKIE_FILE,
    inspect_source, download_http_file, check_file_on_disk,
    repair_extension_magic_bytes, format_size, get_cookie_file_path
)
import gdown

app = FastAPI(title="AetherDrive")

DB_PATH = DATA_DIR / "aether_history.db"

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                folder_name TEXT,
                url TEXT,
                total_files INTEGER,
                downloaded_files INTEGER,
                skipped_files INTEGER,
                failed_files INTEGER,
                status TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exclusion_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT UNIQUE
            )
        """)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM exclusion_rules")
        if cursor.fetchone()[0] == 0:
            defaults = [
                "*trailer*", "*sample*", "*preview*", "*bonus*", "*promo*",
                "*reels*", "*short*", "*teaser*",
                "*.nfo", "*.txt", "*.url", "*.lnk", "*.exe"
            ]
            conn.executemany("INSERT OR IGNORE INTO exclusion_rules (pattern) VALUES (?)", [(d,) for d in defaults])
        conn.execute(
            "DELETE FROM exclusion_rules WHERE lower(pattern) IN (?, ?)",
            ("*advert*", "*watermark*")
        )
        conn.commit()

init_db()

class JobState:
    def __init__(self):
        self.is_running = False
        self.cancel_requested = False
        self.active_url = ""
        self.current_plan: Optional[Dict[str, Any]] = None
        self.master_items: List[Dict[str, Any]] = []
        self.logs: List[str] = []
        self.logs_generation = 0
        self.progress: Dict[str, Any] = {
            "current": 0,
            "total": 0,
            "percent": 0,
            "speed": "",
            "active_file": ""
        }
        self.completed_summary: Optional[Dict[str, Any]] = None
        self.cleanup_paths: Dict[str, tuple[int, int, int, int]] = {}
        self.cleanup_directories: Dict[str, tuple[int, int]] = {}
        self.download_queue: List[Dict[str, Any]] = []
        self.current_job: Optional[Dict[str, Any]] = None
        self.queue_runner: Optional[asyncio.Task] = None
        self.queue_paused = False
        self.resume_job: Optional[Dict[str, Any]] = None

state = JobState()
gdown_progress_lock = threading.Lock()

class DownloadCancelled(Exception):
    pass

def download_with_progress(url: str, output: Path, cookie_path: Optional[str], expected_size: int, file_index: int, total_files: int) -> Optional[str]:
    tqdm_module = gdown.download.__globals__["tqdm"]
    original_tqdm = tqdm_module.tqdm
    progress_bar_start = file_index / total_files * 100

    class ProgressBar:
        def __init__(self, total: Optional[int] = None, initial: int = 0, **kwargs: Any):
            self.total = total or expected_size or None
            self.n = initial
            self.update(0)

        def update(self, amount: int = 1):
            if state.cancel_requested:
                raise DownloadCancelled()
            self.n += amount
            fraction = min(self.n / self.total, 1) if self.total else None
            percent = (
                progress_bar_start + fraction / total_files * 100
                if fraction is not None
                else progress_bar_start
            )
            state.progress.update({
                "percent": min(int(percent), 99),
                "downloaded_bytes": self.n,
                "total_bytes": self.total,
                "indeterminate": self.total is None
            })

        def close(self):
            pass

    with gdown_progress_lock:
        tqdm_module.tqdm = ProgressBar
        try:
            return gdown.download(
                url=url,
                output=str(output),
                quiet=False,
                cookies_file=cookie_path
            )
        finally:
            tqdm_module.tqdm = original_tqdm

def download_http_with_progress(url: str, output: Path, source_url: str, file_index: int, total_files: int) -> str:
    progress_start = file_index / total_files * 100
    downloaded_bytes = 0

    def update_progress(amount: int):
        nonlocal downloaded_bytes
        if state.cancel_requested:
            raise DownloadCancelled()
        downloaded_bytes += amount
        state.progress.update({
            "percent": min(int(progress_start), 99),
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": None,
            "indeterminate": True
        })

    return download_http_file(url, output, source_url, update_progress)

class ScanRequest(BaseModel):
    url: str

class CookiePayload(BaseModel):
    cookie_content: str

class ReapplyRequest(BaseModel):
    destination_folder: str

class ExclusionsUpdate(BaseModel):
    patterns: List[str]

class DownloadRequest(BaseModel):
    destination_folder: str
    source_folder_name: Optional[str] = ""
    source_type: Optional[str] = "google_drive"
    timezone: Optional[str] = ""
    local_datetime: Optional[str] = ""
    url: Optional[str] = ""
    items: List[Dict[str, Any]]

# --- Cookie Management Endpoints ---

@app.get("/api/cookies/status")
def get_cookie_status():
    has_cookies = COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 10
    file_size = COOKIE_FILE.stat().st_size if has_cookies else 0
    mod_time = ""
    if has_cookies:
        mtime = datetime.datetime.fromtimestamp(COOKIE_FILE.stat().st_mtime)
        mod_time = mtime.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "has_cookies": has_cookies,
        "file_size": file_size,
        "last_modified": mod_time
    }

@app.post("/api/cookies")
def save_cookies(payload: CookiePayload):
    text = payload.cookie_content.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        raise HTTPException(status_code=400, detail="Cookie content cannot be empty.")
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return {"status": "success", "message": "Cookies saved successfully.", "size": len(text)}

@app.delete("/api/cookies")
def delete_cookies():
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
    return {"status": "success", "message": "Cookies deleted. Session reverted to anonymous."}

# --- Audit & Exclusions Endpoints ---

@app.get("/api/exclusions")
def get_exclusions():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pattern FROM exclusion_rules ORDER BY id ASC")
        rows = cursor.fetchall()
        return {"exclusions": [r[0] for r in rows]}

@app.post("/api/exclusions")
def update_exclusions(payload: ExclusionsUpdate):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM exclusion_rules")
        conn.executemany("INSERT OR IGNORE INTO exclusion_rules (pattern) VALUES (?)", [(p.strip(),) for p in payload.patterns if p.strip()])
        conn.commit()
    return {"status": "success", "exclusions": payload.patterns}

@app.get("/api/history")
def get_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC")
        rows = cursor.fetchall()
        return {"history": [dict(r) for r in rows]}

@app.delete("/api/history/{record_id}")
def delete_history_record(record_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM audit_logs WHERE id = ?", (record_id,))
        conn.commit()
    return {"status": "success"}

@app.delete("/api/history")
def clear_all_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM audit_logs")
        conn.commit()
    return {"status": "success"}

# --- Scanning & Plan Management ---

@app.post("/api/scan")
def scan_folder(req: ScanRequest):
    if not state.is_running:
        if not state.queue_paused:
            preserve_canceled_actions = (
                state.completed_summary
                and state.completed_summary.get("status") == "Canceled"
                and (
                    state.completed_summary.get("cleanup_available")
                    or state.completed_summary.get("resume_available")
                )
            )
            if not preserve_canceled_actions:
                state.completed_summary = None
        state.progress = {
            "current": 0, "total": 0, "percent": 0, "speed": "--",
            "active_file": "", "downloaded_bytes": 0, "total_bytes": None,
            "indeterminate": False
        }
        state.logs_generation += 1
        state.logs = []
    state.current_plan = None
    state.master_items = []
    state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔎 Inspecting source URL...")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pattern FROM exclusion_rules")
        exclusions = [r[0] for r in cursor.fetchall()]

    try:
        plan = inspect_source(req.url, exclusions)
        state.active_url = req.url.strip()
        state.current_plan = plan
        state.master_items = plan["items"]
        source_label = "Google Drive folder" if plan.get("source_type") == "google_drive" else "website directory"
        state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 Scanned {source_label}: {plan['destination_folder']} ({len(plan['items'])} items found)")
        if plan.get("source_type") == "google_drive":
            if plan.get("cookie_active"):
                state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✓ Authenticated session active (cookies.txt applied)")
            elif get_cookie_file_path():
                state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ Saved cookies were rejected/empty — fell back to anonymous session.")
            else:
                state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ℹ Running with anonymous session (no cookies.txt found)")
        return plan
    except Exception as e:
        state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ Inspection failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reapply")
def reapply_rules(req: ReapplyRequest):
    if not state.master_items:
        raise HTTPException(status_code=400, detail="No active scan in memory to reapply.")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pattern FROM exclusion_rules")
        exclusions = [r[0] for r in cursor.fetchall()]

    from backend.core import matching_exclusions
    dest = req.destination_folder.strip()
    dest_exists = (DOWNLOAD_DIR / dest).exists()

    recalculated = []
    for item in state.master_items:
        raw_p = item["original_path"]
        size = item["size"]
        clean_sub = item["sanitized_path"]
        bloat_reasons = matching_exclusions(raw_p, exclusions)
        bloat = bool(bloat_reasons)
        if item.get("size_known") is False:
            on_disk = (DOWNLOAD_DIR / dest / clean_sub).is_file() if not bloat else False
        else:
            on_disk = check_file_on_disk(dest, clean_sub, size) if not bloat else False

        if bloat:
            cat = "bloat"
        elif on_disk:
            cat = "on_disk"
        else:
            cat = "approved"

        item["category"] = cat
        item["bloat_reasons"] = bloat_reasons
        item["selected"] = (cat == "approved")
        recalculated.append(item)

    state.master_items = recalculated
    if state.current_plan:
        state.current_plan["destination_folder"] = dest
        state.current_plan["destination_exists_on_disk"] = dest_exists
        state.current_plan["items"] = recalculated

    return state.current_plan

# --- SSE Streaming & Execution ---

@app.get("/api/events")
async def sse_stream():
    async def event_generator():
        last_log_count = 0
        last_logs_generation = state.logs_generation
        while True:
            if state.logs_generation != last_logs_generation:
                last_logs_generation = state.logs_generation
                last_log_count = 0
                yield {"event": "reset_logs", "data": "{}"}

            if len(state.logs) > last_log_count:
                new_logs = state.logs[last_log_count:]
                last_log_count = len(state.logs)
                for log in new_logs:
                    yield {"event": "log", "data": json.dumps({"message": log})}

            yield {
                "event": "status",
                "data": json.dumps({
                    "is_running": state.is_running,
                    "progress": state.progress,
                    "completed_summary": state.completed_summary,
                    "current_job": state.current_job,
                    "queue_paused": state.queue_paused,
                    "download_queue": [
                        {
                            "id": job["id"],
                            "folder": job["folder"],
                            "total": len(job["items"])
                        }
                        for job in state.download_queue
                    ]
                })
            }
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())

def download_relative_path(path: Path) -> Optional[str]:
    root = DOWNLOAD_DIR.resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return None

def ensure_download_directories(path: Path) -> Dict[str, tuple[int, int]]:
    root = DOWNLOAD_DIR.resolve()
    try:
        relative_path = path.resolve().relative_to(root)
    except ValueError:
        path.mkdir(parents=True, exist_ok=True)
        return {}

    directories = {}
    current = root
    for part in relative_path.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            if not current.is_dir():
                raise
        else:
            stat = current.stat()
            directories[str(current.relative_to(root))] = (stat.st_dev, stat.st_ino)
    return directories

def track_cleanup_file(path: Path):
    relative = download_relative_path(path)
    if relative and path.is_file() and not path.is_symlink():
        stat = path.stat()
        state.cleanup_paths[relative] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

def format_source_datetime(value: datetime.datetime) -> str:
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return f"{value.strftime('%B')} {value.day}, {value.year} at {hour}:{value.minute:02d}:{value.second:02d} {meridiem}"

async def run_download_worker(
    dest_folder: str,
    approved_items: List[Dict[str, Any]],
    original_url: str,
    created_source_file: Optional[Path] = None,
    created_source_directories: Optional[Dict[str, tuple[int, int]]] = None
):
    state.is_running = True
    state.cancel_requested = False
    state.completed_summary = None
    state.cleanup_paths = {}
    state.cleanup_directories = dict(created_source_directories or {})
    state.resume_job = None
    if created_source_file:
        track_cleanup_file(created_source_file)

    downloaded = 0
    skipped = 0
    failed = 0
    completed_ids = set()
    total = len(approved_items)
    state.progress = {
        "current": 0, "total": total, "percent": 0, "speed": "--",
        "active_file": "", "downloaded_bytes": 0, "total_bytes": None,
        "indeterminate": False
    }

    cookie_path = get_cookie_file_path()
    dest_root = Path(dest_folder.strip()) if dest_folder.strip() else Path("Downloads")
    state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 Task initiated: {total} files queued into folder '{dest_root}'.")

    for idx, item in enumerate(approved_items):
        if state.cancel_requested:
            state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛑 Task canceled by user.")
            break

        rel_sub = Path(item["sanitized_path"])
        
        if len(rel_sub.parts) > 1 and rel_sub.parts[0].lower() == dest_root.name.lower():
            target_path = DOWNLOAD_DIR / dest_root / Path(*rel_sub.parts[1:])
        else:
            target_path = DOWNLOAD_DIR / dest_root / rel_sub

        state.cleanup_directories.update(ensure_download_directories(target_path.parent))
        existing_entries = set(target_path.parent.iterdir())
        target_preexisting = target_path in existing_entries

        state.progress["current"] = idx + 1
        state.progress["percent"] = int((idx / total) * 100)
        state.progress["downloaded_bytes"] = 0
        state.progress["total_bytes"] = item.get("size") or None
        state.progress["indeterminate"] = not bool(item.get("size"))
        state.progress["active_file"] = target_path.name
        state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⬇️ Downloading ({idx+1}/{total}): {target_path.name}")
        item_finished = False

        try:
            if item.get("source_type") == "http":
                output = await asyncio.to_thread(
                    download_http_with_progress,
                    item["download_url"],
                    target_path,
                    original_url or state.active_url,
                    idx,
                    total
                )
            else:
                url = f"https://drive.google.com/uc?id={item['id']}"
                output = await asyncio.to_thread(
                    download_with_progress,
                    url,
                    target_path,
                    cookie_path,
                    item.get("size") or 0,
                    idx,
                    total
                )

            item_finished = True
            if output and Path(output).exists():
                repaired = repair_extension_magic_bytes(Path(output))
                if not target_preexisting and repaired not in existing_entries:
                    track_cleanup_file(repaired)
                downloaded += 1
                completed_ids.add(item["id"])
                state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Completed: {repaired.name}")
            else:
                failed += 1
                state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ Failed: {target_path.name} | Reason: Rate limit or quota block")
        except DownloadCancelled:
            state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛑 Download canceled: {target_path.name}")
        except Exception as err:
            failed += 1
            state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ Error: {target_path.name} | {str(err)}")
        finally:
            for entry in target_path.parent.iterdir():
                if (
                    entry not in existing_entries
                    and entry.is_file()
                    and entry.name.startswith(target_path.name)
                    and entry.name.endswith(".part")
                ):
                    track_cleanup_file(entry)
            if item_finished:
                state.progress["percent"] = int(((idx + 1) / total) * 100)
            state.progress["indeterminate"] = False

        if state.cancel_requested:
            state.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🛑 Task canceled by user.")
            break

    state.is_running = False
    state.progress["indeterminate"] = False
    remaining_items = [item for item in approved_items if item["id"] not in completed_ids]
    if state.cancel_requested and remaining_items:
        state.resume_job = {
            "id": uuid.uuid4().hex,
            "folder": str(dest_root),
            "items": remaining_items,
            "url": original_url or state.active_url,
            "created_source_file": None,
            "created_source_directories": {}
        }
    state.completed_summary = {
        "folder": str(dest_root),
        "total": total,
        "downloaded": downloaded,
        "failed": failed,
        "status": "Canceled" if state.cancel_requested else ("Success" if failed == 0 else "Completed with errors"),
        "resume_available": state.resume_job is not None,
        "resume_remaining": len(state.resume_job["items"]) if state.resume_job else 0,
        "cleanup_available": state.cancel_requested and bool(state.cleanup_paths or state.cleanup_directories),
        "cleanup_completed": False
    }

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO audit_logs (timestamp, folder_name, url, total_files, downloaded_files, skipped_files, failed_files, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            str(dest_root),
            original_url or state.active_url,
            total,
            downloaded,
            skipped,
            failed,
            state.completed_summary["status"]
        ))
        conn.commit()

@app.post("/api/download")
async def start_download(req: DownloadRequest):
    selected = [it for it in req.items if it.get("selected")]
    if not selected:
        raise HTTPException(status_code=400, detail="No files selected.")

    url_to_save = (req.url or state.active_url).strip()
    if not url_to_save:
        raise HTTPException(status_code=400, detail="No source URL is available for this download.")

    if req.local_datetime:
        try:
            local_timestamp = datetime.datetime.fromisoformat(req.local_datetime)
        except ValueError as err:
            raise HTTPException(status_code=400, detail="The supplied local timestamp is invalid.") from err
        if local_timestamp.utcoffset() is None:
            raise HTTPException(status_code=400, detail="The supplied local timestamp must include a UTC offset.")
    else:
        local_timestamp = datetime.datetime.now().astimezone()
    timezone_name = (req.timezone if req.local_datetime else None) or local_timestamp.tzname() or "Local"

    dest_root = Path(req.destination_folder.strip()) if req.destination_folder.strip() else Path("Downloads")
    source_url_path = DOWNLOAD_DIR / dest_root / "source_url.txt"
    source_file_is_new = not source_url_path.exists()
    try:
        source_directories_is_new = ensure_download_directories(source_url_path.parent)
        existing_content = source_url_path.read_text(encoding="utf-8") if source_url_path.exists() else ""
        existing_lines = existing_content.splitlines()
        matching_line = next(
            (
                index for index, line in enumerate(existing_lines)
                if line == url_to_save or line == f"URL: {url_to_save}"
            ),
            None
        )
        if matching_line is None or existing_lines[matching_line] == url_to_save:
            source_folder_name = (req.source_folder_name or "").strip() or req.destination_folder.strip()
            utc_timestamp = local_timestamp.astimezone(datetime.timezone.utc)
            offset_seconds = int(local_timestamp.utcoffset().total_seconds())
            offset_sign = "+" if offset_seconds >= 0 else "-"
            offset_hours, offset_remainder = divmod(abs(offset_seconds), 3600)
            offset_minutes = offset_remainder // 60
            source_entry = [
                f"{'Google Drive folder' if req.source_type == 'google_drive' else 'Website directory'}: {source_folder_name}",
                f"Request queued (local time): {format_source_datetime(local_timestamp)}",
                f"Time zone: {timezone_name} (UTC{offset_sign}{offset_hours:02d}:{offset_minutes:02d})",
                f"Same time in UTC: {format_source_datetime(utc_timestamp)} UTC",
                f"Source URL: {url_to_save}"
            ]
            if matching_line is not None:
                existing_lines[matching_line:matching_line + 1] = source_entry
                updated_content = "\n".join(existing_lines)
                if existing_content.endswith("\n"):
                    updated_content += "\n"
                source_url_path.write_text(updated_content, encoding="utf-8")
            else:
                with source_url_path.open("a", encoding="utf-8") as source_file:
                    if existing_content:
                        source_file.write("\n")
                    source_file.write("\n".join(source_entry) + "\n")
    except (OSError, UnicodeError) as err:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save the source URL in the destination folder: {err}"
        ) from err

    job = {
        "id": uuid.uuid4().hex,
        "folder": req.destination_folder.strip() or "Downloads",
        "items": selected,
        "url": url_to_save,
        "created_source_file": source_url_path if source_file_is_new else None,
        "created_source_directories": source_directories_is_new
    }
    state.download_queue.append(job)
    if not state.is_running and not state.queue_paused and (state.queue_runner is None or state.queue_runner.done()):
        state.queue_runner = asyncio.create_task(process_download_queue())
    queue_position = len(state.download_queue) + (1 if state.current_job else 0)
    if not (
        state.completed_summary
        and state.completed_summary.get("status") == "Canceled"
        and (state.completed_summary.get("cleanup_available") or state.completed_summary.get("resume_available"))
    ):
        state.completed_summary = None
    state.logs.append(
        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📥 Added '{job['folder']}' ({len(selected)} files) to download queue at position {queue_position}."
    )
    return {
        "status": "queued",
        "job_id": job["id"],
        "queued": len(selected),
        "queue_position": queue_position
    }

async def process_download_queue():
    while state.download_queue:
        job = state.download_queue.pop(0)
        state.current_job = {"id": job["id"], "folder": job["folder"], "total": len(job["items"])}
        try:
            await run_download_worker(
                job["folder"],
                job["items"],
                job["url"],
                job["created_source_file"],
                job["created_source_directories"]
            )
        except Exception as err:
            state.is_running = False
            state.progress["indeterminate"] = False
            state.completed_summary = {
                "folder": job["folder"],
                "total": len(job["items"]),
                "downloaded": 0,
                "failed": len(job["items"]),
                "status": "Completed with errors",
                "resume_available": False,
                "resume_remaining": 0,
                "cleanup_available": False,
                "cleanup_completed": False
            }
            state.logs.append(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ Queue job failed for '{job['folder']}': {err}"
            )
        finally:
            state.current_job = None
        if state.completed_summary and state.completed_summary.get("status") == "Canceled" and state.download_queue:
            state.queue_paused = True
            state.logs.append(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏸ Remaining downloads are paused. Resume the queue when ready."
            )
            break
    if not state.download_queue:
        state.queue_paused = False
    state.queue_runner = None

@app.post("/api/queue/start")
async def resume_download_queue():
    if state.is_running or state.current_job:
        raise HTTPException(status_code=400, detail="A download is already running.")
    if not state.download_queue:
        return {"status": "empty"}
    state.queue_paused = False
    if state.queue_runner is None or state.queue_runner.done():
        state.queue_runner = asyncio.create_task(process_download_queue())
    return {"status": "started", "queued": len(state.download_queue)}

@app.delete("/api/queue/{job_id}")
def remove_queued_download(job_id: str):
    queue_index = next(
        (index for index, job in enumerate(state.download_queue) if job["id"] == job_id),
        None
    )
    if queue_index is None:
        raise HTTPException(status_code=404, detail="That batch is no longer waiting in the queue.")

    job = state.download_queue.pop(queue_index)
    if not state.download_queue and not state.current_job:
        state.queue_paused = False
    state.logs.append(
        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🗑 Removed '{job['folder']}' from the download queue."
    )
    return {"status": "removed", "remaining": len(state.download_queue)}

@app.post("/api/download/resume-canceled")
async def resume_canceled_download():
    if state.is_running or state.current_job:
        raise HTTPException(status_code=400, detail="A download is already running.")
    if not state.resume_job or not state.resume_job["items"]:
        raise HTTPException(status_code=400, detail="There is no canceled download with unfinished files to resume.")

    job = state.resume_job
    state.resume_job = None
    state.download_queue.insert(0, job)
    state.queue_paused = False
    if state.queue_runner is None or state.queue_runner.done():
        state.queue_runner = asyncio.create_task(process_download_queue())
    if state.completed_summary:
        state.completed_summary["resume_available"] = False
    state.completed_summary = None
    state.logs.append(
        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ▶ Resuming '{job['folder']}' with {len(job['items'])} unfinished files."
    )
    return {"status": "started", "queued": len(job["items"])}

@app.post("/api/cancel")
def cancel_task():
    if not state.is_running:
        return {"status": "idle"}
    state.cancel_requested = True
    return {"status": "canceling"}

@app.post("/api/cleanup-canceled")
def cleanup_canceled_downloads():
    if state.is_running or state.current_job:
        raise HTTPException(status_code=400, detail="Cannot clean up while a download task is active.")
    if not state.completed_summary or state.completed_summary.get("status") != "Canceled":
        raise HTTPException(status_code=400, detail="There is no canceled download task to clean up.")
    if not state.completed_summary.get("cleanup_available"):
        raise HTTPException(status_code=400, detail="There are no newly created files or folders to remove.")

    root = DOWNLOAD_DIR.resolve()
    removed_files = 0
    removed_directories = 0
    for relative_path, expected_stat in sorted(state.cleanup_paths.items()):
        candidate = DOWNLOAD_DIR / relative_path
        try:
            candidate.resolve().relative_to(root)
        except ValueError as err:
            raise HTTPException(status_code=400, detail="Cleanup path is outside the downloads directory.") from err
        if candidate.is_symlink():
            continue
        if candidate.is_file():
            try:
                stat = candidate.stat()
                if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != expected_stat:
                    continue
                candidate.unlink()
                removed_files += 1
            except FileNotFoundError:
                continue
            except OSError as err:
                raise HTTPException(status_code=500, detail=f"Failed to remove file {relative_path}: {err}") from err

    for relative_path, expected_stat in sorted(
        state.cleanup_directories.items(),
        key=lambda item: len(Path(item[0]).parts),
        reverse=True
    ):
        candidate = DOWNLOAD_DIR / relative_path
        try:
            candidate.resolve().relative_to(root)
        except ValueError as err:
            raise HTTPException(status_code=400, detail="Cleanup folder is outside the downloads directory.") from err
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            stat = candidate.stat()
            if (stat.st_dev, stat.st_ino) != expected_stat:
                continue
            candidate.rmdir()
            removed_directories += 1
        except FileNotFoundError:
            continue
        except OSError as err:
            if err.errno != errno.ENOTEMPTY:
                raise HTTPException(status_code=500, detail=f"Failed to remove folder {relative_path}: {err}") from err

    state.cleanup_paths.clear()
    state.cleanup_directories.clear()
    state.resume_job = None
    state.completed_summary["cleanup_available"] = False
    state.completed_summary["cleanup_completed"] = True
    state.completed_summary["resume_available"] = False
    state.logs.append(
        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🧹 Removed {removed_files} created files and {removed_directories} empty folders."
    )
    return {
        "status": "success",
        "removed_files": removed_files,
        "removed_directories": removed_directories,
        "completed_summary": state.completed_summary
    }

@app.post("/api/reset")
def reset_workspace():
    if state.is_running or state.current_job or state.download_queue:
        raise HTTPException(status_code=400, detail="Cannot reset while downloads are running or queued.")
    state.current_plan = None
    state.master_items = []
    state.completed_summary = None
    state.resume_job = None
    state.logs = []
    return {"status": "cleared"}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
