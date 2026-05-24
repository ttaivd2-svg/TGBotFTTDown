import asyncio
import io
import json
import logging
import mimetypes
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import credentials as oauth_credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from yt_dlp import YoutubeDL


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads")).expanduser().resolve()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REFRESH_TOKEN = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
RETRY_DELAYS_SECONDS = os.getenv("RETRY_DELAYS_SECONDS", "300,900,1800").strip()
AUTHORS_FILE_NAME = "authors.txt"
DOWNLOADED_FILE_NAME = "downloaded_videos.txt"
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def get_drive_service():
    if not GOOGLE_DRIVE_FOLDER_ID:
        return None

    if (
        GOOGLE_OAUTH_CLIENT_ID
        and GOOGLE_OAUTH_CLIENT_SECRET
        and GOOGLE_OAUTH_REFRESH_TOKEN
    ):
        credentials = oauth_credentials.Credentials(
            token=None,
            refresh_token=GOOGLE_OAUTH_REFRESH_TOKEN,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=GOOGLE_OAUTH_CLIENT_ID,
            client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
            scopes=DRIVE_SCOPES,
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            credentials_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be valid JSON.") from exc

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=DRIVE_SCOPES,
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    return None


DRIVE_SERVICE = get_drive_service()


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def get_retry_delays() -> list[int]:
    delays = []
    for item in RETRY_DELAYS_SECONDS.split(","):
        item = item.strip()
        if not item:
            continue

        try:
            delay = int(item)
        except ValueError:
            logger.warning("Invalid retry delay ignored: %s", item)
            continue

        if delay > 0:
            delays.append(delay)

    return delays


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


def format_delay(delay: int) -> str:
    minutes, seconds = divmod(delay, 60)
    if minutes and seconds:
        return f"{minutes} хв {seconds} сек"
    if minutes:
        return f"{minutes} хв"
    return f"{seconds} сек"


def get_next_video_number() -> int:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    numbers = []
    for file_path in DOWNLOAD_DIR.iterdir():
        if not file_path.is_file():
            continue

        match = re.match(r"^(\d+)\b", file_path.stem)
        if match:
            numbers.append(int(match.group(1)))

    authors_file = DOWNLOAD_DIR / AUTHORS_FILE_NAME
    if authors_file.exists():
        with authors_file.open("r", encoding="utf-8") as file:
            for line in file:
                match = re.match(r"^(\d+)\s+-\s+", line)
                if match:
                    numbers.append(int(match.group(1)))

    for video in get_downloaded_videos().values():
        if video["number"].isdigit():
            numbers.append(int(video["number"]))

    return max(numbers, default=0) + 1


def find_drive_file_id(name: str) -> str | None:
    if not DRIVE_SERVICE:
        return None

    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    response = (
        DRIVE_SERVICE.files()
        .list(
            q=(
                f"name = '{safe_name}' and "
                f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and "
                "trashed = false"
            ),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None


def download_drive_text_file(name: str) -> None:
    file_id = find_drive_file_id(name)
    if not DRIVE_SERVICE or not file_id:
        return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    request = DRIVE_SERVICE.files().get_media(fileId=file_id)
    with io.FileIO(DOWNLOAD_DIR / name, "wb") as file_handle:
        downloader = MediaIoBaseDownload(file_handle, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_to_drive(file_path: Path, name: str | None = None, replace: bool = False) -> str | None:
    if not DRIVE_SERVICE:
        return None

    upload_name = name or file_path.name
    mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    if replace:
        file_id = find_drive_file_id(upload_name)
        if file_id:
            result = (
                DRIVE_SERVICE.files()
                .update(fileId=file_id, media_body=media, fields="id")
                .execute()
            )
            return result["id"]

    metadata = {"name": upload_name, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    result = (
        DRIVE_SERVICE.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute()
    )
    return result["id"]


def sync_state_from_drive() -> None:
    download_drive_text_file(AUTHORS_FILE_NAME)
    download_drive_text_file(DOWNLOADED_FILE_NAME)


def sync_state_file_to_drive(name: str) -> None:
    file_path = DOWNLOAD_DIR / name
    if file_path.exists():
        upload_to_drive(file_path, name=name, replace=True)


def save_video_author(file_path: Path, author: str) -> None:
    number = file_path.stem
    authors_file = DOWNLOAD_DIR / AUTHORS_FILE_NAME

    with authors_file.open("a", encoding="utf-8") as file:
        file.write(f"{number} - {author}\n")

    sync_state_file_to_drive(AUTHORS_FILE_NAME)


class DuplicateVideoError(Exception):
    def __init__(self, video_number: str, author: str) -> None:
        self.video_number = video_number
        self.author = author
        super().__init__(f"Video already downloaded as {video_number}.mp4")


def get_video_key(info: dict, fallback_url: str) -> str:
    extractor = info.get("extractor_key") or info.get("extractor") or "unknown"
    video_id = info.get("id") or info.get("webpage_url") or fallback_url
    return f"{extractor}:{video_id}"


def get_downloaded_videos() -> dict[str, dict[str, str]]:
    downloaded_file = DOWNLOAD_DIR / DOWNLOADED_FILE_NAME
    if not downloaded_file.exists():
        return {}

    videos = {}
    with downloaded_file.open("r", encoding="utf-8") as file:
        for line in file:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue

            key, number, author, url = parts[:4]
            videos[key] = {"number": number, "author": author, "url": url}

    return videos


def save_downloaded_video(key: str, file_path: Path, author: str, url: str) -> None:
    downloaded_file = DOWNLOAD_DIR / DOWNLOADED_FILE_NAME
    number = file_path.stem

    with downloaded_file.open("a", encoding="utf-8") as file:
        file.write(f"{key}\t{number}\t{author}\t{normalize_url(url)}\n")

    sync_state_file_to_drive(DOWNLOADED_FILE_NAME)


def finish_downloaded_file(
    file_path: Path,
    video_key: str,
    author: str,
    author_url: str,
    url: str,
) -> dict[str, str | Path]:
    drive_file_id = upload_to_drive(file_path)
    save_video_author(file_path, author)
    save_downloaded_video(video_key, file_path, author, url)
    return {
        "file_path": file_path,
        "drive_file_id": drive_file_id or "",
        "author": author,
        "author_url": author_url,
    }


def find_duplicate_by_url(url: str) -> dict[str, str] | None:
    normalized_url = normalize_url(url)
    for video in get_downloaded_videos().values():
        if normalize_url(video["url"]) == normalized_url:
            return video

    return None


def download_video(url: str) -> dict[str, str | Path]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    duplicate = find_duplicate_by_url(url)
    if duplicate:
        raise DuplicateVideoError(duplicate["number"], duplicate["author"])

    info_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with YoutubeDL(info_options) as ydl:
        info = ydl.extract_info(url, download=False)

    video_key = get_video_key(info, url)
    downloaded_videos = get_downloaded_videos()
    if video_key in downloaded_videos:
        saved_video = downloaded_videos[video_key]
        raise DuplicateVideoError(saved_video["number"], saved_video["author"])

    video_number = get_next_video_number()
    options = {
        "outtmpl": str(DOWNLOAD_DIR / f"{video_number}.%(ext)s"),
        "format": (
            "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/"
            "bv*[vcodec^=h264][ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    before = set(DOWNLOAD_DIR.glob("*"))
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        prepared = Path(ydl.prepare_filename(info))

    author = (
        info.get("uploader")
        or info.get("channel")
        or info.get("creator")
        or info.get("uploader_id")
        or "невідомо"
    )
    author_url = info.get("uploader_url") or info.get("channel_url") or ""

    after = set(DOWNLOAD_DIR.glob("*"))
    new_files = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)

    if new_files:
        file_path = new_files[0]
        return finish_downloaded_file(
            file_path,
            video_key,
            author,
            author_url,
            info.get("webpage_url") or url,
        )

    if prepared.exists():
        return finish_downloaded_file(
            prepared,
            video_key,
            author,
            author_url,
            info.get("webpage_url") or url,
        )

    mp4_file = prepared.with_suffix(".mp4")
    if mp4_file.exists():
        return finish_downloaded_file(
            mp4_file,
            video_key,
            author,
            author_url,
            info.get("webpage_url") or url,
        )

    raise FileNotFoundError("Video was downloaded, but the output file was not found.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привіт. Надішли мені посилання на відео, і я завантажу його на Google Drive."
    )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    await message.reply_text(
        "chat_id: {chat_id}\nthread_id: {thread_id}".format(
            chat_id=message.chat_id,
            thread_id=message.message_thread_id,
        )
    )


async def process_video_url(message, url: str) -> None:
    await message.chat.send_action(ChatAction.TYPING)
    status = await message.reply_text("Завантажую відео...")
    retry_delays = get_retry_delays()

    for attempt in range(len(retry_delays) + 1):
        try:
            result = await asyncio.to_thread(download_video, url)
            break
        except DuplicateVideoError as exc:
            await status.edit_text(
                "Це відео вже було завантажене.\n"
                f"Файл: {exc.video_number}.mp4\n"
                f"Автор: {exc.author}"
            )
            return
        except Exception as exc:
            if is_rate_limit_error(exc) and attempt < len(retry_delays):
                delay = retry_delays[attempt]
                logger.warning(
                    "TikTok rate limit. Retrying in %s seconds. url=%s",
                    delay,
                    url,
                )
                await status.edit_text(
                    "TikTok тимчасово обмежив запити.\n"
                    f"Спробую ще раз через {format_delay(delay)}.\n"
                    f"Спроба {attempt + 1}/{len(retry_delays)}"
                )
                await asyncio.sleep(delay)
                await message.chat.send_action(ChatAction.TYPING)
                await status.edit_text("Пробую завантажити відео ще раз...")
                continue

            logger.exception("Download failed")
            await status.edit_text(f"Не вдалося завантажити відео: {exc}")
            return

    author_text = result["author"]
    if result["author_url"]:
        author_text = f"{author_text}\n{result['author_url']}"

    await status.edit_text(
        "Готово. Файл збережено:\n"
        f"{result['file_path']}\n\n"
        f"Google Drive file id: {result['drive_file_id'] or 'не налаштовано'}\n\n"
        f"Автор: {author_text}"
    )


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    url = extract_url(message.text or "")
    if not url:
        await message.reply_text("Напиши так: /download https://посилання-на-відео")
        return

    await process_video_url(message, url)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    logger.info(
        "Received message. chat_id=%s thread_id=%s content_type=%s text=%s",
        message.chat_id,
        message.message_thread_id,
        "text" if message.text else "caption" if message.caption else "other",
        text,
    )

    url = extract_url(text)
    if not url:
        return

    await process_video_url(message, url)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to the .env file.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    sync_state_from_drive()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", show_id))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(MessageHandler(~filters.COMMAND, handle_message))

    logger.info("Bot started. Download directory: %s", DOWNLOAD_DIR)
    logger.info(
        "Google Drive upload: %s",
        "enabled" if DRIVE_SERVICE and GOOGLE_DRIVE_FOLDER_ID else "disabled",
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
