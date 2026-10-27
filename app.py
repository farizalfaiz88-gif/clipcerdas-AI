import os, re, shutil, subprocess, tempfile, zipfile
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="ClipCerdas AI", page_icon="✂️", layout="centered")
st.markdown("""
<style>
.block-container{max-width:900px;padding-top:1.5rem}
.hero{padding:1.3rem;border-radius:18px;background:linear-gradient(135deg,#0f2f25,#176b4a);color:white}
.hero h1{margin:0}.hero p{margin:.4rem 0 0}
</style>
<div class="hero"><h1>✂️ ClipCerdas AI</h1><p>Potong video pendek dengan rekomendasi momen dan subtitle Indonesia.</p></div>
""", unsafe_allow_html=True)
st.info("MVP v2. Mode AI memakai transkripsi ucapan dan aturan sederhana; rekomendasi bukan jaminan viral.")

def run(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or "Proses gagal")[-2500:])
    return p.stdout.strip()

def duration(path):
    return float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",path]))

def safe(s):
    return (re.sub(r"[^A-Za-z0-9_-]+","_",Path(s).stem).strip("_") or "video")[:45]

def transcribe(path):
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise RuntimeError("faster-whisper belum terpasang. Pastikan requirements.txt berisi faster-whisper>=1.0,<2.0 lalu tunggu Streamlit selesai reboot. Detail: "+str(e))
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segs, info = model.transcribe(path, language="id", vad_filter=True, beam_size=1)
    rows = [{"start":float(s.start),"end":float(s.end),"text":s.text.strip()} for s in segs if s.text.strip()]
    if not rows: raise RuntimeError("Tidak ditemukan ucapan yang jelas pada video.")
    return rows

def score(rows, a, b):
    selected=[r for r in rows if r["end"]>a and r["start"]<b]
    text=" ".join(r["text"] for r in selected)
    words=re.findall(r"\w+",text.lower())
    hooks=["ternyata","rahasia","jangan","cara","kenapa","tips","penting","bahaya","mudah","gagal","berhasil","uang","hemat","solusi","kesalahan","wajib","fakta"]
    s=min(len(words)/max(1,b-a),3.5)+sum(1.8 for w in hooks if re.search(r"\b"+w+r"\b",text.lower()))
    s+=min(3,sum(1.2 for r in selected if "?" in r["text"] or "!" in r["text"]))
    return s,text

def pick(rows,total,secs,count):
    maxstart=max(0,total-secs)
    if maxstart==0:return [(0,total,"")]
    candidates=[]; t=0
    while t<=maxstart+.01:
        end=min(total,t+secs); sc,txt=score(rows,t,end)
        if txt:candidates.append((sc,t,end,txt))
        t+=max(3,min(8,secs/4))
    candidates.sort(reverse=True)
    chosen=[]
    for sc,a,b,txt in candidates:
        if all(abs(a,x[0])>=secs*.65 for x in chosen):chosen.append((a,b,txt))
        if len(chosen)>=count:break
    if not chosen:
        chosen=[(maxstart*i/max(1,count-1),min(total,maxstart*i/max(1,count-1)+secs),"") for i in range(count)]
    return sorted(chosen,key=lambda x:x[0])

