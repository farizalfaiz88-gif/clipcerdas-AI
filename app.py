import io
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from faster_whisper import WhisperModel


st.set_page_config(page_title="ClipCerdas AI", page_icon="🎬", layout="wide")


# =====================================================
# 1. YOUTUBE
# =====================================================

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


def download_youtube(url, out_dir, max_height, max_minutes, cookies_path=None):
    """Unduh video dari link YouTube. Mengembalikan (path_video, judul)."""
    import yt_dlp

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if cookies_path:
        base_opts["cookiefile"] = str(cookies_path)

    # Cek durasi dulu supaya server tidak kewalahan
    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = info.get("duration") or 0
    if duration > max_minutes * 60:
        raise RuntimeError(
            f"Video berdurasi {duration / 60:.0f} menit, melebihi batas "
            f"{max_minutes} menit. Naikkan batas atau pilih video lain."
        )

    opts = dict(base_opts)
    opts.update({
        "format": (
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
    })

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [
        p for p in out_dir.glob("source.*")
        if p.suffix not in (".part", ".ytdl")
    ]
    if not files:
        raise RuntimeError("File video hasil unduhan tidak ditemukan.")

    files.sort(key=lambda p: (p.suffix != ".mp4", p.name))
    return files[0], info.get("title") or "video"


# =====================================================
# 2. FFmpeg & INFO VIDEO
# =====================================================

def get_video_duration(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Durasi video tidak dapat dibaca.")
    return float(result.stdout.strip())


def get_video_size(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Ukuran video tidak dapat dibaca.")
    w, h = result.stdout.strip().split("x")[:2]
    return int(w), int(h)


# =====================================================
# 3. TRANSKRIP (Whisper)
# =====================================================

@st.cache_resource(show_spinner=False)
def load_whisper(size):
    return WhisperModel(size, device="cpu", compute_type="int8")


def transcribe(model, video_path, language):
    segments, _info = model.transcribe(
        str(video_path),
        language=None if language == "auto" else language,
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
    )
    result = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        result.append({
            "start": float(s.start),
            "end": float(s.end),
            "text": text,
            "words": [
                {
                    "start": float(w.start),
                    "end": float(w.end),
                    "word": w.word,
                }
                for w in (s.words or [])
            ],
        })
    return result


# =====================================================
# 4. PEMILIHAN MOMEN VIRAL
# =====================================================

KEYWORDS = {
    # Indonesia
    "ternyata": 3, "rahasia": 3, "mengejutkan": 4, "bayangkan": 2,
    "kenapa": 2, "mengapa": 2, "bagaimana": 2, "kesalahan": 3,
    "bahaya": 3, "terburuk": 3, "terbaik": 2, "akhirnya": 2,
    "berhasil": 2, "gagal": 2, "fakta": 2, "jangan": 2, "serius": 2,
    "mustahil": 3, "luar biasa": 3, "tidak percaya": 3,
    "tidak menyangka": 3, "masalahnya": 2, "solusinya": 2,
    "gratis": 2, "penting": 1, "uang": 1, "wow": 2,
    # Inggris
    "secret": 3, "shocking": 4, "mistake": 3, "never": 2, "worst": 3,
    "best": 2, "finally": 2, "why": 2, "how": 1, "crazy": 3,
    "unbelievable": 3, "free": 2, "truth": 3, "actually": 1,
    "warning": 3, "danger": 3, "impossible": 3, "problem": 2,
    "solution": 2, "money": 1, "imagine": 2,
}

FILLER_WORDS = {"eee", "em", "anu", "hmm", "eh", "um", "uh"}


def overlaps_any(cand, chosen_list):
    return any(
        cand["start"] < c["end"] and cand["end"] > c["start"]
        for c in chosen_list
    )


def pick_non_overlap(candidates, n):
    candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)
    selected = []
    for cand in candidates:
        if not overlaps_any(cand, selected):
            selected.append(cand)
        if len(selected) >= n:
            break
    selected.sort(key=lambda c: c["start"])
    return selected


def segs_text(segs, start, end):
    return " ".join(
        s["text"] for s in segs if s["end"] > start and s["start"] < end
    )


def heuristic_candidates(segs, total, target):
    """Jendela klip yang selalu mulai dan berakhir di batas kalimat."""
    min_len, max_len = target * 0.6, target * 1.3
    n = len(segs)
    seen, cands = set(), []

    for i in range(n):
        start = segs[i]["start"]
        j = i
        while j < n - 1 and segs[j]["end"] - start < target:
            j += 1
        if segs[j]["end"] - start > max_len and j > i:
            j -= 1

        length = segs[j]["end"] - start
        if (i, j) in seen:
            continue
        if length < min_len or length > max_len:
            continue
        seen.add((i, j))

        window = segs[i:j + 1]
        text = " ".join(s["text"] for s in window)
        normalized = text.lower()
        words = re.findall(r"[\w'-]+", normalized)
        if not words:
            continue

        matched = [
            p for p in KEYWORDS
            if re.search(rf"\b{re.escape(p)}\b", normalized)
        ]
        keyword_score = min(sum(KEYWORDS[p] for p in matched), 8)

        speech_seconds = sum(s["end"] - s["start"] for s in window)
        density = min(speech_seconds / max(length, 1), 1.0)

        first = window[0]["text"].lower()
        hook = 1.5 if (
            "?" in first
            or any(re.search(rf"\b{re.escape(p)}\b", first) for p in matched)
        ) else 0.0

        unique_ratio = len(set(words)) / len(words)
        useful = sum(1 for w in words if w not in FILLER_WORDS)

        score = (
            density * 4
            + keyword_score
            + normalized.count("?") * 1.5
            + normalized.count("!")
            + hook
            + unique_ratio
            + min(useful / 35, 1.5)
        )

        cands.append({
            "start": max(0.0, start - 0.2),
            "end": min(total, segs[j]["end"] + 0.4),
            "score": min(100, round(score / 14 * 100)),
            "title": " ".join(text.split()[:8]) + "...",
            "reason": (
                "Kata pemancing: " + ", ".join(matched[:5])
                if matched else "Ucapan padat dan jelas"
            ),
            "text": text,
        })

    # Video sangat pendek: pakai seluruh video sebagai satu kandidat
    if not cands and total >= 5:
        text = " ".join(s["text"] for s in segs)
        cands.append({
            "start": 0.0,
            "end": total,
            "score": 50,
            "title": " ".join(text.split()[:8]) + "...",
            "reason": "Video pendek, dipakai seluruhnya",
            "text": text,
        })

    return cands


def snap_to_segments(segs, start, end):
    s = min(segs, key=lambda x: abs(x["start"] - start))["start"]
    e = min(segs, key=lambda x: abs(x["end"] - end))["end"]
    return s, e


def llm_candidates(segs, total, target, n, api_key, model_name):
    """Minta Claude memilih momen berdasarkan makna, bukan sekadar kata kunci."""
    from anthropic import Anthropic

    transcript = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs
    )[:120000]

    prompt = f"""You are an expert short-form video editor (TikTok, Reels, Shorts).
Below is a timestamped transcript of a long video. Pick the {n + 2} segments with
the highest viral potential.

Rules:
- Each clip should be about {target} seconds (between {int(target * 0.6)} and {int(target * 1.3)}).
- Start at the beginning of a sentence with a strong hook (a question, bold claim,
  surprise, or story start). End when a thought is complete.
- Prefer self-contained moments that make sense without the rest of the video.
- Clips must not overlap.
- Write "title" and "reason" in the same language as the transcript.

Return ONLY a JSON array, no other text. Each item:
{{"start": <seconds>, "end": <seconds>, "title": "<catchy title>",
  "reason": "<one sentence why it can go viral>", "score": <0-100>}}

Transcript:
{transcript}"""

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model_name,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    items = json.loads(raw[raw.find("["): raw.rfind("]") + 1])

    cands = []
    for item in items:
        try:
            s, e = snap_to_segments(
                segs, float(item["start"]), float(item["end"])
            )
            s, e = max(0.0, s - 0.2), min(total, e + 0.4)
            if e - s < 8:
                continue
            cands.append({
                "start": s,
                "end": e,
                "score": int(item.get("score", 50)),
                "title": str(item.get("title", "Klip"))[:80],
                "reason": str(item.get("reason", "")),
                "text": segs_text(segs, s, e),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return cands


def pick_clips(segs, total, target, n, api_key, llm_model):
    note = "Penilaian: heuristik kata kunci"
    cands = []

    if api_key:
        try:
            cands = llm_candidates(segs, total, target, n, api_key, llm_model)
            if cands:
                note = f"Penilaian: AI ({llm_model})"
        except Exception as error:
            note = f"AI gagal ({error}). Memakai penilaian heuristik."
            cands = []

    if not cands:
        cands = heuristic_candidates(segs, total, target)

    return pick_non_overlap(cands, n), note


# =====================================================
# 5. SUBTITLE (ASS, gaya karaoke per kata)
# =====================================================

def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(segs, start, end, path, out_w, out_h, words_per_line=3):
    words = []
    for seg in segs:
        if seg["end"] <= start or seg["start"] >= end:
            continue
        for w in seg["words"]:
            if w["end"] > start and w["start"] < end:
                text = w["word"].strip().replace("{", "").replace("}", "")
                if text:
                    words.append({
                        "start": max(w["start"], start) - start,
                        "end": min(w["end"], end) - start,
                        "word": text,
                    })

    groups, cur = [], []
    for w in words:
        if cur and (
            len(cur) >= words_per_line or w["start"] - cur[-1]["end"] > 0.8
        ):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    font_size = int(out_h * 0.042)
    margin_v = int(out_h * 0.20)
    outline = max(3, int(out_h * 0.004))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,DejaVu Sans,{font_size},&H0000FFFF,&H00FFFFFF,"
        f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,"
        f"60,60,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    lines = []
    for gi, g in enumerate(groups):
        g_start = g[0]["start"]
        g_end = g[-1]["end"]
        if gi + 1 < len(groups) and groups[gi + 1][0]["start"] - g_end < 0.3:
            g_end = groups[gi + 1][0]["start"]
        else:
            g_end += 0.15

        parts = []
        for k, w in enumerate(g):
            nxt = g[k + 1]["start"] if k + 1 < len(g) else w["end"]
            dur = max(1, round((nxt - w["start"]) * 100))
            parts.append(f"{{\\kf{dur}}}{w['word'].upper()}")

        lines.append(
            f"Dialogue: 0,{ass_time(g_start)},{ass_time(g_end)},"
            f"Default,,0,0,0,,{' '.join(parts)}"
        )

    Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


# =====================================================
# 6. REFRAMING 9:16 MENGIKUTI WAJAH
# =====================================================

def detect_crop_x(video_path, start, end, src_w, crop_w):
    """Cari posisi crop horizontal berdasarkan posisi wajah di klip."""
    center_default = (src_w - crop_w) / 2
    try:
        import cv2
    except ImportError:
        return center_default

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    cap = cv2.VideoCapture(str(video_path))
    centers = []
    t = start

    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            scale = 480 / frame.shape[1]
            small = cv2.resize(frame, None, fx=scale, fy=scale)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
            )
            if len(faces):
                x, _y, w, _h = max(faces, key=lambda f: f[2] * f[3])
                centers.append((x + w / 2) / scale)
        t += 1.0

    cap.release()

    if not centers:
        return center_default

    cx = statistics.median(centers)
    return max(0.0, min(cx - crop_w / 2, src_w - crop_w))


# =====================================================
# 7. RENDER KLIP
# =====================================================

def render_clip(src, start, end, out_path, segs, work_dir, vertical,
                subtitles, src_w, src_h, idx):
    filters = []

    if vertical:
        out_w, out_h = 1080, 1920
        crop_w = int(src_h * 9 / 16) // 2 * 2
        if crop_w < src_w:
            x = int(detect_crop_x(src, start, end, src_w, crop_w))
            filters.append(f"crop={crop_w}:{src_h}:{x}:0")
            filters.append("scale=1080:1920")
        else:
            filters.append(
                "scale=1080:1920:force_original_aspect_ratio=increase"
            )
            filters.append("crop=1080:1920")
    else:
        out_w, out_h = src_w, src_h

    if subtitles:
        ass_path = work_dir / f"sub_{idx:02d}.ass"
        make_ass(segs, start, end, ass_path, out_w, out_h)
        filters.append(f"ass={ass_path.name}")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0, start):.2f}",
        "-i", str(src),
        "-t", f"{end - start:.2f}",
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(work_dir)
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:])


