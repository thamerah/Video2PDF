# Video2PDF

Turn a YouTube lecture (or a local video file) into a clean PDF of its unique
presentation slides. It samples the video, detects genuine slide changes with
perceptual hashing, and compiles the unique frames into a single PDF.

Both the CLI and the Streamlit web app are thin wrappers around the shared
backend in [`video2pdf/core.py`](video2pdf/core.py) — no logic is duplicated
between them.

## Project structure

```
video2pdf/
  core.py      # shared backend: download, sample, detect, compile
  cli.py       # argparse CLI wrapper
streamlit_app.py  # Streamlit web UI wrapper
requirements.txt  # Python dependencies
packages.txt      # apt dependency (ffmpeg)
```

## Setup

```bash
pip install -r requirements.txt
```

`ffmpeg` must also be installed and on your `PATH` (yt-dlp uses it to merge
separate video/audio streams).

## CLI usage

Process a YouTube video:

```bash
python -m video2pdf.cli --url "https://www.youtube.com/watch?v=VIDEO_ID" --output lecture.pdf
```

Process a local video file:

```bash
python -m video2pdf.cli --input path/to/lecture.mp4 --output lecture.pdf
```

Options:

| Flag | Description | Default |
|---|---|---|
| `--url` | YouTube URL to download and process | — |
| `--input` | Local video file path (alternative to `--url`) | — |
| `--threshold` | Slide-change sensitivity (0-64, lower = more sensitive) | `10` |
| `--output` | Output PDF path | `output.pdf` |
| `--cookies` | Path to a `cookies.txt` file, used if YouTube blocks the download | — |
| `--max-duration-hours` | Reject videos longer than this | `3.0` |

`--url` and `--input` are mutually exclusive and one is required.

## Running the Streamlit app locally

```bash
streamlit run streamlit_app.py
```

This opens a browser tab where you can paste a YouTube URL, adjust the
"Slide Sensitivity Threshold" slider, click **Generate PDF**, and download the
result once processing finishes. An "Advanced" section lets you upload a
`cookies.txt` file if YouTube blocks the download.

## How slide detection works

1. Frames are sampled roughly every 0.5–1 second (not every raw frame) and
   downscaled to 640px wide before comparison, keeping memory and CPU cost low
   regardless of video length.
2. Each sampled frame is perceptually hashed (`imagehash.phash`) and compared
   against the hash of the **last saved slide** — not the previous sampled
   frame — so gradual motion or animation doesn't get counted as a run of
   separate transitions.
3. When the hash distance exceeds the sensitivity threshold, the frame is
   saved (capped at 1600px wide) as a new slide; everything else is discarded
   immediately.
4. Saved slides are compiled into a single PDF with `img2pdf`, which embeds
   the JPEGs without re-encoding them.

All intermediate files (downloaded video, extracted slide images) live in a
`tempfile.TemporaryDirectory()` and are guaranteed to be cleaned up even if
processing fails partway through. The downloaded video itself is deleted as
soon as frame extraction finishes, rather than at the very end.
