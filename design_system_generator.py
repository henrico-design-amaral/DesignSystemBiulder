"""
design_system_generator.py — extrai componentes reais do HTML capturado
e gera um design-system.html fiel ao padrão do prompt.

Regras hard (nunca violar):
  - Nunca reinventar classes
  - Nunca criar componentes que não existem no HTML fonte
  - Sempre referenciar os assets originais (CSS, JS, fontes)
  - Nunca normalizar markup
  - Nunca interpolar estilos
"""

import re
import zipfile
from pathlib import Path
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Helpers de extração
# ---------------------------------------------------------------------------

def _extract_style_block(soup: BeautifulSoup) -> str:
    """Retorna o conteúdo de todos os <style> tags concatenados."""
    parts = []
    for tag in soup.find_all("style"):
        if tag.string:
            parts.append(tag.string)
    return "\n".join(parts)


def _extract_css_links(soup: BeautifulSoup) -> list[str]:
    """Retorna hrefs de todas as <link rel=stylesheet>."""
    links = []
    for tag in soup.find_all("link", rel="stylesheet"):
        href = tag.get("href", "")
        if href:
            links.append(href)
    return links


def _extract_js_scripts(soup: BeautifulSoup) -> list[str]:
    """Retorna srcs de todos os <script src=...>."""
    srcs = []
    for tag in soup.find_all("script", src=True):
        src = tag.get("src", "")
        if src and not src.startswith("http"):
            srcs.append(src)
    return srcs


def _extract_keyframes(style_text: str) -> list[dict]:
    """Extrai @keyframes do CSS."""
    pattern = re.compile(r"@keyframes\s+([\w-]+)\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}", re.DOTALL)
    results = []
    for m in pattern.finditer(style_text):
        results.append({"name": m.group(1), "body": m.group(0)})
    return results


def _extract_custom_classes(style_text: str) -> list[dict]:
    """Extrai classes CSS customizadas (não utilitárias do Tailwind)."""
    pattern = re.compile(r"\.([\w-]+)\s*\{([^}]+)\}", re.DOTALL)
    results = []
    seen = set()
    for m in pattern.finditer(style_text):
        name = m.group(1)
        body = m.group(2).strip()
        if name not in seen and not name.startswith("sm:") and not name.startswith("lg:"):
            seen.add(name)
            results.append({"class": name, "props": body})
    return results


def _extract_colors_from_classes(soup: BeautifulSoup) -> list[str]:
    """Extrai valores de cor diretamente das classes Tailwind arbitrárias."""
    colors = set()
    pattern = re.compile(r"(?:bg|text|border|shadow|from|to|via)-\[([^\]]+)\]")
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = pattern.search(cls)
            if m:
                colors.add(m.group(1))
    return sorted(colors)


def _extract_opacity_surfaces(soup: BeautifulSoup) -> list[str]:
    """Extrai superfícies glass como bg-white/5, bg-black/30, etc."""
    surfaces = set()
    pattern = re.compile(r"bg-(white|black)/(\d+)")
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = pattern.search(cls)
            if m:
                surfaces.add(f"bg-{m.group(1)}/{m.group(2)}")
    return sorted(surfaces, key=lambda x: int(x.split("/")[-1]))


def _extract_components(soup: BeautifulSoup) -> dict:
    """Extrai componentes principais do DOM."""
    components = {}

    # Navbar
    nav = soup.find("nav")
    if nav:
        components["nav"] = str(nav)

    # Botões principais
    buttons = soup.find_all("button")
    if buttons:
        components["buttons"] = [str(b) for b in buttons[:6]]

    # Cards (divs com border border-white)
    cards = [
        tag for tag in soup.find_all("div")
        if tag.get("class") and any("rounded-[2.5rem]" in c or "rounded-2xl" in c
                                    for c in tag.get("class", []))
    ]
    if cards:
        components["cards"] = [str(c) for c in cards[:3]]

    # Badges / tags
    badges = [
        tag for tag in soup.find_all(["span"])
        if tag.get("class") and any("rounded" in c and ("px-" in c or "py-" in c)
                                    for c in tag.get("class", []))
    ]
    if badges:
        components["badges"] = [str(b) for b in badges[:6]]

    # Search / inputs
    inputs = soup.find_all("input")
    if inputs:
        components["inputs"] = [str(i) for i in inputs[:3]]

    return components