# =====================================================
# 8. UTILITAS UI
# =====================================================

def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def safe_name(text):
    return re.sub(r"[^\w\-]+", "_", text).strip("_")[:40] or "klip"


def reset_job():
    old = st.session_state.get("job")
    if old:
        shutil.rmtree(old["work_dir"], ignore_errors=True)
    st.session_state.pop("job", None)
    st.session_state.pop("outputs", None)
    return Path(tempfile.mkdtemp(prefix="clipcerdas_"))


def get_default_api_key():
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


# =====================================================
# 9. TAMPILAN
# =====================================================

st.title("🎬 ClipCerdas AI")
st.write(
    "Tempel link YouTube, lalu AI mencari momen berpotensi viral dan "
    "membuat klip vertikal lengkap dengan subtitle otomatis."
)
st.caption(
    "Skor potensi viral adalah perkiraan berdasarkan transkrip, "
    "bukan jaminan. Selalu periksa hasil sebelum dipublikasikan."
)
st.divider()

# ---------- Langkah 1: sumber ----------
st.subheader("1. Sumber video")

source = st.radio(
    "Ambil video dari",
    ["🔗 Link YouTube", "📁 Upload file"],
    horizontal=True,
    label_visibility="collapsed",
)

youtube_url, uploaded_file, cookies_file = "", None, None

