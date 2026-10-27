# ClipCerdas AI — MVP

Website sederhana untuk memotong satu video menjadi beberapa klip MP4. Bisa mengubah video menjadi vertikal 9:16.

## Fitur sekarang
- Upload video MP4, MOV, M4V, MKV, atau WebM
- Pilih durasi klip dan jumlah klip
- Memotong klip dengan titik awal yang tersebar sepanjang video
- Crop tengah ke 9:16 (opsional)
- Unduh semua klip sebagai ZIP

## Belum tersedia
- AI yang memilih momen viral
- Subtitle otomatis
- Pelacakan wajah/pembicara
- Login, database, pembayaran
- Penyimpanan video permanen

## Deploy gratis dengan HP Android

1. Ekstrak ZIP ini di HP menggunakan aplikasi Files/pengelola file.
2. Buka `github.com` di Chrome dan masuk atau buat akun.
3. Buat repository baru, misalnya `clipcerdas-ai`. Untuk memulai, pilih Public agar mudah dihubungkan ke hosting gratis. Jangan pernah unggah kata sandi, API key, atau video pribadi ke repository.
4. Di repository, pilih **Add file → Upload files**. Unggah `app.py`, `requirements.txt`, `packages.txt`, dan folder `.streamlit` yang berisi `config.toml`. Pastikan `app.py` berada di akar repository, bukan di dalam folder lain.
5. Buka `share.streamlit.io` dan masuk dengan GitHub.
6. Pilih **Create app**, pilih repository `clipcerdas-ai`, branch `main`, dan file utama `app.py`, lalu deploy.
7. Tunggu proses instalasi selesai. Pertama kali bisa memerlukan beberapa menit. Jika berhasil, website akan mendapat alamat `*.streamlit.app`.

> Catatan HP: mengunggah folder tersembunyi `.streamlit` melalui browser HP bisa merepotkan. Jika folder tersebut tidak terlihat atau sulit diunggah, MVP tetap bisa dicoba tanpa `config.toml`; hapus baris yang menyebut folder itu dari daftar upload. Pengaturan batas unggahan akan memakai default hosting.

## Cara memakai
1. Buka website.
2. Pilih video pendek untuk percobaan awal.
3. Pilih durasi dan jumlah klip.
4. Pilih atau hapus centang format vertikal.
5. Tekan **Buat klip**.
6. Unduh ZIP dan ekstrak untuk mendapatkan MP4.

## Batasan hosting gratis
Pengolahan video memakai CPU dan memori server. Video panjang/besar bisa gagal, timeout, atau membuat aplikasi tidur. Untuk uji pertama, pakai MP4 kecil, idealnya di bawah 100 MB dan durasi beberapa menit.

## Lisensi dan hak penggunaan
FFmpeg adalah perangkat lunak terpisah dengan ketentuan lisensinya sendiri. Gunakan video yang kamu miliki atau memiliki izin untuk diproses.
