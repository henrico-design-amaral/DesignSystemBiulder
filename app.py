import json
import os
import uuid
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, Response, send_file, jsonify
from downloader import download_site

app = Flask(__name__)

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

# Auto-cleanup: remove ZIPs mais velhos que 10 minutos
def cleanup_old_files():
    while True:
        now = time.time()
        for f in DOWNLOADS_DIR.glob("*.zip"):
            if now - f.stat().st_mtime > 600:
                f.unlink(missing_ok=True)
        time.sleep(60)

threading.Thread(target=cleanup_old_files, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download")
def download():
    url = request.args.get("url", "").strip()
    if not url:
        return Response("data: {\"type\": \"error\", \"msg\": \"URL ausente.\"}\n\n", mimetype="text/event-stream")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    options = {
        "js":    request.args.get("js", "true") == "true",
        "lazy":  request.args.get("lazy", "true") == "true",
        "clean": request.args.get("clean", "true") == "true",
    }

    job_id = str(uuid.uuid4())[:8]
    zip_path = DOWNLOADS_DIR / f"{job_id}.zip"

    def generate():
        def emit(data: dict):
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            yield from emit({"type": "log", "msg": f"Iniciando captura de {url}", "level": "accent"})
            yield from emit({"type": "log", "msg": f"Opções: JS={options['js']} · Lazy={options['lazy']} · Limpeza={options['clean']}", "level": ""})

            for event in download_site(url, zip_path, options):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            if zip_path.exists():
                size = zip_path.stat().st_size
                yield from emit({
                    "type": "done",
                    "filename": zip_path.name,
                    "size": size,
                    "downloadUrl": f"/file/{job_id}",
                })
            else:
                yield from emit({"type": "error", "msg": "Falha ao gerar o arquivo ZIP."})

        except Exception as e:
            yield from emit({"type": "error", "msg": str(e)})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/file/<job_id>")
def get_file(job_id):
    # Sanitize: só permite alphanum e hífens
    safe_id = "".join(c for c in job_id if c.isalnum() or c == "-")
    zip_path = DOWNLOADS_DIR / f"{safe_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "Arquivo não encontrado ou expirado."}), 404
    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
