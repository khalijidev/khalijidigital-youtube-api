# KhalijiDigital YouTube Converter API

Engine converter terpisah untuk KhalijiDigital. Website utama tetap ringan; proses YouTube berjalan di service Python/Docker ini.

## Isi engine

- Python 3.12
- FastAPI + Uvicorn
- yt-dlp + yt-dlp-ejs
- FFmpeg + FFprobe
- Deno 2.9.6

yt-dlp mendokumentasikan FFmpeg/FFprobe, `yt-dlp-ejs`, dan runtime JavaScript seperti Deno sebagai komponen yang direkomendasikan untuk dukungan YouTube yang lengkap.

## API

`GET /healthz` — health check.

`POST /api/convert`

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "format": "mp3",
  "quality": "192"
}
```

Response sukses berisi `download_url` sementara. File dihapus setelah diunduh atau saat TTL 15 menit berakhir.

## Batas bawaan

- Maksimal durasi: 600 detik.
- Maksimal file hasil: 200 MB.
- Maksimal 5 request per IP dalam 10 menit pada satu instance.

Batas ini sengaja dibuat konservatif untuk deployment awal.

## Deploy ke GitHub + Render

1. Buat repository GitHub kosong, misalnya `khalijidigital-youtube-api`.
2. Upload seluruh isi folder ini ke repository.
3. Di Render pilih **New → Web Service**, hubungkan repository GitHub tersebut, dan gunakan Docker. Render mendukung deployment Web Service dari repository Git dan Dockerfile serta menyediakan custom domain.
4. Setelah service aktif, Render memberikan URL `*.onrender.com`.
5. Ubah `config/youtube.php` pada website KhalijiDigital agar `api_base_url` menunjuk ke URL service tersebut.
6. Saat custom domain sudah dibuat, gunakan misalnya `https://youtube-api.khalijidigital.my.id`.
7. Sangat disarankan mengisi `KD_YOUTUBE_API_KEY` di Render lalu nilai yang sama disimpan pada konfigurasi PHP server-side KhalijiDigital.

## Docker lokal

```bash
docker build -t khalijidigital-youtube-api .
docker run --rm -p 10000:10000 khalijidigital-youtube-api
```

Lalu buka `/healthz`.

## Catatan penggunaan

Gunakan layanan ini hanya untuk video/konten yang memang boleh kamu unduh atau konversi. Aturan dan ketersediaan akses YouTube dapat berubah, sehingga engine harus dipelihara dan diperbarui secara berkala.
