import os
import re
import math
import fnmatch
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
import gdown

DATA_DIR = Path(os.environ.get("DATA_DIR", "/appdata"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
COOKIE_FILE = DATA_DIR / "cookies.txt"

DEFAULT_EXCLUSIONS = [
    "*trailer*", "*sample*", "*preview*", "*bonus*", "*promo*",
    "*reels*", "*short*", "*teaser*", "*advert*", "*watermark*",
    "*.nfo", "*.txt", "*.url", "*.lnk", "*.exe"
]

def format_size(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = min(len(units) - 1, int(math.floor(math.log(bytes_val, 1024))))
    p = math.pow(1024, i)
    s = round(bytes_val / p, 2)
    return f"{s} {units[i]}"

def get_cookie_file_path() -> Optional[str]:
    if COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 10:
        return str(COOKIE_FILE)
    return None

def sanitize_name(name: str) -> str:
    cleaned = re.sub(r'https?://\S+|www\.\S+|[a-zA-Z0-9-]+\.(?:com|net|org|app|io|in|tv|me|cc|top|biz|info|site|online|xyz|vercel\.app)\b', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'@\w+', '', cleaned)
    cleaned = re.sub(r'\bCopy(?:\s*\(\d+\))?\s*of\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', cleaned)
    cleaned = re.sub(r'[_\-\.]{2,}', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else name.strip()

def sanitize_path_tree(path_str: str) -> str:
    parts = Path(path_str).parts
    clean_parts = []
    for part in parts:
        clean = sanitize_name(part)
        clean_part = clean if clean else part
        if not clean_parts or clean_parts[-1].lower() != clean_part.lower():
            clean_parts.append(clean_part)
    return str(Path(*clean_parts))

def is_bloatware(path_str: str, custom_exclusions: Optional[List[str]] = None) -> bool:
    exclusions = custom_exclusions if custom_exclusions is not None else DEFAULT_EXCLUSIONS
    filename = Path(path_str).name.lower()
    path_lower = path_str.lower()
    for pattern in exclusions:
        p_lower = pattern.lower().strip()
        if not p_lower:
            continue
        if fnmatch.fnmatch(filename, p_lower) or fnmatch.fnmatch(path_lower, p_lower):
            return True
    return False

def check_file_on_disk(destination_folder: str, sanitized_subpath: str, size: int) -> bool:
    target_path = DOWNLOAD_DIR / destination_folder / sanitized_subpath
    if target_path.exists() and target_path.is_file():
        if size == 0:
            return target_path.stat().st_size > 0
        disk_size = target_path.stat().st_size
        if disk_size == size or abs(disk_size - size) < 1024:
            return True
    return False

def extract_remote_drive_title(url: str, cookie_path: Optional[str] = None) -> Optional[str]:
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        if cookie_path and os.path.exists(cookie_path):
            with open(cookie_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.startswith('#') or not line.strip():
                        continue
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        session.cookies.set(parts[5], parts[6], domain=parts[0], path=parts[2])

        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            match = re.search(r'<title>(.*?)</title>', resp.text, re.IGNORECASE | re.DOTALL)
            if match:
                raw_title = match.group(1).strip()
                # Strip ASCII and unicode hyphen/en-dash/em-dash followed by Google Drive / Google
                cleaned = re.sub(r'[\s\-_–—]+Google(?:\s+Drive)?$', '', raw_title, flags=re.IGNORECASE).strip()
                if cleaned and cleaned.lower() != "google drive":
                    return sanitize_name(cleaned)
    except Exception:
        pass
    return None

def inspect_google_drive(url: str, exclusions: Optional[List[str]] = None) -> Dict[str, Any]:
    cookie_path = get_cookie_file_path()
    remote_files = None
    used_cookie = bool(cookie_path)

    # 1. Attempt scan with cookies
    if cookie_path:
        try:
            remote_files = gdown.download_folder(
                url=url,
                skip_download=True,
                quiet=True,
                cookies_file=cookie_path
            )
        except Exception:
            remote_files = None

    # 2. Automatic anonymous fallback if cookies were expired/empty
    if not remote_files:
        try:
            remote_files = gdown.download_folder(
                url=url,
                skip_download=True,
                quiet=True,
                cookies_file=None
            )
            if remote_files:
                used_cookie = False
        except Exception as e:
            raise RuntimeError(f"Failed to scan Google Drive folder: {str(e)}")

    if not remote_files:
        raise ValueError("No files discovered in the provided Google Drive link.")

    web_title = extract_remote_drive_title(url, cookie_path if used_cookie else None)
    first_path = remote_files[0].path if hasattr(remote_files[0], 'path') else remote_files[0].get('path', '')
    raw_root_title = Path(first_path).parts[0] if first_path else "AetherDrive_Download"

    clean_destination_title = web_title if web_title else sanitize_name(re.sub(r'[\s\-_–—]+Google(?:\s+Drive)?$', '', raw_root_title, flags=re.IGNORECASE))

    items = []
    for f in remote_files:
        raw_p = f.path if hasattr(f, 'path') else f.get('path', '')
        f_id = f.id if hasattr(f, 'id') else f.get('id', '')
        size = getattr(f, 'size', 0) or 0

        clean_sub = sanitize_path_tree(raw_p)

        sub_parts = Path(clean_sub).parts
        if len(sub_parts) > 1 and sub_parts[0].lower() == clean_destination_title.lower():
            clean_sub = str(Path(*sub_parts[1:]))

        bloat = is_bloatware(raw_p, exclusions)
        on_disk = check_file_on_disk(clean_destination_title, clean_sub, size) if not bloat else False

        if bloat:
            category = "bloat"
        elif on_disk:
            category = "on_disk"
        else:
            category = "approved"

        items.append({
            "id": f_id,
            "original_path": raw_p,
            "sanitized_path": clean_sub,
            "size": size,
            "size_str": format_size(size),
            "category": category,
            "selected": (category == "approved")
        })

    dest_exists = (DOWNLOAD_DIR / clean_destination_title).exists()

    return {
        "destination_folder": clean_destination_title,
        "destination_exists_on_disk": dest_exists,
        "cookie_active": used_cookie,
        "items": items
    }

def repair_extension_magic_bytes(filepath: Path) -> Path:
    if not filepath.exists() or filepath.stat().st_size < 16:
        return filepath
    
    with open(filepath, "rb") as f:
        head = f.read(32)

    new_ext = None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        new_ext = ".png"
    elif head.startswith(b"\xff\xd8\xff"):
        new_ext = ".jpg"
    elif head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        new_ext = ".gif"
    elif head.startswith(b"%PDF"):
        new_ext = ".pdf"
    elif head.startswith(b"PK\x03\x04"):
        new_ext = ".zip"
    elif head.startswith(b"Rar!\x1a\x07"):
        new_ext = ".rar"
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        new_ext = ".7z"
    elif len(head) >= 12 and head[4:8] == b"ftyp":
        new_ext = ".mp4"
    elif head.startswith(b"\x1aE\xdf\xa3"):
        new_ext = ".mkv"
    elif head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"AVI ":
        new_ext = ".avi"

    if new_ext and filepath.suffix.lower() != new_ext:
        target = filepath.with_suffix(new_ext)
        if not target.exists():
            filepath.rename(target)
            return target
    return filepath
