from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SCROLL_DELAY = 0.35
SCROLL_STEPS = 7
WAIT_AFTER_LOAD_MS = 2_000
PAGE_TIMEOUT_MS = 45_000
POST_SCROLL_WAIT_MS = 900
REQUEST_TIMEOUT_SECONDS = 25

MAX_ASSET_BYTES = max(256_000, int(os.getenv("MAX_ASSET_BYTES", str(25 * 1024 * 1024))))
MAX_CAPTURED_RESOURCES = max(50, int(os.getenv("MAX_CAPTURED_RESOURCES", "900")))
MAX_CRAWL_PAGES = max(1, int(os.getenv("MAX_CRAWL_PAGES", "14")))
MAX_CRAWL_DEPTH = max(0, int(os.getenv("MAX_CRAWL_DEPTH", "2")))
MAX_CSS_IMPORT_DEPTH = max(1, int(os.getenv("MAX_CSS_IMPORT_DEPTH", "4")))

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
    ".mjs",
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
    ".json",
    ".txt",
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
CSS_IMPORT_PATTERN = re.compile(r"@import\s+(?:url\()?['\"]?([^'\")\s;]+)['\"]?\)?[^;]*;", re.IGNORECASE)
COLOR_PATTERN = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^\)]+\)|hsla?\([^\)]+\)|oklch\([^\)]+\)|color\([^\)]+\)"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _guess_ext(url: str, content_type: str = "") -> str:
    mime, _ = mimetypes.guess_type(url)
    mime = mime or content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "text/css": ".css",
        "application/javascript": ".js",
        "text/javascript": ".js",
        "application/json": ".json",
        "text/plain": ".txt",
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



def _slug(url: str, content_type: str = "") -> str:
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    path = urlparse(url).path
    ext = Path(path).suffix.lower() or ""
    if ext not in RESOURCE_EXTS:
        ext = _guess_ext(url, content_type)
    return f"{digest}{ext}"



def _clean_url(url: str) -> str:
    base, _ = urldefrag(url)
    return base.strip()



def _origin(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), parsed.netloc.lower()



def _same_origin(a: str, b: str) -> bool:
    return _origin(a) == _origin(b)



def _is_probable_page(url: str) -> bool:
    path = urlparse(url).path or "/"
    suffix = Path(path).suffix.lower()
    return suffix in {"", ".html", ".htm"}



def _normalize_page_url(url: str) -> str:
    parsed = urlparse(_clean_url(url))
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}{query}"



def _page_local_path(page_url: str, root_url: str) -> str:
    parsed = urlparse(page_url)
    path = parsed.path or "/"
    if page_url == _normalize_page_url(root_url) or path == "/":
        return "index.html"

    clean = path.lstrip("/")
    if not clean:
        return "index.html"

    if clean.endswith("/"):
        clean = clean.rstrip("/")

    suffix = Path(clean).suffix.lower()
    if suffix in {".html", ".htm"}:
        return clean
    return f"{clean}/index.html"



def _relative_to(from_file: str, target: str) -> str:
    return os.path.relpath(target, start=str(Path(from_file).parent)).replace("\\", "/")



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
            "application/json",
        )
    ):
        return True

    ext = Path(urlparse(response.url).path).suffix.lower()
    return ext in RESOURCE_EXTS



