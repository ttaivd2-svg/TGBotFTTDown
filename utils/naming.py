def get_video_file_stem(video_number: int, like_count: int | None) -> str:
    if like_count is None:
        return f"{video_number} - likes_unknown"
    return f"{video_number} - {like_count} likes"