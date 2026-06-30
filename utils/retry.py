import utils.health_server as hs
import config.settings as s


def get_retry_delays() -> list[int]:
    delays = []
    for item in s.RETRY_DELAYS_SECONDS.split(","):
        item = item.strip()
        if not item:
            continue

        try:
            delay = int(item)
        except ValueError:
            hs.logger.warning("Invalid retry delay ignored: %s", item)
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