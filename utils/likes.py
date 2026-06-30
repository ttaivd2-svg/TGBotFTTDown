import config.settings as s

def get_like_count(info: dict) -> int | None:
    like_count = info.get("like_count")
    return like_count if isinstance(like_count, int) and like_count >= 0 else None


def format_like_count(like_count: int | None) -> str:
    if like_count is None:
        return "невідомо"
    return str(like_count)


def get_like_category(like_count: int | None) -> dict:
    if like_count is None:
        return s.ROOT_CATEGORY

    for category in s.LIKE_CATEGORIES:
        if like_count >= category["min_likes"]:
            return category

    return s.ROOT_CATEGORY

def get_category_label(category: dict) -> str:
    return str(category["label"])