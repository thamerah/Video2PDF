"""Command-line entry point for Video2PDF.

Thin wrapper around video2pdf.core.process_video — all the real logic lives
there so the CLI and the Streamlit app never diverge in behavior.
"""

from __future__ import annotations

import argparse
import sys

from tqdm import tqdm

from video2pdf.core import Video2PDFError, process_video


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video2pdf",
        description="Convert a lecture video into a PDF of its unique slides.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--url", help="YouTube video URL to download and process.")
    source_group.add_argument("--input", help="Path to a local video file (.mp4, .mkv, etc.).")

    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Sensitivity threshold for slide change detection (0-64). "
        "Lower = more sensitive. Default: 10.",
    )
    parser.add_argument(
        "--output",
        default="output.pdf",
        help="Output PDF filename. Default: output.pdf.",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Optional path to a cookies.txt file, used as a fallback if "
        "YouTube blocks the download.",
    )
    parser.add_argument(
        "--max-duration-hours",
        type=float,
        default=3.0,
        help="Reject videos longer than this many hours. Default: 3.0.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source = args.url or args.input

    bars: dict[str, tqdm] = {}

    def progress_callback(stage: str, message: str, percent: float) -> None:
        bar = bars.get(stage)
        if bar is None:
            bar = tqdm(total=100, unit="%", desc=message, leave=True)
            bars[stage] = bar
        bar.set_description(message)
        bar.n = min(max(percent, 0), 100)
        bar.refresh()

    try:
        result_path = process_video(
            source=source,
            output_path=args.output,
            threshold=args.threshold,
            max_duration_hours=args.max_duration_hours,
            cookies_path=args.cookies,
            progress_callback=progress_callback,
        )
    except Video2PDFError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        for bar in bars.values():
            bar.close()

    print(f"\nPDF saved to: {result_path}")


if __name__ == "__main__":
    main()
