from dotenv import load_dotenv
from pathlib import Path
import os
import re
import asyncio

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads")).expanduser().resolve()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REFRESH_TOKEN = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
RETRY_DELAYS_SECONDS = os.getenv("RETRY_DELAYS_SECONDS", "300,900,1800").strip()
PORT = int(os.getenv("PORT", "10000"))

AUTHORS_FILE_NAME = "authors.txt"
DOWNLOADED_FILE_NAME = "downloaded_videos.txt"
NEXT_NUMBER_FILE_NAME = "next_number.txt"

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
VIDEO_NUMBER_RE = re.compile(r"^(\d+)(?:\s+-|\.)")

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
VIDEO_FILE_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

DOWNLOAD_LOCK = asyncio.Lock()

ROOT_CATEGORY = {
    "key": "root",
    "label": "Under 1M likes / unknown",
    "folder_name": None,
    "min_likes": 0,
}
LIKE_CATEGORIES = [
    {
        "key": "10m",
        "label": "10M+ likes",
        "folder_name": "10M+ likes",
        "min_likes": 10_000_000,
    },
    {
        "key": "1m",
        "label": "1M+ likes",
        "folder_name": "1M+ likes",
        "min_likes": 1_000_000,
    },
]
ALL_CATEGORIES = [ROOT_CATEGORY, *LIKE_CATEGORIES]