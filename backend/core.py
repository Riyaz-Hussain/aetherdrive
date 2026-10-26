import os
import re
import time
import warnings
import gdown

warnings.filterwarnings("ignore")

# Unambiguous spam/promo keywords (safe to flag automatically)
STANDALONE_PROMO_KEYWORDS = [
    "bonus", "giveaway", "1m$", "500$", "bundle", "reels bundle",
    "vip access", "subscribe", "join for more", "join telegram",
    "free course", "free courses", "owner channel", "content owner",
    "all in one vip", "vip free", "binus"
]

PROMO_ACTIONS = ["join", "contact", "group", "channel", "community", "chat", "link", "daily"]
PLATFORMS_AND_TOPICS = ["whatsapp", "telegram", "discord"]

def token_to_regex(token):
    token = token.strip()
    if not token:
        return ""
    if "*" in token or "?" in token:
        escaped = re.escape(token)
        escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
        return f"^{escaped}$"
    return re.escape(token)

def build_regex(comma_separated_str):
    if not comma_separated_str:
        return None
    patterns = [token_to_regex(t) for t in comma_separated_str.split(",") if t.strip()]
    if not patterns:
        return None
    return re.compile("|".join(f"({p})" for p in patterns), re.IGNORECASE)

def is_promotional_string(text: str) -> tuple[bool, str]:
    lowered = text.lower()

    # 1. Standalone explicit promo tokens
    for kw in STANDALONE_PROMO_KEYWORDS:
        if kw in lowered:
            return True, f"*{kw}*"

    # 2. Contextual pairing: (action + platform) e.g., 'join whatsapp', 'whatsapp group'
    for platform in PLATFORMS_AND_TOPICS:
        if platform in lowered:
            for action in PROMO_ACTIONS:
                if re.search(rf"\b{action}\b.*?\b{platform}\b", lowered) or re.search(rf"\b{platform}\b.*?\b{action}\b", lowered):
                    return True, f"*{action}*{platform}*"

    # 3. Currency / number giveaways (e.g. 5000+ free, 500$, etc.)
    if re.search(r"(\d+\+?\s*free|free\s*\d+|\$|₹)", lowered) and any(w in lowered for w in ["course", "bundle", "reel", "channel", "vip", "binus"]):
        return True, "*free*courses*"

    # 4. Standalone 'channel' or 'telegram' in standalone files
    if re.search(r"\b(telegram|channel|owner)\b", lowered) and any(w in lowered for w in ["join", "link", "owner", "content"]):
        return True, "*channel*"

    return False, ""

def auto_detect_skip_patterns(items):
    detected_folder_tokens = set()
    detected_file_tokens = set()
    for item in items:
        path = item.path.replace("\\", "/")
        parts = [p.strip() for p in path.split("/") if p.strip()]
        for folder in parts[:-1]:
            is_promo, token = is_promotional_string(folder)
            if is_promo:
                detected_folder_tokens.add(token)
        if parts:
            filename = parts[-1]
            is_promo, token = is_promotional_string(filename)
            if is_promo:
                detected_file_tokens.add(token)
    return (
        ", ".join(sorted(detected_folder_tokens)),
        ", ".join(sorted(detected_file_tokens))
    )