if source.startswith("🔗"):
    youtube_url = st.text_input(
        "Tempel link YouTube",
        placeholder="https://www.youtube.com/watch?v=...",
    )
    video_id = get_youtube_id(youtube_url) if youtube_url else None
    if youtube_url and not video_id:
        st.error("Link YouTube tidak valid.")
    if video_id:
        components.iframe(
            f"https://www.youtube-nocookie.com/embed/{video_id}",
            height=315,
            scrolling=False,
        )

    with st.expander("Unduhan diblokir YouTube? (opsional)"):
        st.write(
            "Server cloud sering diblokir YouTube. Kamu bisa mengunggah "
            "`cookies.txt` (format Netscape) dari browser. Berkas ini "
            "sensitif: hanya dipakai sementara dan dihapus setelah "
            "pemrosesan. Alternatif: pakai mode Upload file."
        )
        cookies_file = st.file_uploader("cookies.txt", type=["txt"])
else:
    uploaded_file = st.file_uploader(
        "Unggah video", type=["mp4", "mov", "mkv", "webm"]
    )

consent = st.checkbox(
    "Saya pemilik video ini, atau sudah mendapat izin untuk "
    "mengunduh dan mengolahnya."
)

st.divider()

# ---------- Langkah 2: pengaturan ----------
st.subheader("2. Pengaturan")

