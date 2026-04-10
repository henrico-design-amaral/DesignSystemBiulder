"""
downloader.py — lógica de captura, processamento e empacotamento.

Fluxo:
  1. Playwright abre Chromium headless e renderiza o JS da página
  2. Captura todos os recursos de rede (CSS, JS, fontes, imagens)
  3. BeautifulSoup reescreve URLs para caminhos locais
  4. Limpeza de scripts que não funcionam offline
  5. Empacota tudo em ZIP e limpa os temporários
"""

import hashlib
import io
import mimetypes
import os
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SCROLL_DELAY = 0.4   # segundos entre scrolls
SCROLL_STEPS = 6     # quantas vezes rola a página
WAIT_AFTER_LOAD = 2  # segundos após networkidle

# Scripts de frameworks que não funcionam offline
FRAMEWORK_PATTERNS = [
    r"_next/static",
    r"__nuxt",
    r"gatsby",
    r"__remix",
    r"webpack",
]

# Bibliotecas de smooth scroll que causam bugs offline
SCROLL_LIB_PATTERNS = [
    r"lenis",
    r"locomotive",
    r"smooth-scroll",
    r"@studio-freight",
]

RESOURCE_EXTS = {
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".ico", ".mp4", ".webm",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(url: str) -> str:
    """Gera nome de arquivo único e seguro para uma URL."""
    digest = hashlib.md5(url.encode()).hexdigest()[:8]
    path = urlparse(url).path
    ext = Path(path).suffix or ""
    if ext not in RESOURCE_EXTS:
        ext = _guess_ext(url)
    return f"{digest}{ext}"


def _guess_ext(url: str) -> str:
    mime, _ = mimetypes.guess_type(url)
    if not mime:
        return ""
    mapping = {
        "text/css": ".css",
        "application/javascript": ".js",
        "text/javascript": ".js",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "font/woff": ".woff",
        "font/woff2": ".woff2",
    }
    return mapping.get(mime, "")


def _is_same_origin(base: str, target: str) -> bool:
    b = urlparse(base)
    t = urlparse(target)
    return b.netloc == t.netloc


def _matches(text: str, patterns: list) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def download_site(url: str, zip_path: Path, options: dict):
    """
    Generator que emite eventos SSE durante o processo e salva o ZIP ao final.
    Cada yield é um dict que será serializado para JSON no app.py.
    """

    def ev(msg: str, level: str = "", progress: int = None) -> dict:
        d = {"type": "log", "msg": msg, "level": level}
        if progress is not None:
            d["progress"] = progress
        return d

    resources: dict[str, bytes] = {}  # url -> conteúdo binário

    # ------------------------------------------------------------------
    # 1. Captura com Playwright
    # ------------------------------------------------------------------
    yield ev("Iniciando Playwright + Chromium...", progress=8)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # Intercepta todos os recursos de rede
        def handle_response(response):
            try:
                if response.status in (200, 206):
                    body = response.body()
                    if body:
                        resources[response.url] = body
            except Exception:
                pass

        page = context.new_page()
        page.on("response", handle_response)

        yield ev("Navegando para a URL alvo...", progress=22)
        page.goto(url, wait_until="networkidle", timeout=45_000)

        yield ev("Aguardando renderização JavaScript...", progress=35)
        if options.get("js", True):
            page.wait_for_timeout(WAIT_AFTER_LOAD * 1000)

        # Scroll para capturar lazy loading
        if options.get("lazy", True):
            yield ev("Rolando página para capturar lazy loading...", progress=45)
            height = page.evaluate("document.body.scrollHeight")
            for i in range(SCROLL_STEPS):
                page.evaluate(f"window.scrollTo(0, {int(height * (i + 1) / SCROLL_STEPS)})")
                time.sleep(SCROLL_DELAY)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(800)

        yield ev(f"Capturados {len(resources)} recursos de rede...", progress=55)

        html_content = page.content()
        browser.close()

    # ------------------------------------------------------------------
    # 2. Processar HTML
    # ------------------------------------------------------------------
    yield ev("Processando HTML com BeautifulSoup...", progress=65)

    soup = BeautifulSoup(html_content, "html.parser")
    asset_map: dict[str, str] = {}  # url_absoluta -> caminho_local

    def save_resource(abs_url: str) -> str | None:
        """Salva recurso nos assets e retorna o caminho local."""
        if abs_url in asset_map:
            return asset_map[abs_url]
        data = resources.get(abs_url)
        if not data:
            return None
        filename = _slug(abs_url)
        asset_map[abs_url] = f"assets/{filename}"
        return f"assets/{filename}"

    # Reescreve <link href>, <script src>, <img src>, <source srcset>, etc.
    yield ev("Reescrevendo URLs para caminhos locais...", progress=72)

    for tag in soup.find_all(["link", "script", "img", "source", "video", "audio", "track"]):
        for attr in ("href", "src", "data-src", "poster"):
            val = tag.get(attr)
            if not val or val.startswith("data:") or val.startswith("#"):
                continue
            abs_url = urljoin(url, val)
            local = save_resource(abs_url)
            if local:
                tag[attr] = local

        # srcset
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
                abs_url = urljoin(url, src)
                local = save_resource(abs_url) or src
                new_parts.append(f"{local} {descriptor}".strip())
            tag["srcset"] = ", ".join(new_parts)

    # CSS inline: url(...) references
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            def replace_css_url(m):
                raw = m.group(1).strip("'\"")
                if raw.startswith("data:") or raw.startswith("#"):
                    return m.group(0)
                abs_url = urljoin(url, raw)
                local = save_resource(abs_url)
                return f"url('{local}')" if local else m.group(0)
            style_tag.string = re.sub(r"url\(([^)]+)\)", replace_css_url, style_tag.string)

    # ------------------------------------------------------------------
    # 3. Limpeza de scripts offline
    # ------------------------------------------------------------------
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

    # Injeta CSS para corrigir scroll offline
    yield ev("Injetando correções de scroll offline...", progress=86)
    fix_css = soup.new_tag("style")
    fix_css.string = (
        "html, body { overflow-x: hidden !important; }"
        "* { scroll-behavior: auto !important; }"
    )
    if soup.head:
        soup.head.append(fix_css)

    # ------------------------------------------------------------------
    # 4. Empacotar em ZIP
    # ------------------------------------------------------------------
    yield ev(f"Empacotando {len(asset_map)} assets em ZIP...", progress=92)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # HTML principal
        zf.writestr("index.html", str(soup))

        # Assets
        for abs_url, local_path in asset_map.items():
            data = resources.get(abs_url)
            if data:
                zf.writestr(local_path, data)

    size_kb = zip_path.stat().st_size // 1024
    yield ev(
        f"ZIP gerado com {len(asset_map) + 1} arquivos ({size_kb} KB).",
        level="success",
        progress=97,
    )

    # ------------------------------------------------------------------
    # 5. Gerar design-system.html
    # ------------------------------------------------------------------
    yield ev("Extraindo design system do HTML capturado...", progress=98)
    try:
        from design_system_generator import generate_design_system
        ds_path = zip_path.with_name(zip_path.stem + "-design-system.html")
        ok = generate_design_system(zip_path, ds_path)
        if ok:
            yield ev(
                f"Design system gerado → {ds_path.name}",
                level="accent",
                progress=100,
            )
        else:
            yield ev("Design system não pôde ser gerado (HTML sem evidência suficiente).", level="")
    except Exception as e:
        yield ev(f"Aviso: design system ignorado ({e})", level="")