def ts(v):
    n=max(0,int(round(v*1000))); h,n=divmod(n,3600000); m,n=divmod(n,60000); s,ms=divmod(n,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def srt(rows,a,b,path):
    lines=[]; i=1
    for r in rows:
        x=max(a,r["start"]); y=min(b,r["end"])
        if y<=x:continue
        lines += [str(i),f"{ts(x-a)} --> {ts(y-a)}",r["text"].replace("-->","→").replace("\n"," "),""]
        i+=1
    open(path,"w",encoding="utf-8").write("\n".join(lines))
    return i>1

def esc(p): return p.replace("\\","\\\\").replace(":","\\:").replace("'","\\'").replace(",","\\,")

if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    st.error("FFmpeg tidak tersedia. Periksa packages.txt harus berisi ffmpeg."); st.stop()

up=st.file_uploader("1. Pilih video dari HP",type=["mp4","mov","m4v","mkv","webm"])
if up:
    mb=up.size/1048576
    st.caption(f"{up.name} · {mb:.1f} MB")
    if mb>100:st.warning("Video besar mungkin gagal di hosting gratis. Coba file di bawah 50–100 MB.")
    mode=st.radio("Mode",["Potong berdasarkan waktu","AI rekomendasikan momen + subtitle Indonesia"])
    c1,c2=st.columns(2)
    with c1: secs=st.select_slider("Durasi tiap klip",options=[10,15,20,30,45,60],value=20,format_func=lambda x:f"{x} detik")
    with c2: count=st.number_input("Jumlah klip",min_value=1,max_value=10,value=3)
    vertical=st.checkbox("Format vertikal 9:16 (720 × 1280)",value=True)
    if mode.startswith("AI"):st.caption("AI menilai kepadatan ucapan dan kata kunci. Pertama kali bisa lebih lama karena model diunduh.")
    if st.button("Buat klip",type="primary",use_container_width=True):
        with st.spinner("Memproses video, jangan tutup halaman…"):
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    inp=os.path.join(tmp,"input_video")
                    open(inp,"wb").write(up.getbuffer())
                    total=duration(inp)
                    rows=[]
                    if mode.startswith("AI"):
                        st.write("Menganalisis ucapan bahasa Indonesia…")
                        rows=transcribe(inp)
                        windows=pick(rows,total,float(secs),int(count))
                    else:
                        n=min(int(count),max(1,int(total//secs))); length=min(float(secs),total); mx=max(0,total-length)
                        starts=[0] if n==1 else [mx*i/(n-1) for i in range(n)]
                        windows=[(a,min(total,a+length),"") for a in starts]
                    name=safe(up.name); outs=[]; notes=[]
                    for i,(a,b,excerpt) in enumerate(windows,1):
                        out=os.path.join(tmp,f"{name}_klip_{i:02}.mp4")
                        cmd=["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",f"{a:.3f}","-i",inp,"-t",f"{max(.5,b-a):.3f}"]
                        vf=[]
                        if rows:
                            sp=os.path.join(tmp,f"sub_{i:02}.srt")
                            if srt(rows,a,b,sp):
                                vf.append("subtitles='"+esc(sp)+"':force_style='FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=45'")
                        if vertical:vf.append("scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1")
                        if vf:cmd += ["-vf",",".join(vf)]
                        cmd += ["-c:v","libx264","-preset","ultrafast","-crf","25","-c:a","aac","-b:a","128k","-movflags","+faststart",out]
                        run(cmd)
                        if not os.path.exists(out) or os.path.getsize(out)==0:raise RuntimeError(f"Klip {i} gagal dibuat.")
                        outs.append(out)
                        if excerpt:notes.append(f"Klip {i}: {excerpt[:200]}")
                    zp=os.path.join(tmp,f"{name}_clipcerdas.zip")
                    with zipfile.ZipFile(zp,"w",zipfile.ZIP_DEFLATED) as z:
                        for p in outs:z.write(p,arcname=os.path.basename(p))
                    st.success(f"Selesai! {len(outs)} klip berhasil dibuat.")
                    if rows:
                        st.success("Subtitle Indonesia ditanam ke video yang memiliki ucapan.")
                        with st.expander("Lihat transkrip"):st.write(" ".join(r["text"] for r in rows))
                        with st.expander("Cuplikan ucapan dari rekomendasi"):
                            for n in notes:st.write(n)
                    st.download_button("⬇️ Unduh semua klip (ZIP)",data=open(zp,"rb").read(),file_name=os.path.basename(zp),mime="application/zip",use_container_width=True)
                    with st.expander("Pratinjau klip"):
                        for p in outs:st.markdown("**"+os.path.basename(p)+"**");st.video(p)
            except Exception as e:
                st.error("Video belum berhasil diproses.");st.code(str(e))
                st.caption("Coba MP4 kecil 1–3 menit. Mode AI lebih berat daripada mode waktu.")
st.divider()
st.caption("Mode AI memakai faster-whisper model tiny dan heuristik transkrip, bukan prediksi viral. Crop vertikal masih mengambil bagian tengah. Gunakan video yang kamu miliki atau punya izin.")