c1, c2, c3 = st.columns(3)
with c1:
    clip_duration = st.selectbox(
        "Target durasi klip",
        [15, 30, 45, 60, 90],
        index=1,
        format_func=lambda x: f"{x} detik",
    )
with c2:
    number_clips = st.number_input("Jumlah klip", 1, 10, 3, 1)
with c3:
    language = st.selectbox(
        "Bahasa percakapan",
        ["id", "en", "auto"],
        format_func=lambda x: {
            "id": "Indonesia", "en": "Inggris", "auto": "Deteksi otomatis"
        }[x],
    )

c4, c5, c6 = st.columns(3)
with c4:
    whisper_size = st.selectbox(
        "Akurasi transkrip",
        ["tiny", "base", "small"],
        index=1,
        format_func=lambda x: {
            "tiny": "Tiny (tercepat)",
            "base": "Base (seimbang)",
            "small": "Small (paling akurat, lambat)",
        }[x],
    )
with c5:
    max_height = st.selectbox(
        "Kualitas unduhan", [480, 720, 1080], index=1,
        format_func=lambda x: f"{x}p",
    )
with c6:
    max_minutes = st.number_input(
        "Batas durasi video (menit)", 5, 180, 60, 5
    )

o1, o2 = st.columns(2)
with o1:
    vertical = st.checkbox("Format vertikal 9:16 (ikuti wajah)", value=True)