def sanitize_name(name: str) -> str:
    cleaned = name
    cleaned = re.sub(r"(\||\[|\()?\s*(https?://\S+|vercel\.app|\.app\b|ahm7tech\S*|telegram|t\.me/\S+|bit\.ly/\S+|google>\S*)\s*(\||\]|\))?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\|.*$", "", cleaned)
    cleaned = re.sub(r"@[\w_]+", "", cleaned)
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)

    ext_match = re.search(r"(\.(mp4|mkv|avi|zip|rar|7z|pdf|png|jpg))\b", cleaned, flags=re.IGNORECASE)
    ext = ""
    if ext_match:
        ext = ext_match.group(1).lower()
        cleaned = re.sub(r"\.(mp4|mkv|avi|zip|rar|7z|pdf|png|jpg)\b", "", cleaned, flags=re.IGNORECASE)
    else:
        _, trailing_ext = os.path.splitext(cleaned)
        if trailing_ext.lower() in [".mp4", ".mkv", ".avi", ".zip", ".rar", ".7z", ".pdf", ".png", ".jpg"]:
            ext = trailing_ext.lower()
            cleaned = cleaned[:-len(trailing_ext)]

    cleaned = re.sub(r"\b(or)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s_.-]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"{cleaned}{ext}" if ext else (cleaned or name)

def sanitize_relative_path(rel_path: str) -> str:
    parts = [sanitize_name(p) for p in rel_path.replace("\\", "/").split("/") if p.strip()]
    collapsed = []
    for p in parts:
        if not collapsed or collapsed[-1].lower() != p.lower():
            collapsed.append(p)
    return "/".join(collapsed) if collapsed else rel_path

def detect_file_extension(filepath):
    try:
        with open(filepath, "rb") as f:
            header = f.read(64)
        if len(header) < 4:
            return None
        if header[4:8] == b"ftyp" or b"mp4" in header[:16]:
            return ".mp4"
        if header.startswith(b"\x1aE\xdf\xa3"):
            return ".mkv"
        if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
            return ".avi"
        if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06"):
            return ".zip"
        if header.startswith(b"Rar!\x1a\x07"):
            return ".rar"
        if header.startswith(b"7z\xbc\xaf\x27\x1c"):
            return ".7z"
        if header.startswith(b"%PDF"):
            return ".pdf"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
    except Exception:
        pass
    return None

def clean_existing_disk_files(target_dir):
    if not os.path.exists(target_dir):
        return []
    actions = []
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for fname in files:
            if fname.startswith("."):
                continue
            old_file_path = os.path.join(root, fname)
            clean_fname = sanitize_name(fname)

            has_ext = bool(re.search(r"\.[a-zA-Z0-9]{2,5}$", clean_fname))
            if not has_ext:
                ext = detect_file_extension(old_file_path)
                if ext:
                    clean_fname = f"{clean_fname}{ext}"

            if clean_fname != fname:
                new_file_path = os.path.join(root, clean_fname)
                if not os.path.exists(new_file_path):
                    try:
                        os.rename(old_file_path, new_file_path)
                        actions.append(f"Renamed file: {fname} -> {clean_fname}")
                    except Exception:
                        pass

        for dname in dirs:
            if dname.startswith("."):
                continue
            old_dir_path = os.path.join(root, dname)
            clean_dname = sanitize_name(dname)
            if clean_dname != dname:
                new_dir_path = os.path.join(root, clean_dname)
                if not os.path.exists(new_dir_path):
                    try:
                        os.rename(old_dir_path, new_dir_path)
                        actions.append(f"Renamed folder: {dname} -> {clean_dname}")
                    except Exception:
                        pass
    return actions

def simplify_string(s):
    s = os.path.splitext(s)[0].lower()
    return re.sub(r"[^a-z0-9]", "", s)

def file_or_variant_exists(base_path):
    if os.path.exists(base_path):
        return True, base_path
    parent_dir = os.path.dirname(base_path)
    base_name = os.path.basename(base_path)
    norm_target = simplify_string(base_name)
    
    if os.path.isdir(parent_dir):
        for fname in os.listdir(parent_dir):
            if fname.startswith("."):
                continue
            norm_disk = simplify_string(fname)
            if norm_disk and (norm_disk == norm_target or norm_target.startswith(norm_disk) or norm_disk.startswith(norm_target)):
                return True, os.path.join(parent_dir, fname)
    return False, None

def download_file_with_retry(file_id, output_path, max_attempts=3):
    retry_delays = [2, 5, 10]
    last_error = "Unknown error"
    for attempt in range(1, max_attempts + 1):
        try:
            res = gdown.download(id=file_id, output=output_path, resume=True, quiet=True)
            if res:
                return True, None
            last_error = "Empty response"
        except Exception as e:
            last_error = str(e).strip() or type(e).__name__
        if attempt < max_attempts:
            time.sleep(retry_delays[attempt - 1])
    return False, last_error
