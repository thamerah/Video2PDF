"""Video2PDF: extract unique lecture slides from a video and compile them into a PDF."""

from video2pdf.core import (
    Video2PDFError,
    VideoTooLongError,
    DownloadError,
    process_video,
)

__all__ = [
    "Video2PDFError",
    "VideoTooLongError",
    "DownloadError",
    "process_video",
]
