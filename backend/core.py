import os
import re
import math
import fnmatch
import unicodedata
import ipaddress
import posixpath
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
import requests
import gdown

DATA_DIR = Path(os.environ.get("DATA_DIR", "/appdata"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
COOKIE_FILE = DATA_DIR / "cookies.txt"

DEFAULT_EXCLUSIONS = [
    "*trailer*", "*sample*", "*preview*", "*bonus*", "*promo*",
    "*reels*", "*short*", "*teaser*",
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

def sanitize_destination_name(name: str) -> str:
    cleaned = sanitize_name(name)
    cleaned = ''.join(
        char for char in cleaned
        if unicodedata.category(char) not in {'So', 'Sk', 'Me', 'Cf'}
        and char not in {'\ufe0e', '\ufe0f', '\u200d', '\u20e3'}
    )
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else "AetherDrive_Download"

def sanitize_path_tree(path_str: str) -> str:
    parts = Path(path_str).parts
    clean_parts = []
    for part in parts:
        clean = sanitize_name(part)
        clean_part = clean if clean else part
        if not clean_parts or clean_parts[-1].lower() != clean_part.lower():
            clean_parts.append(clean_part)
    return str(Path(*clean_parts))

def matching_exclusions(path_str: str, custom_exclusions: Optional[List[str]] = None) -> List[Dict[str, str]]:
    exclusions = custom_exclusions if custom_exclusions is not None else DEFAULT_EXCLUSIONS
    filename = Path(path_str).name.lower()
    path_lower = path_str.lower()
    matches = []
    for pattern in exclusions:
        p_lower = pattern.lower().strip()
        if not p_lower:
            continue
        filename_matches = fnmatch.fnmatch(filename, p_lower)
        path_matches = fnmatch.fnmatch(path_lower, p_lower)
        if filename_matches or path_matches:
            matches.append({
                "pattern": pattern.strip(),
                "matched_on": "filename and path" if filename_matches and path_matches else ("filename" if filename_matches else "path")
            })
    return matches

def is_bloatware(path_str: str, custom_exclusions: Optional[List[str]] = None) -> bool:
    return bool(matching_exclusions(path_str, custom_exclusions))

def check_file_on_disk(destination_folder: str, sanitized_subpath: str, size: int) -> bool:
    target_path = DOWNLOAD_DIR / destination_folder / sanitized_subpath
    if target_path.exists() and target_path.is_file():
        disk_size = target_path.stat().st_size
        if disk_size == 0:
            return False
        if size == 0:
            return True
        if disk_size == size or abs(disk_size - size) < 1024:
            return True
    return False

def _validated_http_url(
    url: str,
    base_origin: Optional[tuple[str, str, int]] = None,
    base_path: Optional[str] = None,
    *,
    resolve_host: bool = True
) -> tuple[str, str, int, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP or HTTPS directory links are supported.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as err:
        raise ValueError("The source URL has an invalid port.") from err
    host = parsed.hostname.lower()
    origin = (parsed.scheme, host, port)
    if base_origin and origin != base_origin:
        raise ValueError("Directory links and redirects must remain on the same origin.")

    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or any(part == ".." for part in decoded_path.split("/")):
        raise ValueError("The source URL contains an invalid path.")
    normalized_path = posixpath.normpath("/" + decoded_path.lstrip("/"))
    if decoded_path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"
    if base_path and normalized_path.rstrip("/") != base_path.rstrip("/") and not normalized_path.startswith(base_path.rstrip("/") + "/"):
        raise ValueError("Directory links must remain inside the submitted directory.")

    if resolve_host:
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        except OSError as err:
            raise ValueError(f"Could not resolve source host '{host}'.") from err
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("The source host must resolve only to public IP addresses.")
    return origin[0], host, port, normalized_path

def _origin_for_url(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS directory URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), port

def _safe_get(
    session: requests.Session,
    url: str,
    base_origin: tuple[str, str, int],
    base_path: str,
    *,
    stream: bool = False
) -> requests.Response:
    current_url = url
    for _ in range(6):
        _validated_http_url(current_url, base_origin, base_path)
        response = session.get(current_url, timeout=20, allow_redirects=False, stream=stream)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("The source site returned a redirect without a destination.")
            current_url = urljoin(current_url, location)
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return response
    raise ValueError("The source site returned too many redirects.")

class _DirectoryLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

def inspect_http_directory(url: str, exclusions: Optional[List[str]] = None) -> Dict[str, Any]:
    parsed = urlsplit(url.strip())
    normalized_url = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path if parsed.path.endswith("/") else parsed.path + "/",
        parsed.query,
        parsed.fragment
    ))
    parsed = urlsplit(normalized_url)
    root_folder_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    base_origin = _origin_for_url(normalized_url)
    _, _, _, base_path = _validated_http_url(normalized_url, base_origin)
    if not base_path.endswith("/"):
        base_path += "/"

    session = requests.Session()
    session.headers["User-Agent"] = "AetherDrive/1.0 (authorized directory downloads)"
    pending = [(normalized_url, "", 0)]
    visited: set[str] = set()
    remote_files: List[tuple[str, str]] = []
    seen_file_paths: set[str] = set()

    while pending:
        directory_url, relative_directory, depth = pending.pop()
        if directory_url in visited:
            continue
        visited.add(directory_url)
        if depth > 20:
            raise ValueError("The directory listing is nested too deeply (maximum depth is 20).")

        with _safe_get(session, directory_url, base_origin, base_path, stream=True) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                if not relative_directory:
                    raise ValueError("This URL is not an HTML directory listing.")
                remote_files.append((response.url, relative_directory.rstrip("/")))
                continue
            page_url = response.url
            page_bytes = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                page_bytes.extend(chunk)
                if len(page_bytes) > 4 * 1024 * 1024:
                    raise ValueError("A directory listing page exceeds the 4 MiB scan limit.")
            html = bytes(page_bytes).decode(response.encoding or "utf-8", errors="replace")

        parser = _DirectoryLinks()
        parser.feed(html)

        for href in parser.links:
            candidate = urljoin(page_url, href)
            try:
                _, _, _, candidate_path = _validated_http_url(
                    candidate, base_origin, base_path, resolve_host=False
                )
            except ValueError:
                continue
            current_path = unquote(urlsplit(page_url).path)
            if candidate_path.rstrip("/") == current_path.rstrip("/"):
                continue
            relative_path = candidate_path[len(base_path):]
            if not relative_path or relative_path.startswith("/"):
                continue
            relative_path = unquote(relative_path)
            segments = relative_path.strip("/").split("/")
            if any(
                part in {"", ".", ".."} or any(ord(char) < 32 or ord(char) == 127 for char in part)
                for part in segments
            ):
                continue
            if depth == 0 and len(segments) == 1 and segments[0].casefold() == root_folder_name.casefold():
                continue
            if candidate_path.endswith("/"):
                pending.append((candidate, relative_path, depth + 1))
            else:
                relative_file = relative_path
                if relative_file not in seen_file_paths:
                    seen_file_paths.add(relative_file)
                    remote_files.append((candidate, relative_file))
                if len(remote_files) > 2000:
                    raise ValueError("The directory contains more than 2,000 files; narrow the submitted directory.")
        if len(visited) + len(pending) > 500:
            raise ValueError("The directory tree contains too many folders (maximum 500).")

    if not remote_files:
        raise ValueError("No downloadable files were found. The URL must expose a public HTML directory listing.")

    path_parts = [part for part in unquote(parsed.path).split("/") if part]
    root_name = path_parts[-1] if path_parts else (parsed.hostname or "Website")
    root_name = root_name.replace("\\", "/").split("/")[-1].strip()
    if root_name in {"", ".", ".."}:
        root_name = path_parts[-1] if path_parts else (parsed.hostname or "Website")
    destination = sanitize_destination_name(root_name)

    items = []
    for file_url, raw_path in remote_files:
        clean_path = sanitize_path_tree(raw_path)
        bloat_reasons = matching_exclusions(raw_path, exclusions)
        bloat = bool(bloat_reasons)
        existing_path = DOWNLOAD_DIR / destination / clean_path
        exists = existing_path.is_file() and existing_path.stat().st_size > 0
        category = "bloat" if bloat else ("on_disk" if exists else "approved")
        items.append({
            "id": file_url,
            "download_url": file_url,
            "source_type": "http",
            "original_path": raw_path,
            "sanitized_path": clean_path,
            "size": 0,
            "size_str": "Unknown",
            "size_known": False,
            "category": category,
            "bloat_reasons": bloat_reasons,
            "selected": category == "approved"
        })

    return {
        "destination_folder": destination,
        "source_folder_name": destination,
        "destination_exists_on_disk": (DOWNLOAD_DIR / destination).exists(),
        "cookie_active": False,
        "source_type": "http",
        "items": items
    }

def inspect_source(url: str, exclusions: Optional[List[str]] = None) -> Dict[str, Any]:
    hostname = (urlsplit(url.strip()).hostname or "").lower()
    if hostname in {"drive.google.com", "docs.google.com"}:
        plan = inspect_google_drive(url, exclusions)
        plan["source_type"] = "google_drive"
        return plan
    if hostname in {"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"}:
        from backend.mega import inspect_mega_link
        return inspect_mega_link(url, exclusions)
    return inspect_http_directory(url, exclusions)

def download_http_file(url: str, output: Path, source_url: str, on_chunk) -> str:
    base_origin = _origin_for_url(source_url)
    _, _, _, base_path = _validated_http_url(source_url, base_origin)
    if not base_path.endswith("/"):
        base_path += "/"
    session = requests.Session()
    session.headers["User-Agent"] = "AetherDrive/1.0 (authorized directory downloads)"
    temporary_path = output.with_name(output.name + ".part")
    bytes_written = 0
    try:
        with _safe_get(session, url, base_origin, base_path, stream=True) as response:
            response_status = response.status_code
            response_url = response.url
            response_type = response.headers.get("Content-Type", "unknown")
            content_length = response.headers.get("Content-Length")
            try:
                expected_bytes = int(content_length) if content_length is not None else None
            except ValueError:
                expected_bytes = None
            with temporary_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        on_chunk(len(chunk))
                        file.write(chunk)
                        bytes_written += len(chunk)
            content_encoding = response.headers.get("Content-Encoding", "").lower()
            if bytes_written == 0:
                raise ValueError(
                    "The source returned no file data "
                    f"(HTTP {response_status}, Content-Type: {response_type}, "
                    f"Content-Length: {content_length or 'unknown'}, URL: {response_url})."
                )
            if (
                expected_bytes is not None
                and expected_bytes > 0
                and content_encoding in {"", "identity"}
                and bytes_written != expected_bytes
            ):
                raise ValueError(
                    "The source returned an incomplete file "
                    f"({bytes_written} of {expected_bytes} bytes; HTTP {response_status}, "
                    f"Content-Type: {response_type}, URL: {response_url})."
                )
        temporary_path.replace(output)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return str(output)

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

    clean_destination_title = sanitize_destination_name(
        web_title if web_title else re.sub(
            r'[\s\-_–—]+Google(?:\s+Drive)?$', '', raw_root_title, flags=re.IGNORECASE
        )
    )

    items = []
    for f in remote_files:
        raw_p = f.path if hasattr(f, 'path') else f.get('path', '')
        f_id = f.id if hasattr(f, 'id') else f.get('id', '')
        size = getattr(f, 'size', 0) or 0

        clean_sub = sanitize_path_tree(raw_p)

        sub_parts = Path(clean_sub).parts
        if len(sub_parts) > 1 and sanitize_destination_name(sub_parts[0]).lower() == clean_destination_title.lower():
            clean_sub = str(Path(*sub_parts[1:]))

        bloat_reasons = matching_exclusions(raw_p, exclusions)
        bloat = bool(bloat_reasons)
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
            "bloat_reasons": bloat_reasons,
            "selected": (category == "approved")
        })

    dest_exists = (DOWNLOAD_DIR / clean_destination_title).exists()

    return {
        "destination_folder": clean_destination_title,
        "source_folder_name": clean_destination_title,
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
