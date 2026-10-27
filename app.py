import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

APP_NAME = "ClipCerdas AI"
MAX_CLIPS = 10

st.set_page_config(
    page_title=APP_NAME,
    page_icon="✂️",
    layout="centered",
)

st.markdown("""
<style>
  .block-container {max-width: 900px; padding-top: 2rem; padding-bottom: 3rem;}
  .hero {padding: 1.4rem; border-radius: 18px; background: linear-gradient(135deg,#0f2f25,#176b4a); color: white; margin-bottom: 1rem;}
  .hero h1 {margin: 0 0 .35rem 0; font-size: 2rem;}
  .hero p {margin: 0; opacity: .92;}
  div.stButton > button[kind="primary"] {background: #176b4a; border-color: #176b4a;}
</style>
<div class="hero">
  <h1>✂️ ClipCerdas AI</h1>
  <p>Potong video panjang menjadi klip pendek vertikal — versi MVP gratis.</p>
</div>
""", unsafe_allow_html=True)

st.info(
    "Versi awal ini memotong video berdasarkan waktu secara otomatis. "
    "Belum memilih momen viral dengan AI dan belum membuat subtitle otomatis."
)

def command_exists(name: str) -> bool:
    return shutil.which(name) is not None

def run_cmd(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        detail = result.stderr[-2500:] if result.stderr else "Tidak ada detail dari FFmpeg."
        raise RuntimeError(detail)
    return result.stdout.strip()

def get_duration(path: str) -> float:
    output = run_cmd([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ])
    return float(output)

def safe_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return (stem or "video")[:50]

if not command_exists("ffmpeg") or not command_exists("ffprobe"):
    st.error("Mesin video belum tersedia. Pastikan FFmpeg terpasang melalui packages.txt saat deploy.")
    st.stop()

uploaded = st.file_uploader(
    "1. Pilih video dari HP",
    type=["mp4", "mov", "m4v", "mkv", "webm"],
    help="Untuk uji coba pertama, gunakan video pendek berukuran kecil (disarankan di bawah 100 MB)."
)

if uploaded:
    size_mb = uploaded.size / (1024 * 1024)
    st.caption(f"File: {uploaded.name} · {size_mb:.1f} MB")
    if size_mb > 180:
        st.warning("File cukup besar untuk hosting gratis. Coba video yang lebih kecil agar pemrosesan tidak kehabisan memori atau waktu.")

    st.subheader("2. Atur klip")
    col1, col2 = st.columns(2)
    with col1:
        clip_seconds = st.select_slider(
            "Durasi tiap klip",
            options=[10, 15, 20, 30, 45, 60],
            value=30,
            format_func=lambda x: f"{x} detik"
        )
    with col2:
        clip_count = st.number_input(
            "Jumlah klip",
            min_value=1,
            max_value=MAX_CLIPS,
            value=3,
            step=1
        )

    vertical = st.checkbox("Ubah ke format vertikal 9:16 (720 × 1280)", value=True)
    st.caption("Pemotongan versi ini memilih titik mulai yang tersebar sepanjang video. Jika durasi video tidak cukup, jumlah klip akan dikurangi otomatis.")

    if st.button("Buat klip", type="primary", use_container_width=True):
        with st.spinner("Memproses video… Jangan tutup halaman sampai selesai."):
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    input_path = os.path.join(tmp, "input_video")
                    with open(input_path, "wb") as f:
                        f.write(uploaded.getbuffer())

                    total_duration = get_duration(input_path)
                    if total_duration <= 0:
                        raise RuntimeError("Durasi video tidak dapat dibaca.")

                    possible = int(total_duration // clip_seconds)
                    actual_count = min(int(clip_count), max(1, possible))
                    if total_duration < clip_seconds:
                        clip_seconds_actual = max(1, int(total_duration))
                        actual_count = 1
                    else:
                        clip_seconds_actual = int(clip_seconds)

                    if actual_count < int(clip_count):
                        st.warning(f"Video hanya cukup untuk sekitar {actual_count} klip berdurasi {clip_seconds_actual} detik.")

                    max_start = max(0, total_duration - clip_seconds_actual)
                    if actual_count == 1:
                        starts = [0.0]
                    else:
                        # Sebar titik awal dari awal hingga akhir video.
                        starts = [
                            max_start * i / (actual_count - 1)
                            for i in range(actual_count)
                        ]

                    output_paths = []
                    base_name = safe_name(uploaded.name)

                    for i, start in enumerate(starts, start=1):
                        out_path = os.path.join(tmp, f"{base_name}_klip_{i:02d}.mp4")
                        filters = []
                        if vertical:
                            filters.append("scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1")
                        cmd = [
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-ss", f"{start:.3f}",
                            "-i", input_path,
                            "-t", str(clip_seconds_actual),
                        ]
                        if filters:
                            cmd += ["-vf", ",".join(filters)]
                        cmd += [
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                            "-c:a", "aac", "-b:a", "128k",
                            "-movflags", "+faststart",
                            out_path
                        ]
                        run_cmd(cmd)
                        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                            raise RuntimeError(f"Klip {i} gagal dibuat.")
                        output_paths.append(out_path)

                    zip_path = os.path.join(tmp, f"{base_name}_clipcerdas.zip")
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for path in output_paths:
                            zf.write(path, arcname=os.path.basename(path))

                    st.success(f"Selesai! {len(output_paths)} klip berhasil dibuat.")
                    st.caption("Unduh ZIP untuk menyimpan semua klip. Setelah diekstrak, file MP4 dapat diunggah ke platform pilihanmu.")
                    with open(zip_path, "rb") as f:
                        st.download_button(
                            "⬇️ Unduh semua klip (ZIP)",
                            data=f.read(),
                            file_name=os.path.basename(zip_path),
                            mime="application/zip",
                            use_container_width=True
                        )

                    with st.expander("Pratinjau klip"):
                        for path in output_paths:
                            st.markdown(f"**{os.path.basename(path)}**")
                            st.video(path)
            except Exception as exc:
                st.error("Video belum berhasil diproses.")
                st.code(str(exc))
                st.markdown(
                    "Coba video MP4 yang lebih pendek/kecil. Jika error tetap muncul, "
                    "simpan pesan error di atas untuk diperiksa."
                )

st.divider()
st.markdown("**Rencana fitur berikutnya:** subtitle bahasa Indonesia → saran bagian menarik → crop pembicara → login dan paket penggunaan.")
st.caption("Gunakan hanya video yang kamu miliki atau punya izin untuk mengedit dan mengunggahnya.")
