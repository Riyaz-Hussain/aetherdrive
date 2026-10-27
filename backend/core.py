import os
import re
import fnmatch
import asyncio
import subprocess
import html
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
import gdown

MAGIC_BYTE_SIGNATURES = [
    (4, b"ftyp", ".mp4"),
    (0, b"\x1a\x45\xdf\xa3", ".mkv"),
    (0, b"RIFF", ".avi"),
    (0, b"PK\x03\x04", ".zip"),
    (0, b"Rar!\x1a\x07", ".rar"),
    (0, b"7z\xbc\xaf\x27\x1c", ".7z"),
    (0, b"%PDF", ".pdf"),
    (0, b"\x89PNG\r\n\x1a\n", ".png"),
    (0, b"\xff\xd8\xff", ".jpg"),
    (0, b"ID3", ".mp3"),
]

DEFAULT_EXCLUDE_PATTERNS = [
    "*500$*",
    "*5000$*",
    "*all in one vip*",
    "*free course*",
    "*free courses*",
    "*join telegram*",
    "*join whatsapp*",
    "*vip access*",
    "*reels bundle*",
    "*giveaway*",
    "*crack*",
    "*keygen*",
    "*content owner*",
    "*owner channel*",
    "*bonus*",
]

URL_DOMAIN_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?[a-zA-Z0-9_\-]+\.(?:vercel\.app|blogspot\.com|com|net|org|io|me|xyz|top|live|site|info|online)\b",
    re.IGNORECASE,
)
SOCIAL_HANDLE_PATTERN = re.compile(r"@[a-zA-Z0-9_]+", re.IGNORECASE)
WATERMARK_WORDS = re.compile(r"\b(ahm7tech|careership|google|telegram)\b", re.IGNORECASE)
COPY_PREFIX_PATTERN = re.compile(r"^(?:Copy\s+of|Copy\s*\(\d+\)\s*of)\s+", re.IGNORECASE)


def extract_drive_folder_id(url: str) -> Optional[str]:
    patterns = [
        r"folders\/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", url.strip()):
        return url.strip()
    return None


def clean_root_folder_name(raw_name: str) -> str:
    """Cleans title and strips trailing 'Drive' or '- Google Drive'."""
    s = html.unescape(raw_name).strip()
    s = re.sub(r'["\']', '', s)
    s = re.sub(r'\s*-\s*Google\s*Drive$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s_-]+Drive$', '', s, flags=re.IGNORECASE)
    cleaned = sanitize_path(s)
    return cleaned.strip()


def fetch_folder_root_name(folder_id: str) -> str:
    try:
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r"<title>([^<]+?)(?:\s*-\s*Google Drive)?</title>", page_html, re.IGNORECASE)
        if match:
            raw_title = match.group(1).strip()
            if raw_title and raw_title.lower() != "google drive":
                return clean_root_folder_name(raw_title)
    except Exception:
        pass
    return ""


def format_size(bytes_val: int) -> str:
    if not bytes_val or bytes_val <= 0:
        return "—"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} TB"


