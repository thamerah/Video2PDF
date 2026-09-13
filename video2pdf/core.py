"""Core backend for Video2PDF.

Turns a YouTube lecture link (or a local video file) into a PDF of the unique
presentation slides it contains. This module has no CLI or UI dependencies —
both video2pdf.cli and streamlit_app import process_video() and drive it with
their own progress display.

Processing pipeline:
    1. Ingestion   - download the video (yt-dlp) or use a local file directly.
    2. Sampling    - stream frames one at a time via cv2.VideoCapture, sampling
                     at a fixed interval and downscaling before hashing so
                     memory/CPU stay roughly constant regardless of video length.
    3. Detection   - perceptual-hash each sampled frame against the last SAVED
                     slide (not the previous sampled frame) to catch genuine
                     transitions while ignoring cursor movement / minor motion.
    4. Compilation - unique slides are written to a temp folder as they're
                     found, then assembled into a single PDF with img2pdf.
"""

from __future__ import annotations

import glob
import os
import tempfile
from typing import Callable, Optional
from urllib.parse import urlparse

import cv2
import img2pdf
import imagehash
from PIL import Image

# (stage, message, percent) -> None. Stages: "downloading", "analyzing", "compiling", "done".
ProgressCallback = Callable[[str, str, float], None]


class Video2PDFError(Exception):
    """Base error for any Video2PDF processing failure."""


class VideoTooLongError(Video2PDFError):
    """Raised when the source video exceeds the configured maximum duration."""


class DownloadError(Video2PDFError):
    """Raised when the video could not be downloaded via yt-dlp."""


def _report(callback: Optional[ProgressCallback], stage: str, message: str, percent: float) -> None:
    if callback is not None:
        callback(stage, message, percent)


