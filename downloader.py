"""
downloader.py — lógica de captura, processamento e empacotamento.

Principais melhorias desta versão:
- assets gravados em disco temporário, não em RAM
- reescrita de url(...) também dentro dos CSS baixados
- limites de tamanho para reduzir risco de OOM
- saída de metadados para o frontend/backend
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SCROLL_DELAY = 0.4
SCROLL_STEPS = 6
WAIT_AFTER_LOAD_MS = 2_000
PAGE_TIMEOUT_MS = 45_000
POST_SCROLL_WAIT_MS = 800

MAX_ASSET_BYTES = max(256_000, int(os.getenv("MAX_ASSET_BYTES", str(25 * 1024 * 1024))))
MAX_CAPTURED_RESOURCES = max(50, int(os.getenv("MAX_CAPTURED_RESOURCES", "600")))

FRAMEWORK_PATTERNS = [
    r"_next/static",
    r"__nuxt",
    r"gatsby",
    r"__remix",
    r"webpack",
]

SCROLL_LIB_PATTERNS = [
    r"lenis",
    r"locomotive",
    r"smooth-scroll",
    r"@studio-freight",
]

RESOURCE_EXTS = {
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".ico",
    ".mp4",
    ".webm",
    ".avif",
}

CAPTURED_RESOURCE_TYPES = {
    "stylesheet",
    "script",
    "image",
    "font",
    "media",
    "fetch",
    "xhr",
    "other",
}

CSS_URL_PATTERN = re.compile(r"url\(([^)]+)\)")


def _slug(url: str, content_type: str = "") -> str:
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    path = urlparse(url).path
    ext = Path(path).suffix.lower() or ""
    if ext not in RESOURCE_EXTS:
        ext = _guess_ext(url, content_type)
    return f"{digest}{ext}"



def _guess_ext(url: str, content_type: str = "") -> str:
    mime, _ = mimetypes.guess_type(url)
    mime = mime or content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "text/css": ".css",
        "application/javascript": ".js",
        "text/javascript": ".js",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
        "font/woff": ".woff",
        "font/woff2": ".woff2",
        "font/ttf": ".ttf",
        "font/otf": ".otf",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }
    return mapping.get(mime, "")



def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)



def _should_capture(response) -> bool:
    request_type = response.request.resource_type or ""
    if request_type not in CAPTURED_RESOURCE_TYPES:
        return False

    content_type = response.headers.get("content-type", "").lower()
    if any(
        token in content_type
        for token in (
            "text/css",
            "javascript",
            "image/",
            "font/",
            "video/",
            "audio/",
            "svg+xml",
        )
    ):
        return True

    ext = Path(urlparse(response.url).path).suffix.lower()
    return ext in RESOURCE_EXTS



def _rewrite_css_blob(css_text: str, base_url: str, save_resource) -> str:
    def replace_css_url(match: re.Match[str]) -> str:
        raw = match.group(1).strip().strip("'\"")
        if not raw or raw.startswith(("data:", "#", "blob:", "javascript:")):
            return match.group(0)
        absolute = urljoin(base_url, raw)
        local = save_resource(absolute)
        if not local:
            return match.group(0)
        return f"url('{local}')"

    return CSS_URL_PATTERN.sub(replace_css_url, css_text)



def download_site(
    url: str,
    zip_path: Path,
    options: dict,
    design_system_output_path: Path | None = None,
):
    started_at = time.monotonic()

    def ev(msg: str, level: str = "", progress: int | None = None) -> dict:
        data = {"type": "log", "msg": msg, "level": level}
        if progress is not None:
            data["progress"] = progress
        return data

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if design_system_output_path:
        design_system_output_path.parent.mkdir(parents=True, exist_ok=True)
        design_system_output_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{zip_path.stem}-", dir=zip_path.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        resources: dict[str, dict] = {}
        skipped_large = 0

        def save_response_body(abs_url: str, body: bytes, content_type: str) -> str:
            if abs_url in resources:
                return resources[abs_url]["local_path"]

            filename = _slug(abs_url, content_type)
            local_path = f"assets/{filename}"
            disk_path = temp_dir / local_path
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(body)
            resources[abs_url] = {
                "local_path": local_path,
                "disk_path": disk_path,
                "content_type": content_type,
            }
            return local_path

        def save_resource(abs_url: str) -> str | None:
            entry = resources.get(abs_url)
            if entry:
                return entry["local_path"]
            return None

        yield ev("Iniciando Playwright + Chromium...", progress=8)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )

            def handle_response(response):
                nonlocal skipped_large
                try:
                    if len(resources) >= MAX_CAPTURED_RESOURCES:
                        return
                    if response.status not in (200, 206):
                        return
                    if not _should_capture(response):
                        return

                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit() and int(content_length) > MAX_ASSET_BYTES:
                        skipped_large += 1
                        return

                    body = response.body()
                    if not body:
                        return
                    if len(body) > MAX_ASSET_BYTES:
                        skipped_large += 1
                        return

                    content_type = response.headers.get("content-type", "")
                    save_response_body(response.url, body, content_type)
                except Exception:
                    return

            page = context.new_page()
            page.on("response", handle_response)

            yield ev("Navegando para a URL alvo...", progress=22)
            page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)

            yield ev("Aguardando renderização JavaScript...", progress=35)
            if options.get("js", True):
                page.wait_for_timeout(WAIT_AFTER_LOAD_MS)

            if options.get("lazy", True):
                yield ev("Rolando página para capturar lazy loading...", progress=45)
                height = page.evaluate("document.body.scrollHeight") or 0
                for step in range(SCROLL_STEPS):
                    page.evaluate(
                        f"window.scrollTo(0, {int(height * (step + 1) / max(1, SCROLL_STEPS))})"
                    )
                    time.sleep(SCROLL_DELAY)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(POST_SCROLL_WAIT_MS)

            html_content = page.content()
            context.close()
            browser.close()

        yield ev(f"Capturados {len(resources)} recursos de rede...", progress=55)
        if skipped_large:
            yield ev(
                f"{skipped_large} recursos grandes foram ignorados para evitar estouro de memória.",
                level="",
            )

        yield ev("Processando HTML com BeautifulSoup...", progress=65)
        soup = BeautifulSoup(html_content, "html.parser")

        yield ev("Reescrevendo URLs para caminhos locais...", progress=72)
        for tag in soup.find_all(["link", "script", "img", "source", "video", "audio", "track"]):
            for attr in ("href", "src", "data-src", "poster"):
                value = tag.get(attr)
                if not value or str(value).startswith(("data:", "#", "blob:", "javascript:")):
                    continue
                absolute = urljoin(url, value)
                local = save_resource(absolute)
                if local:
                    tag[attr] = local

            srcset = tag.get("srcset")
            if srcset:
                new_parts = []
                for part in srcset.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    pieces = part.split()
                    src = pieces[0]
                    descriptor = " ".join(pieces[1:])
                    absolute = urljoin(url, src)
                    local = save_resource(absolute) or src
                    new_parts.append(f"{local} {descriptor}".strip())
                tag["srcset"] = ", ".join(new_parts)

        for node in soup.find_all(style=True):
            node["style"] = _rewrite_css_blob(str(node["style"]), url, save_resource)

        for style_tag in soup.find_all("style"):
            if style_tag.string:
                style_tag.string = _rewrite_css_blob(style_tag.string, url, save_resource)

        css_entries = [
            (abs_url, entry)
            for abs_url, entry in resources.items()
            if entry["local_path"].endswith(".css") or "text/css" in entry["content_type"].lower()
        ]
        for abs_url, entry in css_entries:
            try:
                css_text = entry["disk_path"].read_text(encoding="utf-8", errors="ignore")
                rewritten = _rewrite_css_blob(css_text, abs_url, save_resource)
                if rewritten != css_text:
                    entry["disk_path"].write_text(rewritten, encoding="utf-8")
            except Exception:
                continue

        if options.get("clean", True):
            yield ev("Removendo scripts de hydration e smooth scroll...", progress=80)
            removed = 0
            for tag in soup.find_all("script"):
                src = tag.get("src", "")
                content = tag.string or ""
                if _matches(src, FRAMEWORK_PATTERNS + SCROLL_LIB_PATTERNS):
                    tag.decompose()
                    removed += 1
                elif _matches(content, SCROLL_LIB_PATTERNS):
                    tag.decompose()
                    removed += 1
            if removed:
                yield ev(f"Removidos {removed} scripts incompatíveis com modo offline.")

        yield ev("Injetando correções de scroll offline...", progress=86)
        fix_css = soup.new_tag("style")
        fix_css.string = (
            "html, body { overflow-x: hidden !important; }"
            "* { scroll-behavior: auto !important; }"
        )
        if soup.head:
            soup.head.append(fix_css)
        elif soup.html:
            head = soup.new_tag("head")
            head.append(fix_css)
            soup.html.insert(0, head)

        yield ev("Analisando HTML para gerar design system...", progress=88)
        html_str = str(soup)
        ds_html = None
        try:
            from design_system_generator import generate_design_system

            ds_html = generate_design_system(html_str)
            if ds_html:
                if design_system_output_path:
                    design_system_output_path.write_text(ds_html, encoding="utf-8")
                yield ev(
                    "Design system gerado — incluindo no ZIP e em rota dedicada.",
                    level="accent",
                    progress=93,
                )
            else:
                yield ev("Design system indisponível — seguindo sem ele.")
        except Exception as exc:
            yield ev(f"Aviso: design system ignorado ({exc})")

        total_files = len(resources) + 1 + (1 if ds_html else 0)
        yield ev(f"Empacotando {total_files} arquivos em ZIP...", progress=95)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.html", html_str)
            if ds_html:
                zf.writestr("design-system.html", ds_html)
            for entry in resources.values():
                if entry["disk_path"].exists():
                    zf.write(entry["disk_path"], arcname=entry["local_path"])

        size_kb = zip_path.stat().st_size // 1024
        ds_note = " + design-system.html" if ds_html else ""
        yield ev(
            f"Concluído. ZIP com index.html{ds_note} + {len(resources)} assets ({size_kb} KB).",
            level="success",
            progress=100,
        )
        yield {
            "type": "summary",
            "files": total_files,
            "assets": len(resources),
            "design_system": bool(ds_html),
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        }