def _discover_links(page_url: str, html: str, root_url: str) -> list[str]:
    root_scheme, root_netloc = _origin(root_url)
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        raw = (tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = _normalize_page_url(urljoin(page_url, raw))
        parsed = urlparse(absolute)
        if (parsed.scheme.lower(), parsed.netloc.lower()) != (root_scheme, root_netloc):
            continue
        if not _is_probable_page(absolute):
            continue
        if absolute not in seen:
            seen.add(absolute)
            found.append(absolute)
    return found



def _fetch_url_bytes(abs_url: str) -> tuple[bytes | None, str]:
    req = urllib.request.Request(
        abs_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(MAX_ASSET_BYTES + 1)
            if len(body) > MAX_ASSET_BYTES:
                return None, content_type
            return body, content_type
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, ""


# ---------------------------------------------------------------------------
# Rewriting helpers
# ---------------------------------------------------------------------------

def _rewrite_css_blob(
    css_text: str,
    base_url: str,
    from_local_path: str,
    ensure_resource,
    unresolved_urls: set[str],
) -> str:
    def replace_import(match: re.Match[str]) -> str:
        raw = match.group(1).strip().strip("'\"")
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            return match.group(0)
        absolute = _clean_url(urljoin(base_url, raw))
        local = ensure_resource(absolute)
        if not local:
            unresolved_urls.add(absolute)
            return match.group(0)
        rel = _relative_to(from_local_path, local)
        return match.group(0).replace(raw, rel)

    def replace_css_url(match: re.Match[str]) -> str:
        raw = match.group(1).strip().strip("'\"")
        if not raw or raw.startswith(("data:", "#", "blob:", "javascript:")):
            return match.group(0)
        absolute = _clean_url(urljoin(base_url, raw))
        local = ensure_resource(absolute)
        if not local:
            unresolved_urls.add(absolute)
            return match.group(0)
        rel = _relative_to(from_local_path, local)
        return f"url('{rel}')"

    css_text = CSS_IMPORT_PATTERN.sub(replace_import, css_text)
    css_text = CSS_URL_PATTERN.sub(replace_css_url, css_text)
    return css_text



def _rewrite_document(
    page_url: str,
    html: str,
    current_local_path: str,
    root_url: str,
    page_local_map: dict[str, str],
    resources: dict[str, dict],
    ensure_resource,
    unresolved_urls: set[str],
    clean_scripts: bool,
) -> str:
    soup = BeautifulSoup(html, "html.parser")

    def local_for_resource(raw_url: str) -> str | None:
        absolute = _clean_url(urljoin(page_url, raw_url))
        if absolute in page_local_map:
            return _relative_to(current_local_path, page_local_map[absolute])
        entry = resources.get(absolute)
        if entry:
            return _relative_to(current_local_path, entry["local_path"])
        ensured = ensure_resource(absolute)
        if ensured:
            return _relative_to(current_local_path, ensured)
        unresolved_urls.add(absolute)
        return None

    for tag in soup.find_all(["a", "link", "script", "img", "source", "video", "audio", "track"]):
        for attr in ("href", "src", "data-src", "poster"):
            value = tag.get(attr)
            if not value or str(value).startswith(("data:", "#", "blob:", "javascript:", "mailto:", "tel:")):
                continue
            rewritten = local_for_resource(value)
            if rewritten:
                tag[attr] = rewritten

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
                rewritten = local_for_resource(src)
                if rewritten:
                    new_parts.append(f"{rewritten} {descriptor}".strip())
                else:
                    new_parts.append(part)
            tag["srcset"] = ", ".join(new_parts)

    for node in soup.find_all(style=True):
        node["style"] = _rewrite_css_blob(
            str(node["style"]),
            page_url,
            current_local_path,
            ensure_resource,
            unresolved_urls,
        )

    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string = _rewrite_css_blob(
                style_tag.string,
                page_url,
                current_local_path,
                ensure_resource,
                unresolved_urls,
            )

    if clean_scripts:
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
        if removed and soup.body:
            marker = soup.new_tag("meta")
            marker.attrs["name"] = "capturar-removed-scripts"
            marker.attrs["content"] = str(removed)
            soup.body.insert(0, marker)

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

    return str(soup)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def download_site(
    url: str,
    zip_path: Path,
    options: dict,
    design_system_output_path: Path | None = None,
):
    started_at = time.monotonic()
    root_url = _normalize_page_url(url)

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
        unresolved_urls: set[str] = set()
        skipped_large = 0
        fetched_out_of_band = 0

        def save_bytes(abs_url: str, body: bytes, content_type: str) -> str | None:
            if not body or len(body) > MAX_ASSET_BYTES:
                return None
            abs_url = _clean_url(abs_url)
            if abs_url in resources:
                return resources[abs_url]["local_path"]
            if len(resources) >= MAX_CAPTURED_RESOURCES:
                return None
            filename = _slug(abs_url, content_type)
            local_path = f"assets/{filename}"
            disk_path = temp_dir / local_path
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(body)
            resources[abs_url] = {
                "local_path": local_path,
                "disk_path": disk_path,
                "content_type": content_type,
                "size": len(body),
                "source": "network",
            }
            return local_path

        def ensure_resource(abs_url: str, import_depth: int = 0) -> str | None:
            nonlocal fetched_out_of_band
            abs_url = _clean_url(abs_url)
            if not abs_url:
                return None
            entry = resources.get(abs_url)
            if entry:
                return entry["local_path"]
            if len(resources) >= MAX_CAPTURED_RESOURCES:
                return None
            body, content_type = _fetch_url_bytes(abs_url)
            if not body:
                return None
            local_path = save_bytes(abs_url, body, content_type)
            if not local_path:
                return None
            fetched_out_of_band += 1
            if local_path.endswith(".css") and import_depth < MAX_CSS_IMPORT_DEPTH:
                try:
                    css_text = (temp_dir / local_path).read_text(encoding="utf-8", errors="ignore")
                    rewritten = _rewrite_css_blob(
                        css_text,
                        abs_url,
                        local_path,
                        lambda u: ensure_resource(u, import_depth + 1),
                        unresolved_urls,
                    )
                    (temp_dir / local_path).write_text(rewritten, encoding="utf-8")
                except Exception:
                    pass
            return local_path

        yield ev(
            f"Iniciando captura same-origin: até {MAX_CRAWL_PAGES} páginas e profundidade {MAX_CRAWL_DEPTH}.",
            progress=6,
        )

        pages_raw: dict[str, str] = {}
        crawl_order: list[str] = []
        seen_pages: set[str] = {root_url}
        queue: deque[tuple[str, int]] = deque([(root_url, 0)])

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=USER_AGENT,
            )
            page = context.new_page()

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
                    save_bytes(response.url, body, content_type)
                except Exception:
                    return

            page.on("response", handle_response)

            while queue and len(crawl_order) < MAX_CRAWL_PAGES:
                current_url, depth = queue.popleft()
                yield ev(
                    f"Capturando página {len(crawl_order) + 1}/{MAX_CRAWL_PAGES}: {urlparse(current_url).path or '/'}",
                    progress=min(12 + len(crawl_order) * 6, 58),
                )
                try:
                    page.goto(current_url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
                    if options.get("js", True):
                        page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
                    if options.get("lazy", True):
                        height = page.evaluate("document.body.scrollHeight") or 0
                        for step in range(SCROLL_STEPS):
                            page.evaluate(
                                f"window.scrollTo(0, {int(height * (step + 1) / max(1, SCROLL_STEPS))})"
                            )
                            time.sleep(SCROLL_DELAY)
                        page.evaluate("window.scrollTo(0, 0)")
                        page.wait_for_timeout(POST_SCROLL_WAIT_MS)
                    html_content = page.content()
                except Exception as exc:
                    yield ev(f"Aviso: falha ao capturar {current_url} ({exc})", level="error")
                    continue

                pages_raw[current_url] = html_content
                crawl_order.append(current_url)

                if depth < MAX_CRAWL_DEPTH and len(seen_pages) < MAX_CRAWL_PAGES:
                    for discovered in _discover_links(current_url, html_content, root_url):
                        if discovered not in seen_pages and len(seen_pages) < MAX_CRAWL_PAGES:
                            seen_pages.add(discovered)
                            queue.append((discovered, depth + 1))

            context.close()
            browser.close()

        yield ev(
            f"Cobertura inicial concluída: {len(crawl_order)} páginas e {len(resources)} recursos observados.",
            progress=64,
        )
        if skipped_large:
            yield ev(
                f"{skipped_large} recursos grandes foram ignorados para evitar estouro de memória.",
            )
        if fetched_out_of_band:
            yield ev(
                f"{fetched_out_of_band} recursos adicionais foram baixados fora do fluxo do navegador para fechar dependências.",
            )

        page_local_map = {page_url: _page_local_path(page_url, root_url) for page_url in crawl_order}

        yield ev("Fechando dependências de CSS e fontes...", progress=72)
        css_urls = [
            abs_url
            for abs_url, entry in list(resources.items())
            if entry["local_path"].endswith(".css") or "text/css" in entry["content_type"].lower()
        ]
        for css_url in css_urls:
            ensure_resource(css_url)

        yield ev("Reescrevendo páginas, rotas internas e assets locais...", progress=82)
        processed_pages: dict[str, dict] = {}
        for page_url in crawl_order:
            local_path = page_local_map[page_url]
            rewritten_html = _rewrite_document(
                page_url,
                pages_raw[page_url],
                local_path,
                root_url,
                page_local_map,
                resources,
                ensure_resource,
                unresolved_urls,
                clean_scripts=options.get("clean", True),
            )
            title_match = re.search(r"<title>(.*?)</title>", rewritten_html, re.IGNORECASE | re.DOTALL)
            processed_pages[page_url] = {
                "local_path": local_path,
                "html": rewritten_html,
                "title": title_match.group(1).strip() if title_match else "",
            }

        yield ev("Consolidando manifesto e design system do site inteiro...", progress=90)
        manifest = {
            "root_url": root_url,
            "captured_at": int(time.time()),
            "page_count": len(processed_pages),
            "asset_count": len(resources),
            "pages": [
                {
                    "url": page_url,
                    "local_path": data["local_path"],
                    "title": data["title"],
                }
                for page_url, data in processed_pages.items()
            ],
            "assets": [
                {
                    "url": abs_url,
                    "local_path": entry["local_path"],
                    "content_type": entry["content_type"],
                    "size": entry["size"],
                }
                for abs_url, entry in sorted(resources.items())
            ],
            "unresolved_urls": sorted(unresolved_urls),
            "options": options,
        }

        ds_html = None
        try:
            from design_system_generator import generate_design_system

            ds_html = generate_design_system(
                {
                    "root_url": root_url,
                    "pages": processed_pages,
                    "resources": {
                        url_key: {
                            "local_path": entry["local_path"],
                            "content_type": entry["content_type"],
                            "size": entry["size"],
                        }
                        for url_key, entry in resources.items()
                    },
                    "asset_root": str(temp_dir / "assets"),
                    "manifest": manifest,
                }
            )
            if ds_html and design_system_output_path:
                design_system_output_path.write_text(ds_html, encoding="utf-8")
            if ds_html:
                yield ev("Design system gerado a partir das páginas e CSS reais capturados.", level="accent", progress=94)
        except Exception as exc:
            yield ev(f"Aviso: design system ignorado ({exc})")

        total_files = len(resources) + len(processed_pages) + 1 + (1 if ds_html else 0)
        yield ev(f"Empacotando site local com {len(processed_pages)} páginas...", progress=96)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for _, data in processed_pages.items():
                zf.writestr(data["local_path"], data["html"])
            zf.writestr("capture-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if ds_html:
                zf.writestr("design-system.html", ds_html)
            for entry in resources.values():
                if entry["disk_path"].exists():
                    zf.write(entry["disk_path"], arcname=entry["local_path"])

        size_kb = zip_path.stat().st_size // 1024
        unresolved_note = f" · {len(unresolved_urls)} referências remotas ainda sem fechamento" if unresolved_urls else ""
        yield ev(
            (
                f"Concluído. ZIP com {len(processed_pages)} páginas locais, "
                f"{len(resources)} assets e manifesto ({size_kb} KB){unresolved_note}."
            ),
            level="success",
            progress=100,
        )
        yield {
            "type": "summary",
            "files": total_files,
            "assets": len(resources),
            "pages": len(processed_pages),
            "design_system": bool(ds_html),
            "unresolved": len(unresolved_urls),
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        }
