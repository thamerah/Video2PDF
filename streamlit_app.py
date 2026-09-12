"""Streamlit web UI for Video2PDF.

Thin wrapper around video2pdf.core.process_video — all the real logic lives
there so this UI and the CLI never diverge in behavior.
"""

import os
import tempfile

import streamlit as st

from video2pdf.core import Video2PDFError, process_video

st.set_page_config(page_title="Video2PDF", page_icon="📄", layout="centered")

st.title("📄 Video2PDF")
st.markdown("##### Turn a lecture video into a clean PDF of its unique slides")
st.write(
    "Paste a YouTube lecture link below. Video2PDF scans the video for slide "
    "changes and compiles the unique slides into a single downloadable PDF."
)

# Persist the generated PDF across reruns (e.g. moving the slider) so it only
# has to be regenerated when the button is actually clicked again.
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pdf_name" not in st.session_state:
    st.session_state.pdf_name = None

url = st.text_input("YouTube video URL", placeholder="https://www.youtube.com/watch?v=...")

threshold = st.slider(
    "Slide Sensitivity Threshold",
    min_value=0,
    max_value=30,
    value=10,
    help=(
        "Lower values detect smaller visual changes, producing more slides "
        "(and possibly some false positives from animations or cursor "
        "movement). Higher values only register major slide transitions."
    ),
)

with st.expander("Advanced: having trouble downloading? (optional)"):
    cookies_file = st.file_uploader(
        "cookies.txt",
        type=["txt"],
        help=(
            "If YouTube blocks the download, export your browser's cookies "
            "for youtube.com (e.g. with the 'Get cookies.txt' extension) and "
            "upload the file here. It's only used for this one request and "
            "is discarded immediately after."
        ),
    )

generate_clicked = st.button("Generate PDF", type="primary", use_container_width=True)

status_placeholder = st.empty()
progress_placeholder = st.empty()

if generate_clicked:
    if not url.strip():
        st.error("Please enter a YouTube URL.")
    else:
        st.session_state.pdf_bytes = None
        st.session_state.pdf_name = None

        progress_bar = progress_placeholder.progress(0)

        def progress_callback(stage: str, message: str, percent: float) -> None:
            status_placeholder.info(message)
            progress_bar.progress(int(min(max(percent, 0), 100)))

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "slides.pdf")

            cookies_path = None
            if cookies_file is not None:
                cookies_path = os.path.join(tmp_dir, "cookies.txt")
                with open(cookies_path, "wb") as f:
                    f.write(cookies_file.getvalue())

            try:
                with st.spinner("Processing video..."):
                    process_video(
                        source=url.strip(),
                        output_path=output_path,
                        threshold=threshold,
                        cookies_path=cookies_path,
                        progress_callback=progress_callback,
                    )
                with open(output_path, "rb") as f:
                    st.session_state.pdf_bytes = f.read()
                st.session_state.pdf_name = "slides.pdf"
                status_placeholder.success("PDF generated successfully!")
                progress_placeholder.progress(100)
            except Video2PDFError as exc:
                status_placeholder.error(f"Failed to process video: {exc}")
                progress_placeholder.empty()
            except Exception as exc:  # noqa: BLE001 - surface unexpected errors to the user
                status_placeholder.error(f"Unexpected error: {exc}")
                progress_placeholder.empty()

if st.session_state.pdf_bytes:
    st.download_button(
        label="⬇️ Download PDF",
        data=st.session_state.pdf_bytes,
        file_name=st.session_state.pdf_name,
        mime="application/pdf",
        use_container_width=True,
    )