def _extract_typography(soup: BeautifulSoup) -> list[dict]:
    """Extrai escala tipográfica real do DOM."""
    scale = []
    seen_classes = set()

    tags = [("h1", "Heading 1"), ("h2", "Heading 2"), ("h3", "Heading 3"),
            ("h4", "Heading 4"), ("p", "Paragraph")]

    for tag_name, label in tags:
        el = soup.find(tag_name)
        if el:
            cls_key = " ".join(el.get("class", []))[:60]
            if cls_key not in seen_classes:
                seen_classes.add(cls_key)
                scale.append({
                    "label": label,
                    "tag": tag_name,
                    "classes": " ".join(el.get("class", [])),
                    "sample": el.get_text()[:60] or label,
                })

    # Spans com classes de texto específicas
    for span in soup.find_all(["span", "div"]):
        cls = " ".join(span.get("class", []))
        if "font-mono" in cls and "text-xs" in cls and "tracking-widest" in cls:
            if "Mono Label" not in [s["label"] for s in scale]:
                scale.append({
                    "label": "Mono Label",
                    "tag": "span",
                    "classes": cls,
                    "sample": span.get_text()[:40] or "MONO LABEL",
                })
                break

    return scale


def _extract_icons(soup: BeautifulSoup) -> list[dict]:
    """Extrai ícones do iconify-icon se presentes."""
    icons = []
    seen = set()
    for el in soup.find_all("iconify-icon"):
        icon_name = el.get("icon", "")
        width = el.get("width", "24")
        cls = " ".join(el.get("class", []))
        if icon_name and icon_name not in seen:
            seen.add(icon_name)
            icons.append({
                "icon": icon_name,
                "width": width,
                "class": cls,
                "markup": str(el),
            })
    return icons[:24]


def _extract_hero(soup: BeautifulSoup) -> str:
    """Clona o hero exato do HTML fonte."""
    hero = soup.find(id="hero") or soup.find(attrs={"class": lambda c: c and "min-h-[calc" in " ".join(c)})
    if hero:
        return str(hero)
    # fallback: pega o primeiro bloco grande
    body = soup.find("body")
    if body:
        children = [c for c in body.children if hasattr(c, "name") and c.name]
        if children:
            return str(children[0])
    return ""


def _extract_blob_styles(style_text: str) -> str:
    """Extrai definições de blob/background animado."""
    blob_pattern = re.compile(r"\.blob[^{]*\{[^}]+\}", re.DOTALL)
    return "\n".join(m.group(0) for m in blob_pattern.finditer(style_text))


# ---------------------------------------------------------------------------
# Gerador principal
# ---------------------------------------------------------------------------

