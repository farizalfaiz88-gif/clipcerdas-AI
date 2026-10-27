
import re
import io
import zipfile
import subprocess
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="ClipCerdas AI",
    page_icon="🎬",
    layout="wide"
)


def get_youtube_id(url):
    patterns = [
        r"(?:youtube\.com/watch\?.*?v=)([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtube\.com/live/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url.strip())
        if match:
            return match.group(1)

    return None


def cut_video(video_path, start, duration, output_path):
    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "veryfast",
        "-movflags", "+faststart",
        str(output_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


st.title("🎬 ClipCerdas AI")
st.write(
    "Ubah video panjang menjadi klip pendek untuk "
    "TikTok, YouTube Shorts, dan Facebook Reels."
)

st.divider()

# PILIH SUMBER VIDEO
st.subheader("1. Pilih sumber video")

tab_youtube, tab_upload = st.tabs(
    ["🔗 Link YouTube", "📁 Upload Video"]
)

with tab_youtube:
    st.caption(
        "Tempel link video YouTube untuk melihat pratinjau."
    )

    youtube_url = st.text_input(
        "Link YouTube",
        placeholder="https://www.youtube.com/watch?v=...",
        key="youtube_url"
    )

    if youtube_url:
        video_id = get_youtube_id(youtube_url)

        if video_id:
            st.success("Format link berhasil dikenali.")

            components.iframe(
                f"https://www.youtube-nocookie.com/embed/{video_id}",
                height=315,
                scrolling=False
            )

            st.info(
                "Pratinjau tidak otomatis mengunduh video. "
                "Untuk memotongnya, gunakan file yang kamu miliki "
                "atau sudah mendapat izin untuk diolah."
            )
        else:
            st.error("Link YouTube tidak dikenali.")

with tab_upload:
    uploaded_file = st.file_uploader(
        "Pilih file video milikmu",
        type=["mp4", "mov", "mkv", "webm"]
    )

st.divider()

# PEMOTONG VIDEO
st.subheader("2. Atur klip video")

st.caption(
    "Fitur pemotongan ini memproses video yang diunggah, "
    "bukan mengambil file dari link YouTube."
)

clip_duration = st.number_input(
    "Durasi setiap klip (detik)",
    min_value=10,
    max_value=180,
    value=30,
    step=5
)

max_clips = st.number_input(
    "Jumlah klip maksimal",
    min_value=1,
    max_value=20,
    value=5,
    step=1
)

if uploaded_file is not None:
    st.video(uploaded_file)

    if st.button("✂️ Buat Klip Video", type="primary"):
        with st.spinner("Sedang memproses video..."):
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)

                    input_path = temp_path / uploaded_file.name
                    input_path.write_bytes(uploaded_file.getbuffer())

                    probe = subprocess.run(
                        [
                            "ffprobe",
                            "-v", "error",
                            "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1",
                            str(input_path)
                        ],
                        capture_output=True,
                        text=True
                    )

                    if probe.returncode != 0:
                        st.error(
                            "Video tidak dapat dibaca. "
                            "Coba unggah video MP4."
                        )
                        st.stop()

                    total_duration = float(probe.stdout.strip())

                    if total_duration <= 0:
                        st.error("Durasi video tidak valid.")
                        st.stop()

                    zip_buffer = io.BytesIO()
                    clips_created = 0

                    with zipfile.ZipFile(
                        zip_buffer,
                        "w",
                        zipfile.ZIP_DEFLATED
                    ) as zip_file:

                        start = 0
                        clip_number = 1

                        while (
                            start < total_duration
                            and clip_number <= max_clips
                        ):
                            duration = min(
                                clip_duration,
                                total_duration - start
                            )

                            output_path = (
                                temp_path /
                                f"klip_{clip_number:02d}.mp4"
                            )

                            cut_video(
                                input_path,
                                start,
                                duration,
                                output_path
                            )

                            zip_file.write(
                                output_path,
                                output_path.name
                            )

                            clips_created += 1
                            start += clip_duration
                            clip_number += 1

                    zip_buffer.seek(0)

                    st.success(
                        f"Berhasil membuat {clips_created} klip!"
                    )

                    st.download_button(
                        "⬇️ Download Semua Klip (ZIP)",
                        data=zip_buffer.getvalue(),
                        file_name="ClipCerdas_Klip.zip",
                        mime="application/zip"
                    )

            except FileNotFoundError:
                st.error(
                    "FFmpeg belum terpasang di server. "
                    "Pastikan file packages.txt berisi ffmpeg."
                )

            except Exception as error:
                st.error(f"Gagal memproses video: {error}")

else:
    st.info(
        "Tempel link YouTube untuk pratinjau, atau upload video "
        "untuk mulai membuat klip."
    )

st.divider()

st.caption(
    "ClipCerdas AI • MVP gratis • Gunakan video yang kamu miliki "
    "atau yang penggunaannya sudah mendapat izin."
)