async def parse_drive_folder(folder_url: str, custom_root: Optional[str] = "") -> List[Dict[str, Any]]:
    folder_id = extract_drive_folder_id(folder_url)
    if not folder_id:
        raise ValueError("Invalid Google Drive folder link or folder ID provided.")

    loop = asyncio.get_event_loop()

    def _scrape():
        detected_root = fetch_folder_root_name(folder_id)
        root_folder_name = clean_root_folder_name(custom_root) if custom_root else detected_root
        items = []

        view_url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#grid"
        req = urllib.request.Request(
            view_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_html = resp.read().decode("utf-8", errors="ignore")

            size_map = {}
            for sid, sz in re.findall(r'\["([a-zA-Z0-9_-]{25,})",.*?(\d{4,12})\]', resp_html):
                try:
                    size_map[sid] = int(sz)
                except ValueError:
                    pass

            matches = re.findall(
                r'data-id="([a-zA-Z0-9_-]+)".*?class="flip-entry-title"[^>]*>([^<]+)<',
                resp_html,
                re.DOTALL
            )
            for f_id, name in matches:
                clean_name = html.unescape(urllib.parse.unquote(name)).strip()
                if clean_name and f_id != folder_id:
                    path_str = f"{root_folder_name}/{clean_name}" if root_folder_name else clean_name
                    items.append({
                        "id": f_id,
                        "name": clean_name,
                        "path": path_str,
                        "relative_subpath": clean_name,
                        "size": size_map.get(f_id, 0)
                    })
        except Exception:
            pass

        if not items:
            raw = gdown.download_folder(
                f"https://drive.google.com/drive/folders/{folder_id}",
                skip_download=True,
                quiet=True,
                use_cookies=False
            )
            for r in (raw or []):
                if isinstance(r, dict) and r.get("id"):
                    rel_sub = html.unescape(r.get("path", r.get("name", "unnamed")))
                    path_str = f"{root_folder_name}/{rel_sub}" if root_folder_name else rel_sub
                    items.append({
                        "id": r["id"],
                        "name": html.unescape(r.get("name", "unnamed")),
                        "path": path_str,
                        "relative_subpath": rel_sub,
                        "size": r.get("size", 0)
                    })
                elif hasattr(r, "id"):
                    rel_sub = html.unescape(getattr(r, "path", getattr(r, "name", "unnamed")))
                    path_str = f"{root_folder_name}/{rel_sub}" if root_folder_name else rel_sub
                    items.append({
                        "id": getattr(r, "id"),
                        "name": html.unescape(getattr(r, "name", "unnamed")),
                        "path": path_str,
                        "relative_subpath": rel_sub,
                        "size": getattr(r, "size", 0)
                    })

        unique = {}
        for item in items:
            if item["id"] not in unique:
                unique[item["id"]] = item

        return list(unique.values())

    items = await loop.run_in_executor(None, _scrape)
    if not items:
        raise ValueError("No accessible files found in this Google Drive folder. Verify it is public.")
    return items


def sanitize_path(raw_path: str) -> str:
    raw_path = html.unescape(raw_path)
    parts = raw_path.replace("\\", "/").strip("/").split("/")
    cleaned_parts = []

    for part in parts:
        s = COPY_PREFIX_PATTERN.sub("", part)
        s = URL_DOMAIN_PATTERN.sub("", s)
        s = SOCIAL_HANDLE_PATTERN.sub("", s)
        s = re.sub(r"\|.*?\|", "", s)
        s = re.sub(r"\[.*?\]", "", s)
        s = re.sub(r"\(.*?\.(?:com|org|net|app|io|xyz)\)", "", s, flags=re.IGNORECASE)
        s = WATERMARK_WORDS.sub("", s)
        s = re.sub(r'[\\/*?:"<>|&;]', "", s)
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[_\s]+", " ", s)
        s = re.sub(r"^[-\s]+|[-\s]+$", "", s)

        if not s.strip():
            s = part.strip()
        cleaned_parts.append(s.strip())

    collapsed = []
    for part in cleaned_parts:
        if not collapsed or collapsed[-1].lower() != part.lower():
            collapsed.append(part)

    result = "/".join(collapsed)
    result = re.sub(r"(\.[a-zA-Z0-9]{2,5})\s*(\.[a-zA-Z0-9]{2,5})+$", r"\1", result)
    return result.strip()


def matches_wildcards(target: str, patterns: List[str]) -> bool:
    target_norm = target.encode("ascii", "ignore").decode("ascii").lower()
    for pattern in patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        if not p.startswith("*") and not p.endswith("*"):
            p = f"*{p}*"
        if fnmatch.fnmatch(target_norm, p) or fnmatch.fnmatch(target.lower(), p):
            return True
    return False


def is_file_already_on_disk(download_dir: Path, sanitized_rel_path: str) -> tuple[bool, int]:
    target_file = download_dir / sanitized_rel_path
    
    if target_file.exists() and target_file.is_file() and target_file.stat().st_size > 0:
        return True, target_file.stat().st_size

    parent_folder = target_file.parent
    if parent_folder.exists() and parent_folder.is_dir():
        target_norm = re.sub(r"[^a-zA-Z0-9]", "", target_file.stem.lower())
        for f in parent_folder.iterdir():
            if f.is_file() and f.stat().st_size > 0 and not f.name.endswith(".part"):
                f_norm = re.sub(r"[^a-zA-Z0-9]", "", f.stem.lower())
                if f_norm == target_norm:
                    return True, f.stat().st_size

    return False, 0


def apply_heuristics(
    raw_items: List[Dict[str, Any]],
    download_dir: Path,
    destination_root: Optional[str] = "",
    exclusions: Optional[str] = "",
) -> List[Dict[str, Any]]:
    custom_patterns = [p.strip() for p in (exclusions or "").split(",") if p.strip()]
    active_patterns = list(set(DEFAULT_EXCLUDE_PATTERNS + custom_patterns))

    dest_root = clean_root_folder_name(destination_root) if destination_root else ""
    plan = []

    for item in raw_items:
        original_name = item.get("name", "")
        file_id = item.get("id")
        raw_size = item.get("size", 0)

        # Recalculate full relative path based on the chosen destination folder
        rel_sub = item.get("relative_subpath")
        if not rel_sub:
            raw_p = item.get("path", original_name)
            if "/" in raw_p:
                rel_sub = raw_p.split("/", 1)[1]
            else:
                rel_sub = raw_p

        sanitized_subpath = sanitize_path(rel_sub)
        if dest_root:
            sanitized_rel_path = f"{dest_root}/{sanitized_subpath}"
        else:
            sanitized_rel_path = sanitized_subpath

        sanitized_name = Path(sanitized_rel_path).name

        exists_on_disk, disk_size = is_file_already_on_disk(download_dir, sanitized_rel_path)
        display_size_val = disk_size if exists_on_disk else raw_size

        plan_entry = {
            "id": file_id,
            "original_path": item.get("path", original_name),
            "sanitized_path": sanitized_rel_path,
            "relative_subpath": rel_sub,
            "name": sanitized_name,
            "size": display_size_val,
            "size_str": format_size(display_size_val),
            "action": "download",
            "category": "download",
            "reason": "Ready for download",
        }

        # 1. Skipped Bloat Files
        if (
            matches_wildcards(original_name, active_patterns)
            or matches_wildcards(sanitized_name, active_patterns)
            or matches_wildcards(sanitized_rel_path, active_patterns)
        ):
            plan_entry["action"] = "skip"
            plan_entry["category"] = "skip_bloat"
            plan_entry["reason"] = "Matched promotional / bloat pattern"
            plan.append(plan_entry)
            continue

        # 2. Check if file exists in the destination folder
        if exists_on_disk:
            plan_entry["action"] = "skip"
            plan_entry["category"] = "skip_exists"
            plan_entry["reason"] = "File already exists in destination folder"
            plan.append(plan_entry)
            continue

        plan.append(plan_entry)

    return plan


def download_with_gdown_cli(file_id: str, destination: Path, cancel_event: asyncio.Event) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_target = destination.with_suffix(destination.suffix + ".part")

    url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    cmd = [
        "gdown",
        url,
        "-O", str(temp_target),
        "--no-cookies",
        "--continue"
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        while proc.poll() is None:
            if cancel_event.is_set():
                proc.terminate()
                if temp_target.exists():
                    temp_target.unlink()
                return False, "Cancelled by user"
            import time
            time.sleep(0.5)

        stdout, stderr = proc.communicate()

        if cancel_event.is_set():
            if temp_target.exists():
                temp_target.unlink()
            return False, "Cancelled by user"

        if temp_target.exists():
            file_size = temp_target.stat().st_size
            if file_size > 0:
                with open(temp_target, "rb") as f:
                    header = f.read(256)
                if b"<html" in header or b"<!DOCTYPE" in header or b"<!doctype" in header:
                    temp_target.unlink()
                    return False, "Google Drive rate limit or quota page"

                if destination.exists():
                    destination.unlink()
                temp_target.rename(destination)

                return True, format_size(file_size)
            else:
                temp_target.unlink()
                return False, "Downloaded 0 bytes"

        return False, stderr.strip() or stdout.strip() or "Download failed"
    except Exception as e:
        if temp_target.exists():
            temp_target.unlink()
        return False, str(e)


async def download_folder_plan(
    item: Dict[str, Any],
    target_path: Path,
    cancel_event: asyncio.Event,
) -> tuple[bool, str]:
    if cancel_event.is_set():
        return False, "Cancelled"

    file_id = item.get("id")
    if not file_id:
        return False, "Missing file ID"

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, download_with_gdown_cli, file_id, target_path, cancel_event)


def repair_extensions(file_path: Path) -> Path:
    if not file_path.exists() or file_path.stat().st_size < 16:
        return file_path

    if file_path.suffix and re.match(r"^\.[a-zA-Z0-9]{2,5}$", file_path.suffix):
        return file_path

    try:
        with open(file_path, "rb") as f:
            header = f.read(64)
    except Exception:
        return file_path

    detected_ext = None
    for offset, magic, ext in MAGIC_BYTE_SIGNATURES:
        if len(header) >= offset + len(magic):
            if header[offset : offset + len(magic)] == magic:
                detected_ext = ext
                break

    if detected_ext:
        new_path = file_path.with_name(f"{file_path.name}{detected_ext}")
        try:
            file_path.rename(new_path)
            return new_path
        except Exception:
            return file_path

    return file_path
