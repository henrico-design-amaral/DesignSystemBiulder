from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request, send_file

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

RETENTION_SECONDS = max(60, int(os.getenv("DOWNLOAD_RETENTION_SECONDS", "600")))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "2")))
RATE_LIMIT_WINDOW_SECONDS = max(10, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
RATE_LIMIT_MAX_REQUESTS = max(1, int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5")))
MAX_URL_LENGTH = max(128, int(os.getenv("MAX_URL_LENGTH", "2048")))
REQUIRE_API_TOKEN = os.getenv("APP_API_TOKEN", "").strip()
ALLOWED_DOMAIN_SUFFIXES = {
    item.strip().lower()
    for item in os.getenv("ALLOWED_DOMAIN_SUFFIXES", "").split(",")
    if item.strip()
}
DENIED_DOMAIN_SUFFIXES = {
    item.strip().lower()
    for item in os.getenv(
        "DENIED_DOMAIN_SUFFIXES",
        "localhost,local,internal,test,example,invalid",
    ).split(",")
    if item.strip()
}

_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
_rate_limit_lock = threading.Lock()
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def health():
    return jsonify(
        {
            "status": "ok",
            "active_capacity": MAX_CONCURRENT_JOBS,
            "retention_seconds": RETENTION_SECONDS,
        }
    )


@app.route("/download")
def download():
    if REQUIRE_API_TOKEN:
        provided_token = request.headers.get("X-API-Token", "").strip()
        if provided_token != REQUIRE_API_TOKEN:
            return _sse_error("Acesso não autorizado.", status=401)

    if not _check_rate_limit(_client_ip()):
        return _sse_error(
            "Limite temporário de requisições atingido. Aguarde alguns instantes.",
            status=429,
        )

    raw_url = request.args.get("url", "").strip()
    if not raw_url:
        return _sse_error("URL ausente.", status=400)

    try:
        normalized_url = _normalize_url(raw_url)
    except ValueError as exc:
        return _sse_error(str(exc), status=400)

    validation_error = _validate_target_url(normalized_url)
    if validation_error:
        return _sse_error(validation_error, status=400)

    if not _job_slots.acquire(blocking=False):
        return _sse_error(
            "Capacidade temporariamente esgotada. Tente novamente em instantes.",
            status=429,
        )

    options = {
        "js": request.args.get("js", "true") == "true",
        "lazy": request.args.get("lazy", "true") == "true",
        "clean": request.args.get("clean", "true") == "true",
    }

    job_id = str(uuid.uuid4())[:8]
    zip_path = DOWNLOADS_DIR / f"{job_id}.zip"
    design_system_path = DOWNLOADS_DIR / f"{job_id}.design-system.html"
    started_at = time.monotonic()

    def generate() -> Iterable[str]:
        summary: dict | None = None

        def emit(data: dict) -> str:
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            from downloader import download_site

            yield emit(
                {
                    "type": "log",
                    "msg": f"Iniciando captura de {normalized_url}",
                    "level": "accent",
                }
            )
            yield emit(
                {
                    "type": "log",
                    "msg": (
                        f"Opções: JS={options['js']} · Lazy={options['lazy']} · "
                        f"Limpeza={options['clean']}"
                    ),
                }
            )

            for event in download_site(
                normalized_url,
                zip_path,
                options,
                design_system_output_path=design_system_path,
            ):
                if event.get("type") == "summary":
                    summary = event
                    continue
                yield emit(event)

            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if zip_path.exists():
                size = zip_path.stat().st_size
                yield emit(
                    {
                        "type": "done",
                        "filename": zip_path.name,
                        "size": size,
                        "files": summary.get("files") if summary else None,
                        "elapsed": elapsed_ms,
                        "downloadUrl": f"/file/{job_id}",
                        "designSystemUrl": (
                            f"/design-system/{job_id}"
                            if design_system_path.exists()
                            else None
                        ),
                    }
                )
            else:
                yield emit({"type": "error", "msg": "Falha ao gerar o arquivo ZIP."})
        except Exception as exc:  # pragma: no cover - defensive path
            yield emit({"type": "error", "msg": str(exc)})
        finally:
            _job_slots.release()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/file/<job_id>")
def get_file(job_id):
    safe_id = _safe_job_id(job_id)
    zip_path = DOWNLOADS_DIR / f"{safe_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "Arquivo não encontrado ou expirado."}), 404
    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)


@app.route("/design-system/<job_id>")
def get_design_system(job_id):
    safe_id = _safe_job_id(job_id)
    html_path = DOWNLOADS_DIR / f"{safe_id}.design-system.html"
    if not html_path.exists():
        return jsonify({"error": "Design system não encontrado ou expirado."}), 404
    return send_file(html_path, mimetype="text/html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_job_id(job_id: str) -> str:
    return "".join(c for c in job_id if c.isalnum() or c == "-")


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"



def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_buckets[client_ip]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        bucket.append(now)
        return True



def _normalize_url(url: str) -> str:
    trimmed = url.strip()
    if len(trimmed) > MAX_URL_LENGTH:
        raise ValueError("URL longa demais.")
    if not trimmed.startswith(("http://", "https://")):
        trimmed = "https://" + trimmed
    return trimmed



def _validate_target_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return "URL inválida."

    if parsed.scheme not in {"http", "https"}:
        return "Apenas URLs HTTP/HTTPS são suportadas."
    if not parsed.netloc:
        return "URL inválida."
    if parsed.username or parsed.password:
        return "URLs com credenciais embutidas não são permitidas."

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return "Hostname inválido."

    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return "Destinos locais não são permitidos."

    if hostname.endswith(".local"):
        return "Destinos locais não são permitidos."

    if ALLOWED_DOMAIN_SUFFIXES and not any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in ALLOWED_DOMAIN_SUFFIXES
    ):
        return "Este domínio não está autorizado neste ambiente."

    if any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in DENIED_DOMAIN_SUFFIXES
    ):
        return "Este domínio não é permitido."

    try:
        ip = ipaddress.ip_address(hostname)
        if _is_forbidden_ip(ip):
            return "Destinos privados ou reservados não são permitidos."
        return None
    except ValueError:
        pass

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return "Não foi possível resolver o domínio informado."

    for _, _, _, _, sockaddr in addrinfo:
        candidate_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(candidate_ip)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            return "Destinos privados ou reservados não são permitidos."

    return None



def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )



def _sse_error(message: str, status: int = 200) -> Response:
    payload = json.dumps({"type": "error", "msg": message}, ensure_ascii=False)
    return Response(f"data: {payload}\n\n", status=status, mimetype="text/event-stream")



def cleanup_old_files() -> None:
    while True:
        now = time.time()
        for file_path in DOWNLOADS_DIR.glob("*"):
            try:
                if now - file_path.stat().st_mtime > RETENTION_SECONDS:
                    file_path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
        time.sleep(60)


threading.Thread(target=cleanup_old_files, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