with o2:
    subtitles = st.checkbox("Subtitle otomatis (karaoke)", value=True)

with st.expander("Penilaian viral dengan AI Claude (opsional, lebih akurat)"):
    st.write(
        "Tanpa API key, aplikasi memakai penilaian kata kunci sederhana. "
        "Dengan API key, Claude membaca transkrip dan memilih momen "
        "berdasarkan makna, lalu memberi judul dan alasannya."
    )
    api_key = st.text_input(
        "Anthropic API key",
        type="password",
        value=get_default_api_key(),
    )
    llm_model = st.text_input("Model", value="claude-sonnet-5-5")

st.divider()

# ---------- Jalankan analisis ----------
has_source = bool(
    (youtube_url and get_youtube_id(youtube_url)) or uploaded_file
)

if st.button(
    "✨ Analisis & Cari Momen Terbaik",
    type="primary",
    use_container_width=True,
    disabled=not (has_source and consent),
):
    work_dir = reset_job()
    cookies_path = None

    try:
        with st.status("Memproses video...", expanded=True) as status:
            if uploaded_file is not None:
                st.write("Menyimpan file...")
                video_path = work_dir / Path(uploaded_file.name).name
                video_path.write_bytes(uploaded_file.getbuffer())
                title = Path(uploaded_file.name).stem
            else:
                if cookies_file is not None:
                    cookies_path = work_dir / "cookies.txt"
                    cookies_path.write_bytes(cookies_file.getbuffer())
                st.write("Mengunduh video dari YouTube...")
                video_path, title = download_youtube(
                    youtube_url, work_dir, max_height,
                    max_minutes, cookies_path,
                )
                if cookies_path and cookies_path.exists():
                    cookies_path.unlink()

            total = get_video_duration(video_path)
            src_w, src_h = get_video_size(video_path)

            st.write(f"Mendengarkan ucapan (Whisper {whisper_size})...")
            model = load_whisper(whisper_size)
            segs = transcribe(model, video_path, language)
            if not segs:
                raise RuntimeError(
                    "Tidak ada ucapan yang terdeteksi. Pastikan video "
                    "memiliki audio yang jelas."
                )

            st.write("Memilih momen berpotensi viral...")
            clips, note = pick_clips(
                segs, total, clip_duration, int(number_clips),
                api_key.strip(), llm_model.strip(),
            )
            if not clips:
                raise RuntimeError(
                    "Tidak ditemukan momen yang cocok. Coba durasi klip "
                    "berbeda atau video lain."
                )

            status.update(label="Analisis selesai", state="complete")

        st.session_state["job"] = {
            "id": work_dir.name,
            "work_dir": work_dir,
            "video_path": video_path,
            "title": title,
            "total": total,
            "src_w": src_w,
            "src_h": src_h,
            "segs": segs,
            "clips": clips,
            "note": note,
        }

    except Exception as error:
        shutil.rmtree(work_dir, ignore_errors=True)
        st.error(f"Pemrosesan gagal: {error}")
        st.stop()

