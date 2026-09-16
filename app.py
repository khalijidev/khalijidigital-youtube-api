from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

APP_NAME = "KhalijiDigital YouTube Converter API"
BASE_DIR = Path(os.getenv("KD_TEMP_DIR", "/tmp/khalijidigital-youtube"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
JOB_TTL = max(60, int(os.getenv("KD_JOB_TTL", "900")))
MAX_SECONDS = max(30, int(os.getenv("KD_MAX_SECONDS", "600")))
MAX_FILESIZE = os.getenv("KD_MAX_FILESIZE", "200M")
API_KEY = os.getenv("KD_YOUTUBE_API_KEY", "").strip()
ALLOWED_ORIGINS = [x.strip() for x in os.getenv(
    "KD_ALLOWED_ORIGINS",
    "https://khalijidigital.my.id,https://www.khalijidigital.my.id"
).split(",") if x.strip()]

app = FastAPI(title=APP_NAME, version="1.0.0")

# In-memory download registry. This intentionally does not require a database.
JOBS: dict[str, dict[str, Any]] = {}
RATE: dict[str, list[float]] = {}


class ConvertRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    format: str = Field(default="mp3")
    quality: str = Field(default="192")


def cleanup_expired() -> None:
    now = time.time()
    expired = [token for token, job in JOBS.items() if job["expires"] <= now]
    for token in expired:
        job = JOBS.pop(token, None)
        if job:
            shutil.rmtree(job["dir"], ignore_errors=True)


def check_api_key(x_api_key: str | None) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key tidak valid.")


def check_rate_limit(request: Request) -> None:
    # Conservative in-memory guard for public deployments.
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = 600.0
    limit = 5
    recent = [t for t in RATE.get(ip, []) if now - t < window]
    if len(recent) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Batas percobaan sementara tercapai. Coba lagi beberapa menit lagi.",
        )
    recent.append(now)
    RATE[ip] = recent


def validate_youtube_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    allowed = {
        "youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
    if parsed.scheme not in {"http", "https"} or host not in allowed:
        raise HTTPException(status_code=400, detail="URL harus berasal dari YouTube.")
    return value


def safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r"\s+", " ", name)
    return (name or fallback)[:180]


def choose_quality(fmt: str, quality: str) -> str:
    if fmt == "mp3":
        return quality if quality in {"128", "192", "256", "320"} else "192"
    if fmt == "mp4":
        return quality if quality in {"360", "480", "720", "1080"} else "720"
    raise HTTPException(status_code=400, detail="Format harus MP3 atau MP4.")


def run_ytdlp(url: str, fmt: str, quality: str, out_dir: Path) -> Path:
    template = str(out_dir / "%(title).180B.%(ext)s")
    common = [
        "python",
        "-m",
        "yt_dlp",
        "--js-runtimes",
        "deno:/usr/local/bin/deno",
        "--no-playlist",
        "--no-part",
        "--restrict-filenames",
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
        "--max-filesize",
        MAX_FILESIZE,
        "--match-filter",
        f"duration <= {MAX_SECONDS}",
        "--ffmpeg-location",
        "/usr/bin",
        "--output",
        template,
        url,
    ]

    if fmt == "mp3":
        cmd = common[:-1] + [
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            f"{quality}K",
        ] + common[-1:]
    else:
        cmd = common[:-1] + [
            "-f",
            f"bv*[height<={quality}]+ba/b[height<={quality}]",
            "--merge-output-format",
            "mp4",
        ] + common[-1:]

    completed = subprocess.run(
        cmd,
        cwd=out_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=MAX_SECONDS + 90,
        check=False,
    )

    if completed.returncode != 0:
        output = (completed.stdout or "").strip()
        # Keep API errors concise while retaining the last useful yt-dlp line.
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        detail = lines[-1] if lines else "Konversi gagal pada engine yt-dlp."
        raise HTTPException(status_code=400, detail=detail[:500])

    suffix = ".mp3" if fmt == "mp3" else ".mp4"
    files = sorted(
        [p for p in out_dir.glob(f"*{suffix}") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise HTTPException(status_code=500, detail="Engine selesai tetapi file hasil tidak ditemukan.")
    return files[0]


def remove_job(token: str) -> None:
    job = JOBS.pop(token, None)
    if job:
        shutil.rmtree(job["dir"], ignore_errors=True)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": APP_NAME, "status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    cleanup_expired()
    return {"status": "ok", "service": APP_NAME}


@app.post("/api/convert")
def convert(
    payload: ConvertRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    cleanup_expired()
    check_api_key(x_api_key)
    check_rate_limit(request)

    url = validate_youtube_url(payload.url)
    fmt = payload.format.lower().strip()
    quality = choose_quality(fmt, payload.quality.strip())

    job_id = secrets.token_hex(12)
    token = secrets.token_urlsafe(32)
    out_dir = BASE_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=False)

    try:
        result = run_ytdlp(url, fmt, quality, out_dir)
        filename = safe_filename(result.name, f"khalijidigital-youtube.{fmt}")
        if result.name != filename:
            renamed = result.with_name(filename)
            result.rename(renamed)
            result = renamed

        expires = time.time() + JOB_TTL
        JOBS[token] = {
            "dir": out_dir,
            "path": result,
            "filename": filename,
            "expires": expires,
        }
        return JSONResponse({
            "ok": True,
            "job": token,
            "filename": filename,
            "format": fmt,
            "size": result.stat().st_size,
            "download_url": f"/api/download/{token}",
            "expires_in": JOB_TTL,
        })
    except HTTPException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Konversi melewati batas waktu.")
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Engine error: {str(exc)[:300]}") from exc


@app.get("/api/download/{token}")
def download(token: str):
    cleanup_expired()
    job = JOBS.get(token)
    if not job or job["expires"] <= time.time():
        remove_job(token)
        raise HTTPException(status_code=404, detail="File tidak ditemukan atau sudah kedaluwarsa.")

    path = Path(job["path"])
    if not path.is_file():
        remove_job(token)
        raise HTTPException(status_code=404, detail="File hasil tidak tersedia.")

    response = FileResponse(
        path,
        media_type="audio/mpeg" if path.suffix.lower() == ".mp3" else "video/mp4",
        filename=job["filename"],
        headers={"Cache-Control": "private, no-store, max-age=0"},
    )
    # File is deleted after the response is fully handed to the client by the ASGI server.
    from starlette.background import BackgroundTask
    response.background = BackgroundTask(remove_job, token)
    return response
