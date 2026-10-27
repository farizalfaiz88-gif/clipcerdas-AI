
import io
import re
import zipfile
import tempfile
import subprocess
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from faster_whisper import WhisperModel


st.set_page_config(
    page_title="ClipCerdas AI",
    page_icon="🎬",
    layout="wide"
)


# =========================
# FUNGSI LINK YOUTUBE
# =========================

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


# =========================
# FUNGSI FFmpeg
# =========================

def cut_video(video_path, start, duration, output_path, vertical):
    command = [
        "ffmpeg", "-y",
        "-ss", str(max(0, start)),
        "-i", str(video_path),
        "-t", str(duration),
    ]

    if vertical:
        command += [
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        ]

    command += [
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
        raise RuntimeError(result.stderr[-2000:])


def get_video_duration(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError("Durasi video tidak dapat dibaca.")

    return float(result.stdout.strip())


# =========================
# ANALISIS UCAPAN DENGAN AI
# =========================

KEYWORDS = {
    # Kata yang sering menandai momen menarik
    "ternyata": 3,
    "rahasia": 3,
    "mengejutkan": 4,
    "bayangkan": 2,
    "kenapa": 2,
    "mengapa": 2,
    "bagaimana": 2,
    "kesalahan": 3,
    "bahaya": 3,
    "terburuk": 3,
    "terbaik": 2,
    "akhirnya": 2,
    "berhasil": 2,
    "gagal": 2,
    "fakta": 2,
    "jangan": 2,
    "ternyata": 3,
    "serius": 2,
    "mustahil": 3,
    "luar biasa": 3,
    "tidak percaya": 3,
    "tidak menyangka": 3,
    "masalahnya": 2,
    "solusinya": 2,
    "uang": 1,
    "gratis": 2,
    "penting": 1,
    "wow": 2,
}

FILLER_WORDS = {
    "eee", "em", "anu", "hmm", "eh"
}


def score_moments(segments, total_duration, clip_duration, number_clips):
    """
    Menilai kandidat klip berdasarkan:
    - kepadatan ucapan
    - kata pemancing perhatian
    - pertanyaan dan tanda seru
    - jumlah kata unik
    - durasi klip yang tersedia

    Ini adalah penilaian heuristik, bukan prediksi viral.
    """

    transcript = []

    for seg in segments:
        text = seg.text.strip()
        if text:
            transcript.append({
                "start": float(seg.start),
                "end": float(seg.end),
                "text": text
            })

    if not transcript:
        return []

    clip_length = min(float(clip_duration), total_duration)

    # Coba beberapa posisi awal berdasarkan waktu segmen ucapan
    candidate_starts = {0.0}

    for seg in transcript:
        candidate_starts.add(
            max(0.0, min(seg["start"] - 1.5,
                         total_duration - clip_length))
        )

    candidates = []

    for start in candidate_starts:
        end = min(start + clip_length, total_duration)

        window = [
            seg for seg in transcript
            if seg["end"] > start and seg["start"] < end
        ]

        if not window:
            continue

        text = " ".join(seg["text"] for seg in window)
        normalized = text.lower()
        words = re.findall(r"\b[\w'-]+\b", normalized)

        if not words:
            continue

        # Perkiraan waktu yang benar-benar berisi ucapan
        speech_seconds = sum(
            max(
                0,
                min(seg["end"], end) - max(seg["start"], start)
            )
            for seg in window
        )

        speech_density = min(
            speech_seconds / max(clip_length, 1),
            1.0
        )

        keyword_score = sum(
            weight
            for phrase, weight in KEYWORDS.items()
            if phrase in normalized
        )

        question_score = normalized.count("?") * 1.5
        excitement_score = normalized.count("!") * 1.0

        unique_ratio = len(set(words)) / max(len(words), 1)
        useful_words = sum(
            1 for word in words if word not in FILLER_WORDS
        )

        # Batasi pengaruh panjang teks agar tidak hanya memilih
        # bagian yang paling banyak bicara.
        speech_score = min(useful_words / 35, 1.5)

        total_score = (
            speech_density * 4
            + min(keyword_score, 8)
            + question_score
            + excitement_score
            + unique_ratio
            + speech_score
        )

        candidates.append({
            "start": start,
            "end": end,
            "score": total_score,
            "text": text
        })

    # Pilih skor tertinggi dan hindari klip yang tumpang tindih.
    candidates.sort(key=lambda item: item["score"], reverse=True)

    selected = []

    for candidate in candidates:
        overlaps = any(
            candidate["start"] < chosen["end"]
            and candidate["end"] > chosen["start"]
            for chosen in selected
        )

        if not overlaps:
            selected.append(candidate)

        if len(selected) >= int(number_clips):
            break

    selected.sort(key=lambda item: item["start"])

    return selected


# =========================
# TAMPILAN APLIKASI
# =========================

st.title("🎬 ClipCerdas AI")
st.write(
    "Temukan momen potensial dari video panjang "
    "dan buat klip pendek secara otomatis."
)

st.caption(
    "AI membantu memilih momen berdasarkan transkrip. "
    "Hasilnya tetap perlu diperiksa sebelum dipublikasikan."
)

st.divider()

st.subheader("1. Sumber video")

tab_youtube, tab_upload = st.tabs(
    ["🔗 Link YouTube", "📁 Upload Video"]
)

with tab_youtube:
    youtube_url = st.text_input(
        "Tempel link YouTube",
        placeholder="https://www.youtube.com/watch?v=...",
        key="youtube_url"
    )

    video_id = get_youtube_id(youtube_url) if youtube_url else None

    if video_id:
        components.iframe(
            f"https://www.youtube-nocookie.com/embed/{video_id}",
            height=315,
            scrolling=False
        )

        st.info(
            "Pratinjau saja. Link ini belum menyediakan file video "
            "untuk dianalisis. Unggah file yang kamu miliki atau "
            "yang penggunaannya sudah mendapat izin."
        )

    if st.button("🔍 Periksa Link YouTube"):
        if video_id:
            st.success("Format link dan ID video dikenali.")
        else:
            st.error("Masukkan link YouTube yang valid.")

with tab_upload:
    uploaded_file = st.file_uploader(
        "Unggah video yang boleh kamu olah",
        type=["mp4", "mov", "mkv", "webm"]
    )

st.divider()

st.subheader("2. Atur Auto Clip")

col1, col2 = st.columns(2)

with col1:
    clip_duration = st.selectbox(
        "Durasi setiap klip",
        options=[15, 30, 45, 60, 90],
        index=1,
        format_func=lambda x: f"{x} detik"
    )

with col2:
    number_clips = st.number_input(
        "Jumlah klip yang diminta",
        min_value=1,
        max_value=10,
        value=3,
        step=1
    )

language = st.selectbox(
    "Bahasa percakapan",
    options=["id", "en", "auto"],
    format_func=lambda x: {
        "id": "Bahasa Indonesia",
        "en": "Bahasa Inggris",
        "auto": "Deteksi otomatis"
    }[x]
)

vertical = st.checkbox(
    "Format vertikal 9:16 untuk Shorts/Reels",
    value=True
)

st.caption(
    "Model AI awal menggunakan Whisper tiny untuk mengurangi "
    "beban server gratis. Pemrosesan bisa memerlukan waktu."
)

if uploaded_file is not None:
    st.video(uploaded_file)

    if st.button(
        "✨ Cari Momen Terbaik & Buat Klip",
        type="primary",
        use_container_width=True
    ):
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                input_path = temp_path / uploaded_file.name
                input_path.write_bytes(uploaded_file.getbuffer())

                with st.spinner("Membaca durasi video..."):
                    total_duration = get_video_duration(input_path)

                if total_duration <= 0:
                    st.error("Durasi video tidak valid.")
                    st.stop()

                with st.spinner(
                    "AI sedang mendengarkan dan menganalisis ucapan..."
                ):
                    if "whisper_model" not in st.session_state:
                        st.session_state["whisper_model"] = (
                            WhisperModel(
                                "tiny",
                                device="cpu",
                                compute_type="int8"
                            )
                        )

                    model = st.session_state["whisper_model"]

                    transcribe_language = (
                        None if language == "auto" else language
                    )

                    segments_generator, info = model.transcribe(
                        str(input_path),
                        language=transcribe_language,
                        beam_size=1,
                        vad_filter=True
                    )

                    segments = list(segments_generator)

                if not segments:
                    st.error(
                        "AI tidak menemukan ucapan yang bisa dianalisis. "
                        "Pastikan video memiliki audio yang jelas."
                    )
                    st.stop()

                with st.spinner("Memilih momen terbaik..."):
                    selected = score_moments(
                        segments,
                        total_duration,
                        clip_duration,
                        number_clips
                    )

                if not selected:
                    st.error(
                        "Tidak ditemukan momen yang cukup jelas. "
                        "Coba video lain atau durasi klip berbeda."
                    )
                    st.stop()

                st.subheader("3. Hasil analisis AI")

                for i, clip in enumerate(selected, start=1):
                    st.write(
                        f"**Klip {i}** — "
                        f"{int(clip['start'])}s sampai "
                        f"{int(clip['end'])}s"
                    )
                    st.write(
                        f"Skor potensi: {clip['score']:.1f}/"
                        f"(skor heuristik)"
                    )
                    st.caption(clip["text"])

                zip_buffer = io.BytesIO()
                created = 0

                with st.spinner("Memotong video terpilih..."):
                    with zipfile.ZipFile(
                        zip_buffer,
                        "w",
                        zipfile.ZIP_DEFLATED
                    ) as zip_file:

                        for i, clip in enumerate(selected, start=1):
                            output_path = (
                                temp_path / f"clipcerdas_{i:02d}.mp4"
                            )

                            duration = clip["end"] - clip["start"]

                            cut_video(
                                input_path,
                                clip["start"],
                                duration,
                                output_path,
                                vertical
                            )

                            zip_file.write(
                                output_path,
                                output_path.name
                            )
                            created += 1

                zip_buffer.seek(0)

                st.success(f"Berhasil membuat {created} klip!")

                st.download_button(
                    "⬇️ Download Semua Klip (ZIP)",
                    data=zip_buffer.getvalue(),
                    file_name="ClipCerdas_AI_AutoClip.zip",
                    mime="application/zip",
                    use_container_width=True
                )

        except FileNotFoundError:
            st.error(
                "FFmpeg belum tersedia. Pastikan packages.txt "
                "berisi ffmpeg."
            )

        except Exception as error:
            st.error(f"Pemrosesan gagal: {error}")

else:
    st.info(
        "Unggah video terlebih dahulu untuk menjalankan "
        "AI Auto Clip. Link YouTube saat ini hanya untuk pratinjau."
    )

st.divider()

st.caption(
    "ClipCerdas AI • AI memilih momen berdasarkan transkrip, "
    "bukan jaminan viral."
)