if not (has_source and consent):
    st.info(
        "Masukkan link YouTube (atau upload file) dan centang "
        "pernyataan izin untuk memulai."
    )

# ---------- Langkah 3: hasil analisis ----------
job = st.session_state.get("job")

if job:
    st.subheader("3. Momen yang ditemukan")
    st.caption(
        f"**{job['title']}** • durasi {fmt_time(job['total'])} • "
        f"{job['note']}"
    )

    chosen = []
    for i, clip in enumerate(job["clips"]):
        label = (
            f"Klip {i + 1} • {clip['title']} • "
            f"potensi {clip['score']}/100"
        )
        with st.expander(label, expanded=(i == 0)):
            if clip["reason"]:
                st.write(clip["reason"])
            st.caption(clip["text"][:350])

            use = st.checkbox(
                "Pakai klip ini", value=True, key=f"use_{job['id']}_{i}"
            )
            a, b = st.columns(2)
            s = a.number_input(
                "Mulai (detik)", 0.0, float(job["total"]),
                float(clip["start"]), 0.5, key=f"s_{job['id']}_{i}",
            )
            e = b.number_input(
                "Selesai (detik)", 0.0, float(job["total"]),
                float(clip["end"]), 0.5, key=f"e_{job['id']}_{i}",
            )
            st.video(
                str(job["video_path"]),
                start_time=int(s),
                end_time=int(e),
            )
            if use:
                chosen.append((i, clip, s, e))

    if st.button(
        "🎬 Buat Klip Terpilih",
        type="primary",
        use_container_width=True,
        disabled=not chosen,
    ):
        outputs = []
        progress = st.progress(0.0)
        try:
            for k, (i, clip, s, e) in enumerate(chosen):
                if e - s < 3:
                    raise RuntimeError(
                        f"Klip {i + 1} terlalu pendek (minimal 3 detik)."
                    )
                out_path = job["work_dir"] / f"clip_{i + 1:02d}.mp4"
                progress.progress(
                    k / len(chosen),
                    text=f"Merender klip {i + 1}...",
                )
                render_clip(
                    job["video_path"], s, e, out_path, job["segs"],
                    job["work_dir"], vertical, subtitles,
                    job["src_w"], job["src_h"], i + 1,
                )
                outputs.append({
                    "name": f"{i + 1:02d}_{safe_name(clip['title'])}.mp4",
                    "title": clip["title"],
                    "data": out_path.read_bytes(),
                })
            progress.progress(1.0, text="Selesai")

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
                for out in outputs:
                    z.writestr(out["name"], out["data"])

            st.session_state["outputs"] = {
                "job_id": job["id"],
                "clips": outputs,
                "zip": zip_buffer.getvalue(),
            }
        except Exception as error:
            st.error(f"Gagal membuat klip: {error}")

# ---------- Langkah 4: hasil klip ----------
outputs = st.session_state.get("outputs")

if job and outputs and outputs["job_id"] == job["id"]:
    st.divider()
    st.subheader("4. Klip siap unduh")
    st.success(f"Berhasil membuat {len(outputs['clips'])} klip!")

    st.download_button(
        "⬇️ Download Semua Klip (ZIP)",
        data=outputs["zip"],
        file_name="ClipCerdas_AI_Klip.zip",
        mime="application/zip",
        use_container_width=True,
    )

    cols = st.columns(min(len(outputs["clips"]), 3))
    for idx, out in enumerate(outputs["clips"]):
        with cols[idx % len(cols)]:
            st.caption(out["title"])
            st.video(out["data"])
            st.download_button(
                "Download",
                data=out["data"],
                file_name=out["name"],
                mime="video/mp4",
                key=f"dl_{job['id']}_{idx}",
            )

st.divider()
st.caption(
    "ClipCerdas AI • Gunakan hanya untuk video milikmu atau yang sudah "
    "berizin. Skor viral adalah perkiraan, bukan jaminan."
)
