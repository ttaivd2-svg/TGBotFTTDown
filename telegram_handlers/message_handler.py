import downloader.yt_dlp_downloader as ydd
import downloader.url_parser as up
import utils.health_server as hs
import utils.retry as r
import asyncio

from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from telegram import Update

async def process_video_url(message, url: str) -> None:
    await message.chat.send_action(ChatAction.TYPING)
    status = await message.reply_text("Завантажую відео на Google Drive...")
    retry_delays = r.get_retry_delays()

    for attempt in range(len(retry_delays) + 1):
        try:
            result = await asyncio.to_thread(ydd.download_video, url)
            break
        except ydd.DuplicateVideoError as exc:
            category_text = f"\nПапка: {exc.category}" if exc.category else ""
            await status.edit_text(
                "Це відео вже було завантажене.\n"
                f"Файл: {exc.video_number}.mp4\n"
                f"Автор: {exc.author}"
                f"{category_text}"
            )
            return
        except Exception as exc:
            if r.is_rate_limit_error(exc) and attempt < len(retry_delays):
                delay = retry_delays[attempt]
                hs.logger.warning(
                    "TikTok rate limit. Retrying in %s seconds. url=%s",
                    delay,
                    url,
                )
                await status.edit_text(
                    "TikTok тимчасово обмежив запити.\n"
                    f"Спробую ще раз через {r.format_delay(delay)}.\n"
                    f"Спроба {attempt + 1}/{len(retry_delays)}"
                )
                await asyncio.sleep(delay)
                await message.chat.send_action(ChatAction.TYPING)
                await status.edit_text("Пробую завантажити відео ще раз...")
                continue

            hs.logger.exception("Download failed")
            await status.edit_text(f"Не вдалося завантажити відео: {exc}")
            return

    author_text = result["author"]
    if result["author_url"]:
        author_text = f"{author_text}\n{result['author_url']}"

    await status.edit_text(
        "Готово. Відео завантажено на Google Drive:\n"
        f"{result['drive_file_url']}\n\n"
        f"Папка: {result['category']}\n"
        f"Google Drive file id: {result['drive_file_id']}\n"
        f"Лайки: {result['like_count']}\n"
        f"Автор: {author_text}"
    )


async def handle_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    hs.logger.info(
        "Received message. chat_id=%s thread_id=%s content_type=%s text=%s",
        message.chat_id,
        message.message_thread_id,
        "text" if message.text else "caption" if message.caption else "other",
        text,
    )

    url = up.extract_url(text)
    if not url:
        return

    await process_video_url(message, url)