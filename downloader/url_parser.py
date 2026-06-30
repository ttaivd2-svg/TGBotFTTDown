import config.settings as s

def extract_url(text: str) -> str | None:
    match = s.URL_RE.search(text or "")
    return match.group(0) if match else None

def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")

def get_video_key(info: dict, fallback_url: str) -> str:
    extractor = info.get("extractor_key") or info.get("extractor") or "unknown"
    video_id = info.get("id") or info.get("webpage_url") or fallback_url
    return f"{extractor}:{video_id}"