def _is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def _download_video(
    url: str,
    dest_dir: str,
    max_duration_hours: float,
    cookies_path: Optional[str],
    progress_callback: Optional[ProgressCallback],
) -> str:
    """Download `url` into `dest_dir` at <=480p and return the local file path."""
    import yt_dlp  # imported lazily so the module loads even if yt-dlp isn't needed

    outtmpl = os.path.join(dest_dir, "source_video.%(ext)s")
    ydl_opts = {
        # Cap resolution: plenty for slide detection, far cheaper to decode than 1080p/4K.
        # Chained fallbacks: the ios/android clients (used below to dodge bot-detection)
        # expose a narrower format list than the default client, so a strict "must be
        # <=480p and have separate video+audio" selector can come up empty for some
        # videos. Fall back to any muxed <=480p stream, then to whatever's best overall,
        # rather than failing outright.
        "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "merge_output_format": "mp4",
        # Spoof a mobile client to dodge YouTube's bot-detection on datacenter/cloud
        # IPs (e.g. Streamlit Community Cloud) — a plain "web" client gets blocked there.
        "extractor_args": {"youtube": {"player_client": ["ios", "android"]}},
    }
    if cookies_path:
        # Fallback for when client-spoofing alone stops being enough.
        ydl_opts["cookiefile"] = cookies_path

    def _hook(d):
        if d.get("status") == "downloading" and progress_callback:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            pct = (downloaded / total * 100.0) if total else 0.0
            _report(progress_callback, "downloading", f"Downloading video... {pct:.0f}%", pct)

    ydl_opts["progress_hooks"] = [_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get("duration") or 0
            max_seconds = max_duration_hours * 3600
            if duration > max_seconds:
                raise VideoTooLongError(
                    f"Video duration ({duration / 3600:.2f}h) exceeds the maximum "
                    f"allowed duration ({max_duration_hours}h). Choose a shorter "
                    f"video or raise --max-duration-hours."
                )
            _report(progress_callback, "downloading", "Downloading video...", 0.0)
            ydl.download([url])
    except VideoTooLongError:
        raise
    except Exception as exc:
        raise DownloadError(f"Failed to download video: {exc}") from exc

    candidates = sorted(
        p for p in glob.glob(os.path.join(dest_dir, "source_video.*")) if not p.endswith(".part")
    )
    if not candidates:
        raise DownloadError("Download finished but no output file was found on disk.")
    return candidates[0]


def _frame_to_hash(frame, downscale_width: int) -> imagehash.ImageHash:
    """Downscale a BGR frame and compute its perceptual hash."""
    h, w = frame.shape[:2]
    if w > downscale_width:
        scale = downscale_width / w
        small = cv2.resize(frame, (downscale_width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    else:
        small = frame
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return imagehash.phash(Image.fromarray(rgb))


def _save_slide(frame, path: str, save_width: int) -> None:
    """Save a BGR frame to disk at up to `save_width` px wide, for PDF quality."""
    h, w = frame.shape[:2]
    if w > save_width:
        scale = save_width / w
        frame = cv2.resize(frame, (save_width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(path, "JPEG", quality=92)


def _extract_slides(
    video_path: str,
    slides_dir: str,
    threshold: int,
    max_duration_hours: float,
    sample_interval_sec: float,
    downscale_width: int,
    save_width: int,
    progress_callback: Optional[ProgressCallback],
) -> list[str]:
    """Stream through the video and save each unique slide as it's detected.

    Never holds more than one decoded frame in memory at a time: cap.grab()
    is used to cheaply skip frames between samples, and only sampled frames
    are actually decoded (retrieve) and processed.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Video2PDFError(f"Could not open video file: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if fps > 0 and total_frames > 0:
            duration_hours = (total_frames / fps) / 3600.0
            if duration_hours > max_duration_hours:
                raise VideoTooLongError(
                    f"Video duration ({duration_hours:.2f}h) exceeds the maximum "
                    f"allowed duration ({max_duration_hours}h)."
                )

        frame_interval = max(1, round(fps * sample_interval_sec))

        slide_paths: list[str] = []
        last_hash: Optional[imagehash.ImageHash] = None
        frame_idx = 0
        slide_count = 0

        while True:
            grabbed = cap.grab()
            if not grabbed:
                break

            if frame_idx % frame_interval == 0:
                ok, frame = cap.retrieve()
                if ok:
                    current_hash = _frame_to_hash(frame, downscale_width)
                    is_new_slide = last_hash is None or (current_hash - last_hash) >= threshold
                    if is_new_slide:
                        last_hash = current_hash
                        slide_count += 1
                        slide_path = os.path.join(slides_dir, f"slide_{slide_count:04d}.jpg")
                        _save_slide(frame, slide_path, save_width)
                        slide_paths.append(slide_path)

                if total_frames:
                    pct = min(frame_idx / total_frames * 100.0, 100.0)
                    _report(
                        progress_callback,
                        "analyzing",
                        f"Analyzing frames... {slide_count} unique slide(s) found",
                        pct,
                    )

            frame_idx += 1
    finally:
        cap.release()

    return slide_paths


def _compile_pdf(slide_paths: list[str], output_path: str) -> str:
    if not slide_paths:
        raise Video2PDFError(
            "No unique slides were detected. Try lowering the sensitivity threshold."
        )
    with open(output_path, "wb") as f:
        f.write(img2pdf.convert(slide_paths))
    return output_path


def process_video(
    source: str,
    output_path: str,
    threshold: int = 10,
    max_duration_hours: float = 3.0,
    sample_interval_sec: float = 1.0,
    downscale_width: int = 640,
    save_width: int = 1600,
    cookies_path: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """Convert a lecture video into a PDF of its unique slides.

    Args:
        source: A YouTube URL, or a path to a local video file.
        output_path: Where to write the finished PDF.
        threshold: Perceptual-hash distance (0-64) a sampled frame must exceed
            versus the last saved slide to count as a new slide. Lower = more
            sensitive (detects smaller changes, more false positives).
        max_duration_hours: Videos longer than this are rejected early.
        sample_interval_sec: How often to sample frames for comparison.
        downscale_width: Width (px) frames are shrunk to before hashing.
        save_width: Max width (px) saved slide images are capped at.
        cookies_path: Optional cookies.txt path, used if YouTube blocks the
            download despite client-spoofing.
        progress_callback: Optional callable(stage, message, percent) invoked
            throughout processing so a CLI or UI can show live status.

    Returns:
        The absolute path to the generated PDF (same as `output_path`).
    """
    output_path = os.path.abspath(output_path)

    with tempfile.TemporaryDirectory(prefix="video2pdf_") as temp_dir:
        slides_dir = os.path.join(temp_dir, "slides")
        os.makedirs(slides_dir, exist_ok=True)

        downloaded = False
        if _is_url(source):
            video_path = _download_video(
                source, temp_dir, max_duration_hours, cookies_path, progress_callback
            )
            downloaded = True
        else:
            video_path = os.path.abspath(source)
            if not os.path.isfile(video_path):
                raise Video2PDFError(f"Local video file not found: {video_path}")
            _report(progress_callback, "downloading", "Using local video file...", 100.0)

        try:
            _report(progress_callback, "analyzing", "Analyzing frames...", 0.0)
            slide_paths = _extract_slides(
                video_path,
                slides_dir,
                threshold,
                max_duration_hours,
                sample_interval_sec,
                downscale_width,
                save_width,
                progress_callback,
            )
        finally:
            # Free disk space as soon as we no longer need the source video,
            # rather than waiting for the whole TemporaryDirectory to clean up.
            if downloaded and os.path.exists(video_path):
                os.remove(video_path)

        _report(progress_callback, "compiling", "Compiling PDF...", 0.0)
        _compile_pdf(slide_paths, output_path)
        _report(progress_callback, "done", "Done!", 100.0)

    return output_path