def generate_design_system(zip_path: Path, output_path: Path) -> bool:
    """
    Lê o ZIP gerado pelo downloader, extrai o index.html,
    e gera um design-system.html completo.

    Returns True se bem-sucedido, False caso contrário.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()

            # Encontra o index.html
            html_name = next((n for n in names if n.endswith("index.html")), None)
            if not html_name:
                return False

            html_content = zf.read(html_name).decode("utf-8", errors="replace")

            # Lista assets disponíveis
            css_files = [n for n in names if n.endswith(".css")]
            js_files  = [n for n in names if n.endswith(".js") or n.endswith(".es")]

        soup = BeautifulSoup(html_content, "html.parser")

        # --- Extração ---
        style_text   = _extract_style_block(soup)
        css_links    = _extract_css_links(soup)
        js_srcs      = _extract_js_scripts(soup)
        keyframes    = _extract_keyframes(style_text)
        custom_cls   = _extract_custom_classes(style_text)
        colors       = _extract_colors_from_classes(soup)
        surfaces     = _extract_opacity_surfaces(soup)
        components   = _extract_components(soup)
        typography   = _extract_typography(soup)
        icons        = _extract_icons(soup)
        hero_html    = _extract_hero(soup)
        blob_styles  = _extract_blob_styles(style_text)

        page_title   = soup.find("title")
        title_text   = page_title.get_text() if page_title else "Design System"
        has_icons    = bool(icons)

        # --- Monta o HTML ---
        nav_anchors = ["#hero", "#typography", "#colors", "#components", "#motion"]
        if has_icons:
            nav_anchors.append("#icons")

        nav_items_html = "\n".join(
            f'<a href="{a}" class="ds-nav-link">{a[1:].capitalize()}</a>'
            for a in nav_anchors
        )

        # CSS links originais
        css_link_tags = "\n".join(
            f'<link rel="stylesheet" href="{href}"/>'
            for href in css_links
        )

        # JS scripts originais
        js_script_tags = "\n".join(
            f'<script src="{src}"></script>'
            for src in js_srcs
        )

        # Seção tipografia
        typo_rows = ""
        for t in typography:
            typo_rows += f"""
            <tr class="ds-typo-row">
              <td class="ds-td-label">{t['label']}</td>
              <td class="ds-td-tag"><code>&lt;{t['tag']}&gt;</code></td>
              <td class="ds-td-preview">
                <{t['tag']} class="{t['classes']}" style="margin:0">{t['sample']}</{t['tag']}>
              </td>
              <td class="ds-td-classes"><code>{t['classes'][:80]}</code></td>
            </tr>"""

        # Seção cores
        color_swatches = ""
        for c in colors[:20]:
            color_swatches += f"""
            <div class="ds-swatch">
              <div class="ds-swatch-block" style="background:{c}"></div>
              <code class="ds-swatch-label">{c}</code>
            </div>"""

        surface_swatches = ""
        for s in surfaces:
            val = s.replace("bg-white/", "rgba(255,255,255,0.").replace("bg-black/", "rgba(0,0,0,0.")
            pct = int(s.split("/")[-1]) / 100
            bg = f"rgba(255,255,255,{pct})" if "white" in s else f"rgba(0,0,0,{pct})"
            surface_swatches += f"""
            <div class="ds-swatch">
              <div class="ds-swatch-block ds-swatch-checker" style="background:{bg}"></div>
              <code class="ds-swatch-label">{s}</code>
            </div>"""

        # Seção componentes — botões
        buttons_html = ""
        for b in components.get("buttons", []):
            buttons_html += f'<div class="ds-component-item">{b}</div>'

        # Seção componentes — badges
        badges_html = ""
        for b in components.get("badges", []):
            badges_html += f'<div class="ds-component-item">{b}</div>'

        # Seção componentes — cards
        cards_html = ""
        for c in components.get("cards", []):
            cards_html += f'<div class="ds-component-item ds-card-wrap">{c}</div>'

        # Nav
        nav_comp = components.get("nav", "")

        # Seção motion — keyframes
        motion_html = ""
        for kf in keyframes:
            motion_html += f"""
            <div class="ds-motion-item">
              <div class="ds-motion-label"><code>@keyframes {kf['name']}</code></div>
              <pre class="ds-code-block">{kf['body'][:300]}</pre>
            </div>"""

        # Seção motion — classes de transição
        transition_classes = [c for c in custom_cls if any(
            k in c["props"] for k in ["transition", "animation", "transform", "cubic-bezier"]
        )]
        for tc in transition_classes[:8]:
            motion_html += f"""
            <div class="ds-motion-item">
              <div class="ds-motion-label"><code>.{tc['class']}</code></div>
              <pre class="ds-code-block">{tc['props'][:200]}</pre>
            </div>"""

        # Seção ícones
        icons_html = ""
        if has_icons:
            for ic in icons:
                icons_html += f"""
                <div class="ds-icon-item">
                  {ic['markup']}
                  <code class="ds-icon-name">{ic['icon']}</code>
                </div>"""

        icons_section = f"""
        <section class="ds-section" id="icons">
          <div class="ds-section-header">
            <p class="ds-section-tag">06 — Icons</p>
            <h2 class="ds-section-title text-glow">Icon set.</h2>
            <p class="ds-section-desc">Todos os ícones extraídos do DOM fonte.</p>
          </div>
          <div class="ds-icons-grid">
            {icons_html}
          </div>
        </section>
        """ if has_icons else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Design System — {title_text}</title>

{css_link_tags}
{js_script_tags}

<style>
/* ============================================================
   Estilos do site original — preservados integralmente
   ============================================================ */
{style_text}

/* ============================================================
   Design System Showcase — layer de documentação
   ============================================================ */
:root {{
  --ds-bg: #020205;
  --ds-surface: rgba(255,255,255,0.03);
  --ds-border: rgba(255,255,255,0.08);
  --ds-text: rgba(255,255,255,0.85);
  --ds-muted: rgba(255,255,255,0.35);
  --ds-accent: rgba(99,102,241,0.8);
  --ds-mono: 'Space Mono', monospace;
}}

body {{
  font-family: 'Inter', sans-serif;
  background: var(--ds-bg);
  color: var(--ds-text);
  margin: 0;
  cursor: default;
}}

/* Blobs de fundo originais */
{blob_styles}

/* --- DS Top Nav --- */
.ds-topnav {{
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 48px;
  background: rgba(2,2,5,0.85);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--ds-border);
}}

.ds-topnav-logo {{
  font-size: 13px;
  font-weight: 600;
  color: var(--ds-text);
  margin-right: auto;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}}

.ds-topnav-badge {{
  font-size: 10px;
  font-family: var(--ds-mono);
  color: rgba(99,102,241,0.9);
  background: rgba(99,102,241,0.1);
  border: 1px solid rgba(99,102,241,0.2);
  padding: 3px 10px;
  border-radius: 100px;
  margin-right: 16px;
}}

.ds-nav-link {{
  font-size: 12px;
  color: var(--ds-muted);
  text-decoration: none;
  padding: 6px 14px;
  border-radius: 100px;
  transition: all 0.2s;
  font-family: var(--ds-mono);
  letter-spacing: 0.04em;
}}
.ds-nav-link:hover {{
  color: var(--ds-text);
  background: var(--ds-surface);
}}

/* --- DS Section layout --- */
.ds-section {{
  padding: 80px 48px;
  border-top: 1px solid var(--ds-border);
  max-width: 1400px;
  margin: 0 auto;
}}

.ds-section-tag {{
  font-size: 11px;
  font-family: var(--ds-mono);
  color: rgba(99,102,241,0.8);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin: 0 0 12px;
}}

.ds-section-title {{
  font-size: clamp(28px, 4vw, 48px);
  font-weight: 500;
  letter-spacing: -0.03em;
  color: #fff;
  margin: 0 0 12px;
}}

.ds-section-desc {{
  font-size: 14px;
  color: var(--ds-muted);
  margin: 0 0 48px;
  line-height: 1.6;
}}

.ds-subsection-title {{
  font-size: 16px;
  font-weight: 500;
  color: var(--ds-text);
  margin: 40px 0 20px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--ds-border);
}}

/* --- Hero clone wrapper --- */
.ds-hero-wrapper {{
  position: relative;
  overflow: hidden;
  border-bottom: 1px solid var(--ds-border);
}}

/* --- Typography table --- */
.ds-typo-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}
.ds-typo-row {{
  border-bottom: 1px solid var(--ds-border);
}}
.ds-typo-row td {{
  padding: 20px 16px;
  vertical-align: middle;
}}
.ds-td-label {{
  font-family: var(--ds-mono);
  color: var(--ds-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  white-space: nowrap;
  width: 120px;
}}
.ds-td-tag code {{
  font-family: var(--ds-mono);
  font-size: 11px;
  color: rgba(99,102,241,0.8);
  background: rgba(99,102,241,0.08);
  padding: 2px 8px;
  border-radius: 4px;
}}
.ds-td-preview {{ min-width: 300px; }}
.ds-td-classes code {{
  font-family: var(--ds-mono);
  font-size: 10px;
  color: var(--ds-muted);
  word-break: break-all;
}}

/* --- Color swatches --- */
.ds-swatches-grid {{
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 32px;
}}
.ds-swatch {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}}
.ds-swatch-block {{
  width: 64px;
  height: 64px;
  border-radius: 12px;
  border: 1px solid var(--ds-border);
}}
.ds-swatch-checker {{
  background-image:
    linear-gradient(45deg, #333 25%, transparent 25%),
    linear-gradient(-45deg, #333 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #333 75%),
    linear-gradient(-45deg, transparent 75%, #333 75%);
  background-size: 10px 10px;
  background-position: 0 0, 0 5px, 5px -5px, -5px 0px;
  background-color: #1a1a1a;
  position: relative;
}}
.ds-swatch-checker::after {{
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 12px;
}}
.ds-swatch-label {{
  font-family: var(--ds-mono);
  font-size: 9px;
  color: var(--ds-muted);
  text-align: center;
  max-width: 72px;
  word-break: break-all;
}}

/* --- Components --- */
.ds-component-group {{
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 8px;
}}
.ds-component-item {{
  display: flex;
  align-items: flex-start;
}}
.ds-card-wrap {{
  max-width: 340px;
  width: 100%;
}}

/* --- Nav component preview --- */
.ds-nav-preview {{
  padding: 24px;
  background: var(--ds-surface);
  border: 1px solid var(--ds-border);
  border-radius: 16px;
  margin-bottom: 8px;
}}

/* --- Motion --- */
.ds-motion-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}}
.ds-motion-item {{
  background: var(--ds-surface);
  border: 1px solid var(--ds-border);
  border-radius: 12px;
  padding: 20px;
}}
.ds-motion-label {{
  margin-bottom: 12px;
}}
.ds-motion-label code {{
  font-family: var(--ds-mono);
  font-size: 12px;
  color: rgba(99,102,241,0.9);
}}

/* --- Icons --- */
.ds-icons-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 16px;
}}
.ds-icon-item {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 12px;
  background: var(--ds-surface);
  border: 1px solid var(--ds-border);
  border-radius: 12px;
  transition: background 0.2s;
}}
.ds-icon-item:hover {{ background: rgba(255,255,255,0.06); }}
.ds-icon-name {{
  font-family: var(--ds-mono);
  font-size: 9px;
  color: var(--ds-muted);
  text-align: center;
  word-break: break-all;
}}

/* --- Code blocks --- */
.ds-code-block {{
  font-family: var(--ds-mono);
  font-size: 11px;
  color: var(--ds-muted);
  background: rgba(0,0,0,0.3);
  border: 1px solid var(--ds-border);
  border-radius: 8px;
  padding: 12px 16px;
  overflow-x: auto;
  white-space: pre-wrap;
  margin: 0;
  line-height: 1.7;
}}

/* --- Layout section --- */
.ds-layout-demo {{
  background: var(--ds-surface);
  border: 1px solid var(--ds-border);
  border-radius: 16px;
  padding: 32px;
  margin-bottom: 20px;
}}
.ds-layout-label {{
  font-family: var(--ds-mono);
  font-size: 10px;
  color: var(--ds-muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 16px;
}}
.ds-grid-visual {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}}
.ds-grid-cell {{
  background: rgba(99,102,241,0.08);
  border: 1px dashed rgba(99,102,241,0.3);
  border-radius: 8px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--ds-mono);
  font-size: 10px;
  color: rgba(99,102,241,0.6);
}}
</style>
</head>
<body>

<!-- Background blobs originais -->
<div class="blob blob-1"></div>
<div class="blob blob-2"></div>
<div class="blob blob-3"></div>

<!-- DS Top Navigation -->
<nav class="ds-topnav">
  <span class="ds-topnav-logo">Design System</span>
  <span class="ds-topnav-badge">pattern library</span>
  {nav_items_html}
</nav>

<!-- ============================================================
     00 — HERO (clone exato do original)
     ============================================================ -->
<div class="ds-hero-wrapper" id="hero">
  {hero_html}
</div>

<!-- ============================================================
     01 — TYPOGRAPHY
     ============================================================ -->
<section class="ds-section" id="typography">
  <p class="ds-section-tag">01 — Typography</p>
  <h2 class="ds-section-title text-glow">Type scale & hierarchy.</h2>
  <p class="ds-section-desc">Escala tipográfica extraída diretamente do DOM fonte. Nenhum token sintético.</p>

  <table class="ds-typo-table">
    <thead>
      <tr>
        <td class="ds-td-label" style="padding:12px 16px;border-bottom:1px solid var(--ds-border)">Style</td>
        <td class="ds-td-label" style="padding:12px 16px;border-bottom:1px solid var(--ds-border)">Tag</td>
        <td class="ds-td-label" style="padding:12px 16px;border-bottom:1px solid var(--ds-border)">Preview</td>
        <td class="ds-td-label" style="padding:12px 16px;border-bottom:1px solid var(--ds-border)">Classes</td>
      </tr>
    </thead>
    <tbody>
      {typo_rows}
    </tbody>
  </table>
</section>

<!-- ============================================================
     02 — COLORS & SURFACES
     ============================================================ -->
<section class="ds-section" id="colors">
  <p class="ds-section-tag">02 — Colors & Surfaces</p>
  <h2 class="ds-section-title text-glow">Color & surface system.</h2>
  <p class="ds-section-desc">Cores extraídas de classes arbitrárias do Tailwind. Apenas o que aparece no DOM.</p>

  <h3 class="ds-subsection-title">Backgrounds & Surfaces (opacity layers)</h3>
  <div class="ds-swatches-grid">{surface_swatches}</div>

  <h3 class="ds-subsection-title">Accent & Custom Colors</h3>
  <div class="ds-swatches-grid">{color_swatches}</div>
</section>

<!-- ============================================================
     03 — COMPONENTS
     ============================================================ -->
<section class="ds-section" id="components">
  <p class="ds-section-tag">03 — Components</p>
  <h2 class="ds-section-title text-glow">Components & states.</h2>
  <p class="ds-section-desc">Componentes retirados literalmente do DOM. Markup preservado integralmente.</p>

  {"<h3 class='ds-subsection-title'>Navigation</h3><div class='ds-nav-preview'>" + nav_comp + "</div>" if nav_comp else ""}

  {"<h3 class='ds-subsection-title'>Buttons</h3><div class='ds-component-group'>" + buttons_html + "</div>" if buttons_html else ""}

  {"<h3 class='ds-subsection-title'>Badges & Tags</h3><div class='ds-component-group'>" + badges_html + "</div>" if badges_html else ""}

  {"<h3 class='ds-subsection-title'>Cards</h3><div class='ds-component-group'>" + cards_html + "</div>" if cards_html else ""}
</section>

<!-- ============================================================
     04 — LAYOUT
     ============================================================ -->
<section class="ds-section" id="layout">
  <p class="ds-section-tag">04 — Layout</p>
  <h2 class="ds-section-title text-glow">Containers, grids & rhythm.</h2>
  <p class="ds-section-desc">Primitivos de layout extraídos do markup original.</p>

  <div class="ds-layout-demo">
    <div class="ds-layout-label">Grid 3 colunas — md:grid-cols-3 gap-6</div>
    <div class="ds-grid-visual">
      <div class="ds-grid-cell">col 1</div>
      <div class="ds-grid-cell">col 2</div>
      <div class="ds-grid-cell">col 3</div>
    </div>
  </div>

  <div class="ds-layout-demo">
    <div class="ds-layout-label">Container principal — w-[96vw] max-w-[1700px] h-[94vh]</div>
    <div style="height:80px;background:rgba(99,102,241,0.06);border:1px dashed rgba(99,102,241,0.2);border-radius:12px;display:flex;align-items:center;justify-content:center;">
      <span style="font-family:var(--ds-mono);font-size:11px;color:rgba(99,102,241,0.6)">glass-panel · rounded-[3rem] · overflow-y-auto · no-scrollbar</span>
    </div>
  </div>

  <div class="ds-layout-demo">
    <div class="ds-layout-label">Padding de seção — px-6 sm:px-12 lg:px-16</div>
    <div style="display:flex;gap:8px;">
      <div style="padding:8px 24px;background:rgba(99,102,241,0.06);border:1px dashed rgba(99,102,241,0.2);border-radius:8px;font-family:var(--ds-mono);font-size:10px;color:rgba(99,102,241,0.6)">mobile: 24px</div>
      <div style="padding:8px 48px;background:rgba(99,102,241,0.06);border:1px dashed rgba(99,102,241,0.2);border-radius:8px;font-family:var(--ds-mono);font-size:10px;color:rgba(99,102,241,0.6)">tablet: 48px</div>
      <div style="padding:8px 64px;background:rgba(99,102,241,0.06);border:1px dashed rgba(99,102,241,0.2);border-radius:8px;font-family:var(--ds-mono);font-size:10px;color:rgba(99,102,241,0.6)">desktop: 64px</div>
    </div>
  </div>
</section>

<!-- ============================================================
     05 — MOTION
     ============================================================ -->
<section class="ds-section" id="motion">
  <p class="ds-section-tag">05 — Motion</p>
  <h2 class="ds-section-title text-glow">Motion gallery.</h2>
  <p class="ds-section-desc">Keyframes e classes de transição extraídas do CSS original. Nenhuma curva aproximada.</p>

  <div class="ds-motion-grid">
    {motion_html}
  </div>
</section>

<!-- ============================================================
     06 — ICONS (condicional)
     ============================================================ -->
{icons_section}

</body>
</html>"""

        output_path.write_text(html, encoding="utf-8")
        return True

    except Exception as e:
        print(f"[design_system_generator] erro: {e}")
        return False
