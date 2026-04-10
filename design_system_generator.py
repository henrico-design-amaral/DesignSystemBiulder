"""
design_system_generator.py — v3 (merged prompt)

Merge das melhores partes dos dois prompts:
  - Estrutura limpa do v2 (0→hero, 1→typography, 2→colors, 3→components,
    4→layout, 5→motion, 6→icons, +condicionais scroll/3D)
  - HARD RULES completas do original (14 regras, nunca aproximar)
  - Formato '40px / 48px' do v2 para tipografia
  - Hierarquia tipográfica do v2 (H1, H2, H3, H4, Bold, Paragraph, Regular)
  - Checklist de validação do original
  - Priority order: DOM evidence > class frequency > animation bindings
  - Assets: sempre referencia CSS/JS originais, nunca inline
  - Scroll/3D condicionais (incluídos apenas se existirem no DOM)

HARD RULES (NON-NEGOTIABLE):
  1. Never redesign anything
  2. Never rename classes
  3. Never simplify DOM structure
  4. Never normalize markup
  5. Never replace semantic tags
  6. Never introduce synthetic wrappers
  7. Never introduce inferred tokens
  8. Never create missing components
  9. Never interpolate styles
  10. Never approximate animations
  11. Never approximate spacing
  12. Never approximate easing curves
  13. Always reuse original CSS/JS assets
  14. If something does not exist in HTML → do not include it
"""

import re
import zipfile
from pathlib import Path
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Mapa semântico de variáveis CSS
# ---------------------------------------------------------------------------
SEMANTIC_MAP = [
    (r"brand|primary|accent|highlight",  "Brand / Primary"),
    (r"secondary",                        "Secondary"),
    (r"plum|purple|violet|indigo",        "Brand Purple"),
    (r"gold|amber|yellow",               "Accent Gold"),
    (r"rose|pink|red|danger|error",      "Danger / Rose"),
    (r"green|success|emerald|teal",      "Success / Green"),
    (r"blue|sky|cyan|info",              "Info / Blue"),
    (r"ink|text|fg|foreground|copy",     "Text"),
    (r"muted|soft|dim|subtle|faint",     "Text Muted"),
    (r"bg|background|surface|cream|canvas|base", "Background"),
    (r"card|panel|layer|glass",          "Surface / Card"),
    (r"stone|neutral|gray|grey|slate",   "Neutral"),
    (r"border|divider|separator|stroke", "Border"),
    (r"white",                           "White"),
    (r"black",                           "Black"),
    (r"shadow|overlay|scrim",            "Overlay / Shadow"),
]

def _infer_label(var_name: str) -> str:
    name = var_name.strip("-").lower()
    for pattern, label in SEMANTIC_MAP:
        if re.search(pattern, name):
            return label
    return var_name.strip("-").replace("-", " ").title()

def _looks_like_color(value: str) -> bool:
    v = value.strip().lower()
    NON_COLORS = {"auto","none","inherit","initial","unset","normal","bold",
                  "italic","sans-serif","serif","monospace","transparent",
                  "solid","dashed","dotted","pointer","block","flex","grid"}
    return (
        v.startswith("#") or
        v.startswith("rgb") or
        v.startswith("hsl") or
        v.startswith("oklch") or
        v.startswith("color(") or
        (re.match(r"^[a-z]+$", v) and v not in NON_COLORS and len(v) > 2)
    )

# ---------------------------------------------------------------------------
# Extração de CSS custom properties — PRIORITY: DOM evidence
# ---------------------------------------------------------------------------
def _extract_css_tokens(style_text: str) -> list[dict]:
    """Extrai :root variables. Priority: DOM evidence."""
    tokens = []
    seen = set()
    root_blocks = re.findall(r":root\s*(?:\.[\w-]+\s*)?\{([^}]+)\}", style_text, re.DOTALL)
    for block in root_blocks:
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block):
            name = m.group(1).strip()
            value = m.group(2).strip()
            if name in seen:
                continue
            seen.add(name)
            tokens.append({
                "name": name,
                "value": value,
                "label": _infer_label(name),
                "is_color": _looks_like_color(value),
            })
    return tokens

def _resolve_var(value: str, tokens: list[dict]) -> str:
    """Resolve referências var(--x) para o valor real."""
    if "var(" not in value:
        return value
    ref = re.search(r"var\((--[\w-]+)\)", value)
    if ref:
        ref_name = ref.group(1)
        resolved = next((t["value"] for t in tokens if t["name"] == ref_name), value)
        return _resolve_var(resolved, tokens)  # resolve recursivo
    return value

# ---------------------------------------------------------------------------
# Tipografia — formato '40px / 48px' do v2
# Hierarquia: H1, H2, H3, H4, Bold L/M/S, Paragraph, Regular L/M/S
# ---------------------------------------------------------------------------

TYPO_SAMPLES = {
    "h1": "The Quick Brown Fox",
    "h2": "The Quick Brown Fox",
    "h3": "The Quick Brown Fox",
    "h4": "The Quick Brown Fox",
    "p":  "The quick brown fox jumps over the lazy dog.",
}
TYPO_LABELS = {
    "h1": "Heading 1", "h2": "Heading 2",
    "h3": "Heading 3", "h4": "Heading 4",
    "p":  "Paragraph",
}
TAILWIND_SIZES = {
    "text-xs":"12px/16px", "text-sm":"14px/20px", "text-base":"16px/24px",
    "text-lg":"18px/28px", "text-xl":"20px/28px", "text-2xl":"24px/32px",
    "text-3xl":"30px/36px","text-4xl":"36px/40px","text-5xl":"48px/1",
    "text-6xl":"60px/1","text-7xl":"72px/1","text-8xl":"96px/1",
    "text-9xl":"128px/1",
}

def _extract_font_size_from_css(tag: str, classes: str, style_text: str) -> str:
    """Extrai font-size/line-height real do CSS. Nunca aproxima."""
    # Tenta por seletor de tag
    m = re.search(
        rf"(?:^|\s){tag}\s*\{{[^}}]*font-size\s*:\s*([\d.]+(?:px|rem|em))[^}}]*line-height\s*:\s*([\d.]+(?:px|rem|em|))",
        style_text, re.DOTALL | re.MULTILINE
    )
    if m:
        return f"{m.group(1)} / {m.group(2)}"

    fs = re.search(rf"(?:^|\s){tag}\s*\{{[^}}]*font-size\s*:\s*([\d.]+(?:px|rem|em))", style_text, re.DOTALL)
    if fs:
        return f"{fs.group(1)} / —"

    # Tenta por classes Tailwind
    for cls, size in TAILWIND_SIZES.items():
        if cls in classes:
            return size

    defaults = {
        "h1": "48–72px / 1–1.1", "h2": "36–48px / 1.1",
        "h3": "24–30px / 1.2",   "h4": "18–22px / 1.3",
        "p":  "14–16px / 1.6",
    }
    return defaults.get(tag, "—")

def _extract_typography(soup: BeautifulSoup, style_text: str) -> list[dict]:
    scale = []
    seen = set()

    for tag_name in ["h1", "h2", "h3", "h4", "p"]:
        for el in soup.find_all(tag_name)[:5]:
            classes = " ".join(el.get("class", []))
            key = f"{tag_name}:{classes[:50]}"
            if key in seen:
                continue
            seen.add(key)
            scale.append({
                "label":     TYPO_LABELS[tag_name],
                "tag":       tag_name,
                "classes":   classes,
                "sample":    TYPO_SAMPLES[tag_name],
                "size_hint": _extract_font_size_from_css(tag_name, classes, style_text),
                "gradient":  "gradient" in classes.lower() or "bg-clip-text" in classes,
            })
            break

    # Bold variants — spans ou divs com font-weight explícito
    for el in soup.find_all(["span", "div", "strong"]):
        cls = " ".join(el.get("class", []))
        is_bold = (
            "font-bold" in cls or "font-semibold" in cls or
            "font-medium" in cls or "font-black" in cls or
            re.search(r"font-weight\s*:\s*(600|700|800|900)", style_text)
        )
        if is_bold and "Bold" not in [s["label"] for s in scale]:
            text = el.get_text()[:40].strip()
            if len(text) > 2:
                scale.append({
                    "label": "Bold",
                    "tag": el.name,
                    "classes": cls,
                    "sample": "Bold text sample",
                    "size_hint": _extract_font_size_from_css(el.name, cls, style_text),
                    "gradient": False,
                })
                break

    # Mono label
    for el in soup.find_all(["span", "div", "p", "code"]):
        cls = " ".join(el.get("class", []))
        if any(k in cls.lower() for k in ["mono", "font-mono"]):
            scale.append({
                "label": "Mono",
                "tag": el.name,
                "classes": cls,
                "sample": "SYSTEM · 01 · ACTIVE",
                "size_hint": "11–13px / 1.5",
                "gradient": False,
            })
            break

    return scale

# ---------------------------------------------------------------------------
# Cores e superfícies — apenas o que aparece no DOM
# ---------------------------------------------------------------------------
def _extract_css_tokens_from_classes(soup: BeautifulSoup) -> list[str]:
    """Extrai cores de classes Tailwind arbitrárias [#hex]."""
    colors = set()
    arb = re.compile(r"(?:bg|text|border|from|to|via|ring|shadow|fill|stroke)-\[([^\]]+)\]")
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = arb.search(cls)
            if m:
                val = m.group(1)
                if _looks_like_color(val):
                    colors.add(val)
    return sorted(colors)

def _extract_opacity_surfaces(soup: BeautifulSoup) -> list[str]:
    surfaces = set()
    p = re.compile(r"bg-(white|black)/([\d]+)")
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = p.search(cls)
            if m:
                surfaces.add(f"bg-{m.group(1)}/{m.group(2)}")
    return sorted(surfaces, key=lambda x: int(x.split("/")[-1]))

def _extract_gradient_classes(soup: BeautifulSoup) -> list[str]:
    seen = set()
    grads = []
    for tag in soup.find_all(class_=True):
        cls_str = " ".join(tag.get("class", []))
        if "gradient" in cls_str or ("from-" in cls_str and "to-" in cls_str):
            key = cls_str[:80]
            if key not in seen:
                seen.add(key)
                grads.append(cls_str)
    return grads[:8]

# ---------------------------------------------------------------------------
# Keyframes e transições — nunca aproxima easing
# ---------------------------------------------------------------------------
def _extract_keyframes(style_text: str) -> list[dict]:
    results = []
    pattern = re.compile(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*|\{[^}]*\})*)\}", re.DOTALL)
    for m in pattern.finditer(style_text):
        body = m.group(0)
        results.append({"name": m.group(1), "body": body[:400]})
    return results

def _extract_transition_classes(style_text: str) -> list[dict]:
    """Extrai classes com transition/animation EXATAS — nunca aproxima timing."""
    results = []
    seen = set()
    pattern = re.compile(r"\.([\w-]+)\s*\{([^}]+)\}", re.DOTALL)
    for m in pattern.finditer(style_text):
        name = m.group(1)
        props = m.group(2)
        if name in seen:
            continue
        if any(k in props for k in ["transition", "animation", "transform", "cubic-bezier"]):
            seen.add(name)
            results.append({"class": name, "props": props.strip()[:300]})
    return results[:12]

# ---------------------------------------------------------------------------
# Spacing — valores reais do CSS
# ---------------------------------------------------------------------------
def _extract_spacing(style_text: str) -> list[int]:
    values = set()
    for m in re.finditer(r"gap\s*:\s*([\d.]+)(px|rem)", style_text):
        px = float(m.group(1)) * (1 if m.group(2) == "px" else 16)
        values.add(int(px))
    for m in re.finditer(r"padding(?:-\w+)?\s*:\s*([\d.]+)px", style_text):
        values.add(int(float(m.group(1))))
    for m in re.finditer(r"margin(?:-\w+)?\s*:\s*([\d.]+)px", style_text):
        values.add(int(float(m.group(1))))
    canonical = [4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96]
    found = [v for v in canonical if any(abs(v - s) <= 2 for s in values)]
    return found or [4, 8, 16, 24, 32, 48, 64]

# ---------------------------------------------------------------------------
# Componentes — priority: DOM evidence, exact class names
# ---------------------------------------------------------------------------
def _extract_components(soup: BeautifulSoup) -> dict:
    components = {}

    # Nav — exato do DOM
    nav = soup.find("nav")
    if nav:
        components["nav"] = str(nav)

    # Botões — distintos por class signature
    seen_btn = set()
    buttons = []
    for btn in soup.find_all("button"):
        sig = " ".join(btn.get("class", []))[:80]
        if sig not in seen_btn:
            seen_btn.add(sig)
            buttons.append(str(btn))
        if len(buttons) >= 8:
            break
    if buttons:
        components["buttons"] = buttons

    # Links com aparência de botão
    btn_links = []
    seen_link = set()
    for a in soup.find_all("a"):
        cls = " ".join(a.get("class", []))
        if any(k in cls for k in ["btn", "button", "cta", "pill"]):
            sig = cls[:60]
            if sig not in seen_link:
                seen_link.add(sig)
                btn_links.append(str(a))
        if len(btn_links) >= 4:
            break
    if btn_links:
        components["btn_links"] = btn_links

    # Badges e tags
    seen_badge = set()
    badges = []
    for el in soup.find_all(["span", "div"]):
        cls = " ".join(el.get("class", []))
        if any(k in cls for k in ["badge", "tag", "pill", "chip", "label"]):
            sig = cls[:50]
            if sig not in seen_badge:
                seen_badge.add(sig)
                badges.append(str(el))
        if len(badges) >= 8:
            break
    if badges:
        components["badges"] = badges

    # Cards
    cards = []
    for el in soup.find_all(["div", "a", "article", "section"]):
        cls = " ".join(el.get("class", []))
        if any(k in cls for k in ["card", "case-card", "feature-card", "pricing", "tile"]):
            markup = str(el)
            if 200 < len(markup) < 2500:
                cards.append(markup)
        if len(cards) >= 3:
            break
    if cards:
        components["cards"] = cards

    # Inputs
    inputs = soup.find_all("input", limit=4)
    if inputs:
        components["inputs"] = [str(i) for i in inputs]

    # Scroll hints / section markers (do original v1)
    scroll_hints = []
    for el in soup.find_all(class_=True):
        cls = " ".join(el.get("class", []))
        if any(k in cls for k in ["scroll-hint", "scroll-indicator", "section-marker", "progress-"]):
            scroll_hints.append(str(el))
        if len(scroll_hints) >= 3:
            break
    if scroll_hints:
        components["scroll_hints"] = scroll_hints

    return components

# ---------------------------------------------------------------------------
# Ícones — exact markup, same classes
# ---------------------------------------------------------------------------
def _extract_icons(soup: BeautifulSoup) -> list[dict]:
    icons = []
    seen = set()

    for el in soup.find_all("iconify-icon"):
        name = el.get("icon", "")
        if name and name not in seen:
            seen.add(name)
            icons.append({
                "name": name,
                "markup": str(el),
                "width": el.get("width", "24"),
            })

    for svg in soup.find_all("svg"):
        title = svg.find("title")
        cls = " ".join(svg.get("class", []))
        key = cls[:40] or str(svg)[:50]
        if key not in seen and len(str(svg)) < 1000:
            seen.add(key)
            icons.append({
                "name": title.get_text() if title else cls or "icon",
                "markup": str(svg),
                "width": svg.get("width", "24"),
            })
        if len(icons) >= 28:
            break

    return icons[:28]

# ---------------------------------------------------------------------------
# Scroll system — conditional (do original v1)
# ---------------------------------------------------------------------------
def _extract_scroll_system(soup: BeautifulSoup, style_text: str) -> dict | None:
    """Extrai scroll observers e timeline bindings se existirem. Nunca fabrica."""
    scroll_data = {}

    # Elementos com data-scroll ou IntersectionObserver markers
    scroll_els = []
    for el in soup.find_all(attrs={"data-scroll": True}):
        scroll_els.append(str(el)[:200])
    for el in soup.find_all(class_=re.compile(r"scroll-|reveal-|in-view|observe")):
        markup = str(el)[:200]
        if markup not in scroll_els:
            scroll_els.append(markup)
    if scroll_els:
        scroll_data["elements"] = scroll_els[:6]

    # CSS scroll-driven (animation-timeline, scroll-timeline)
    scroll_css = []
    for m in re.finditer(r"animation-timeline\s*:[^;]+;", style_text):
        scroll_css.append(m.group(0).strip())
    for m in re.finditer(r"scroll-timeline[^;]+;", style_text):
        scroll_css.append(m.group(0).strip())
    if scroll_css:
        scroll_data["css_scroll"] = scroll_css[:4]

    return scroll_data if scroll_data else None

# ---------------------------------------------------------------------------
# 3D / Canvas layers — conditional (do original v1)
# ---------------------------------------------------------------------------
def _extract_canvas_layers(soup: BeautifulSoup) -> list[dict] | None:
    """Extrai canvas e WebGL containers se existirem. Nunca fabrica."""
    layers = []
    for canvas in soup.find_all("canvas"):
        layers.append({
            "tag": "canvas",
            "id": canvas.get("id", ""),
            "class": " ".join(canvas.get("class", [])),
            "markup": str(canvas)[:300],
        })
    for el in soup.find_all(class_=re.compile(r"webgl|three-|canvas-|shader-")):
        layers.append({
            "tag": el.name,
            "id": el.get("id", ""),
            "class": " ".join(el.get("class", [])),
            "markup": str(el)[:300],
        })
    return layers if layers else None

# ---------------------------------------------------------------------------
# Hero — clone exato, só texto substituído
# ---------------------------------------------------------------------------
def _extract_hero(soup: BeautifulSoup) -> str:
    for hero_id in ["hero", "home", "banner", "jumbotron", "intro"]:
        el = soup.find(id=hero_id)
        if el:
            return str(el)
    body = soup.find("body")
    if body:
        for child in body.children:
            if hasattr(child, "name") and child.name in ["section", "div", "header", "main"]:
                content = str(child)
                if len(content) > 600:
                    return content
    return ""

# ---------------------------------------------------------------------------
# Assets originais — nunca bundle, nunca inline
# ---------------------------------------------------------------------------
def _extract_style_blocks(soup: BeautifulSoup) -> str:
    return "\n".join(t.string for t in soup.find_all("style") if t.string)

def _extract_css_links(soup: BeautifulSoup) -> list[str]:
    return [t.get("href", "") for t in soup.find_all("link", rel="stylesheet") if t.get("href")]

def _extract_js_srcs(soup: BeautifulSoup) -> list[str]:
    srcs = []
    for t in soup.find_all("script", src=True):
        src = t.get("src", "")
        if src and not src.startswith("http"):
            srcs.append(src)
    return srcs

# ---------------------------------------------------------------------------
# Gerador de HTML — design-system.html
# ---------------------------------------------------------------------------
def generate_design_system(html_content: str) -> str:
    """
    Recebe o HTML já processado (index.html) e retorna
    o conteúdo do design-system.html como string.
    
    Chamado pelo downloader ANTES de empacotar o ZIP,
    para que ambos os arquivos entrem no mesmo pacote.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Extração — priority: DOM evidence
    style_text  = _extract_style_blocks(soup)
    css_links   = _extract_css_links(soup)
    js_srcs     = _extract_js_srcs(soup)
    tokens      = _extract_css_tokens(style_text)
    keyframes   = _extract_keyframes(style_text)
    transitions = _extract_transition_classes(style_text)
    spacing     = _extract_spacing(style_text)
    typography  = _extract_typography(soup, style_text)
    components  = _extract_components(soup)
    icons       = _extract_icons(soup)
    hero_html   = _extract_hero(soup)
    arb_colors  = _extract_css_tokens_from_classes(soup)
    surfaces    = _extract_opacity_surfaces(soup)
    gradients   = _extract_gradient_classes(soup)
    scroll_sys  = _extract_scroll_system(soup, style_text)
    canvas_lyrs = _extract_canvas_layers(soup)

    page_title = soup.find("title")
    title_text = page_title.get_text() if page_title else "Site"

    # Asset refs — same originals, never inlined
    css_tags = "\n".join(f'<link rel="stylesheet" href="{h}"/>' for h in css_links)
    js_tags  = "\n".join(f'<script src="{s}"></script>' for s in js_srcs)

    # ── 01 Tokens ──
    color_tokens = [t for t in tokens if t["is_color"]]
    other_tokens = [t for t in tokens if not t["is_color"]]

    token_swatches = ""
    for t in color_tokens:
        resolved = _resolve_var(t["value"], tokens)
        token_swatches += f"""
        <div class="ds-token-row">
          <div class="ds-token-swatch">
            <div class="ds-swatch-fill" style="background:{resolved}"></div>
          </div>
          <div class="ds-token-info">
            <code class="ds-tname">{t['name']}</code>
            <span class="ds-tvalue">{t['value']}</span>
            <span class="ds-tlabel">{t['label']}</span>
          </div>
        </div>"""

    arb_swatches = ""
    for c in arb_colors[:16]:
        arb_swatches += f"""
        <div class="ds-arb-swatch">
          <div class="ds-arb-fill" style="background:{c}"></div>
          <code class="ds-arb-val">{c}</code>
        </div>"""

    surface_swatches = ""
    for s in surfaces:
        pct = int(s.split("/")[-1]) / 100
        bg = f"rgba(255,255,255,{pct})" if "white" in s else f"rgba(0,0,0,{pct})"
        surface_swatches += f"""
        <div class="ds-arb-swatch">
          <div class="ds-arb-fill ds-checker" style="--sfill:{bg}"></div>
          <code class="ds-arb-val">{s}</code>
        </div>"""

    grad_swatches = ""
    for g in gradients:
        grad_swatches += f"""
        <div class="ds-grad-item">
          <div class="{g} ds-grad-preview"></div>
          <code class="ds-arb-val" style="font-size:9px">{g[:60]}</code>
        </div>"""

    other_rows = ""
    for t in other_tokens[:10]:
        other_rows += f"""
        <div class="ds-other-row">
          <code class="ds-tname">{t['name']}</code>
          <span class="ds-tvalue">{t['value'][:40]}</span>
          <span class="ds-tlabel">{t['label']}</span>
        </div>"""

    tokens_section = f"""
      <div class="ds-tokens-grid">{token_swatches or '<p class="ds-empty">Nenhuma CSS variable encontrada.</p>'}</div>
      {'<h3 class="ds-sub">Tailwind arbitrary colors</h3><div class="ds-arb-grid">' + arb_swatches + '</div>' if arb_swatches else ''}
      {'<h3 class="ds-sub">Opacity surfaces</h3><div class="ds-arb-grid">' + surface_swatches + '</div>' if surface_swatches else ''}
      {'<h3 class="ds-sub">Gradients</h3><div class="ds-arb-grid">' + grad_swatches + '</div>' if grad_swatches else ''}
      {'<h3 class="ds-sub">Outros tokens</h3><div class="ds-other-grid">' + other_rows + '</div>' if other_rows else ''}
    """

    # ── 02 Typography — formato '40px / 48px' do v2 ──
    typo_rows = ""
    for t in typography:
        typo_rows += f"""
        <div class="ds-typo-row">
          <div class="ds-typo-meta">
            <span class="ds-typo-label">{t['label']}</span>
            <span class="ds-typo-size">{t['size_hint']}</span>
            <code class="ds-typo-tag">&lt;{t['tag']}&gt;</code>
          </div>
          <div class="ds-typo-live">
            <{t['tag']} class="{t['classes']}" style="margin:0">{t['sample']}</{t['tag']}>
          </div>
          <div class="ds-typo-cls"><code>{t['classes'][:100]}</code></div>
        </div>"""

    # ── 03 Spacing ──
    spacing_items = ""
    for v in spacing:
        spacing_items += f"""
        <div class="ds-space-row">
          <div class="ds-space-bar" style="width:{min(v * 2.8, 400)}px"></div>
          <div class="ds-space-meta">
            <span class="ds-space-px">{v}px</span>
            <span class="ds-space-rem">{v/16:.3g}rem</span>
          </div>
        </div>"""

    # ── 04 Components ──
    comp_html = ""
    if components.get("nav"):
        comp_html += f'<h3 class="ds-sub">Navigation</h3><div class="ds-canvas">{components["nav"]}</div>'
    if components.get("buttons"):
        items = "".join(f'<div class="ds-comp-item">{b}</div>' for b in components["buttons"])
        comp_html += f'<h3 class="ds-sub">Buttons — default / hover / active</h3><div class="ds-comp-row">{items}</div>'
    if components.get("btn_links"):
        items = "".join(f'<div class="ds-comp-item">{b}</div>' for b in components["btn_links"])
        comp_html += f'<h3 class="ds-sub">Button Links</h3><div class="ds-comp-row">{items}</div>'
    if components.get("badges"):
        items = "".join(f'<div class="ds-comp-item">{b}</div>' for b in components["badges"])
        comp_html += f'<h3 class="ds-sub">Badges & Tags</h3><div class="ds-comp-row">{items}</div>'
    if components.get("cards"):
        items = "".join(f'<div class="ds-card-item">{c}</div>' for c in components["cards"])
        comp_html += f'<h3 class="ds-sub">Cards</h3><div class="ds-cards-grid">{items}</div>'
    if components.get("inputs"):
        items = "".join(f'<div class="ds-comp-item">{i}</div>' for i in components["inputs"])
        comp_html += f'<h3 class="ds-sub">Inputs</h3><div class="ds-comp-row">{items}</div>'
    if components.get("scroll_hints"):
        items = "".join(f'<div class="ds-comp-item">{h}</div>' for h in components["scroll_hints"])
        comp_html += f'<h3 class="ds-sub">Scroll hints / Section markers</h3><div class="ds-comp-row">{items}</div>'
    if not comp_html:
        comp_html = "<p class='ds-empty'>Nenhum componente identificado no DOM.</p>"

    # ── 05 Motion — exact keyframes, never approximate easing ──
    motion_items = ""
    for kf in keyframes:
        motion_items += f"""
        <div class="ds-motion-item">
          <div class="ds-motion-label"><code>@keyframes {kf['name']}</code></div>
          <pre class="ds-code">{kf['body']}</pre>
        </div>"""
    for tc in transitions:
        motion_items += f"""
        <div class="ds-motion-item">
          <div class="ds-motion-label"><code>.{tc['class']}</code></div>
          <pre class="ds-code">{tc['props']}</pre>
        </div>"""
    if not motion_items:
        motion_items = "<p class='ds-empty'>Nenhuma animação encontrada no CSS.</p>"

    # ── 06 Icons — exact markup, same classes ──
    has_icons = bool(icons)
    icon_items = ""
    for ic in icons:
        icon_items += f"""
        <div class="ds-icon-item">
          <div class="ds-icon-preview">{ic['markup']}</div>
          <code class="ds-icon-name">{ic['name'][:28]}</code>
        </div>"""
    icons_section = f"""
    <section class="ds-section" id="icons">
      <p class="ds-tag">06 — Icons</p>
      <h2 class="ds-title">Icon set.</h2>
      <p class="ds-desc">Ícones extraídos do DOM com markup e classes exatos.</p>
      <div class="ds-icons-grid">{icon_items}</div>
    </section>""" if has_icons else ""

    # ── 07 Scroll system — conditional (v1) ──
    scroll_section = ""
    if scroll_sys:
        scroll_items = ""
        for el in scroll_sys.get("elements", []):
            scroll_items += f'<pre class="ds-code">{el}</pre>'
        for css in scroll_sys.get("css_scroll", []):
            scroll_items += f'<pre class="ds-code">{css}</pre>'
        scroll_section = f"""
    <section class="ds-section" id="scroll-system">
      <p class="ds-tag">07 — Scroll System</p>
      <h2 class="ds-title">Scroll orchestration.</h2>
      <p class="ds-desc">Observers, timeline bindings e triggers extraídos do DOM. Nunca recriados manualmente.</p>
      <div class="ds-motion-grid">{scroll_items}</div>
    </section>"""

    # ── 08 Canvas/3D — conditional (v1) ──
    canvas_section = ""
    if canvas_lyrs:
        canvas_items = ""
        for layer in canvas_lyrs:
            canvas_items += f"""
            <div class="ds-motion-item">
              <div class="ds-motion-label"><code>{layer['tag']}#{layer['id'] or layer['class'][:30]}</code></div>
              <pre class="ds-code">{layer['markup']}</pre>
            </div>"""
        canvas_section = f"""
    <section class="ds-section" id="3d-layers">
      <p class="ds-tag">08 — Canvas / 3D Layers</p>
      <h2 class="ds-title">Rendering layer map.</h2>
      <p class="ds-desc">Canvas containers e WebGL surfaces extraídos do DOM.</p>
      <div class="ds-motion-grid">{canvas_items}</div>
    </section>"""

    # ── Nav anchors — remove os que não têm evidência ──
    nav_items = [
        ('<a href="#hero" class="ds-nav-link">Hero</a>', True),
        ('<a href="#tokens" class="ds-nav-link">Tokens</a>', bool(tokens or arb_colors)),
        ('<a href="#typography" class="ds-nav-link">Typography</a>', bool(typography)),
        ('<a href="#spacing" class="ds-nav-link">Spacing</a>', bool(spacing)),
        ('<a href="#components" class="ds-nav-link">Components</a>', bool(components)),
        ('<a href="#motion" class="ds-nav-link">Motion</a>', bool(keyframes or transitions)),
        ('<a href="#icons" class="ds-nav-link">Icons</a>', has_icons),
        ('<a href="#scroll-system" class="ds-nav-link">Scroll</a>', bool(scroll_sys)),
        ('<a href="#3d-layers" class="ds-nav-link">Canvas</a>', bool(canvas_lyrs)),
    ]
    nav_html = "\n".join(link for link, show in nav_items if show)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Design System — {title_text}</title>

<!-- Assets originais — nunca bundled, nunca inline -->
{css_tags}
{js_tags}

<style>
/* ── CSS original do site — preservado integralmente ── */
{style_text}

/* ── Design System Showcase Layer ── */
:root {{
  --ds-bg:      #09090b;
  --ds-surf:    #111114;
  --ds-surf2:   #18181c;
  --ds-border:  rgba(255,255,255,0.07);
  --ds-border2: rgba(255,255,255,0.13);
  --ds-text:    rgba(255,255,255,0.88);
  --ds-muted:   rgba(255,255,255,0.38);
  --ds-dim:     rgba(255,255,255,0.16);
  --ds-accent:  #6366f1;
  --ds-acc-bg:  rgba(99,102,241,0.09);
  --ds-mono:    ui-monospace,'Cascadia Code','SF Mono',Menlo,monospace;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--ds-bg);color:var(--ds-text);font-family:ui-sans-serif,system-ui,sans-serif;min-height:100vh}}

/* Nav */
.ds-topnav{{position:sticky;top:0;z-index:9999;display:flex;align-items:center;gap:4px;padding:11px 40px;background:rgba(9,9,11,0.92);backdrop-filter:blur(24px);border-bottom:1px solid var(--ds-border)}}
.ds-topnav-logo{{font-size:12px;font-weight:700;color:var(--ds-text);margin-right:auto;letter-spacing:0.08em;text-transform:uppercase;display:flex;align-items:center;gap:8px}}
.ds-topnav-logo::before{{content:'';width:6px;height:6px;background:var(--ds-accent);border-radius:50%;flex-shrink:0}}
.ds-topnav-badge{{font-size:10px;font-family:var(--ds-mono);color:var(--ds-accent);background:var(--ds-acc-bg);border:1px solid rgba(99,102,241,0.2);padding:3px 10px;border-radius:100px;margin-right:10px}}
.ds-nav-link{{font-size:11px;color:var(--ds-muted);text-decoration:none;padding:5px 10px;border-radius:100px;transition:all 0.15s;font-family:var(--ds-mono)}}
.ds-nav-link:hover{{color:var(--ds-text);background:rgba(255,255,255,0.05)}}

/* Sections */
.ds-section{{padding:72px 48px;border-top:1px solid var(--ds-border);max-width:1280px;margin:0 auto}}
.ds-tag{{font-size:11px;font-family:var(--ds-mono);color:var(--ds-accent);letter-spacing:0.12em;text-transform:uppercase;margin:0 0 10px}}
.ds-title{{font-size:clamp(24px,4vw,42px);font-weight:600;letter-spacing:-0.025em;color:#fff;margin:0 0 10px;line-height:1.1}}
.ds-desc{{font-size:14px;color:var(--ds-muted);margin:0 0 44px;line-height:1.6;max-width:560px}}
.ds-sub{{font-size:12px;font-weight:600;color:var(--ds-text);margin:36px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--ds-border)}}
.ds-empty{{font-size:13px;color:var(--ds-muted);padding:20px 0}}

/* Hero wrapper */
.ds-hero-wrap{{position:relative;overflow:hidden;border-bottom:1px solid var(--ds-border)}}

/* Tokens */
.ds-tokens-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}}
.ds-token-row{{display:flex;align-items:center;gap:14px;padding:13px 15px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:10px;transition:border-color 0.15s}}
.ds-token-row:hover{{border-color:var(--ds-border2)}}
.ds-token-swatch{{width:44px;height:44px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);flex-shrink:0;background-image:linear-gradient(45deg,#222 25%,transparent 25%),linear-gradient(-45deg,#222 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#222 75%),linear-gradient(-45deg,transparent 75%,#222 75%);background-size:8px 8px;position:relative;overflow:hidden}}
.ds-swatch-fill{{position:absolute;inset:0;border-radius:7px}}
.ds-token-info{{min-width:0;flex:1}}
.ds-tname{{font-family:var(--ds-mono);font-size:11px;color:var(--ds-accent);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ds-tvalue{{font-size:11px;color:var(--ds-muted);font-family:var(--ds-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px;display:block}}
.ds-tlabel{{font-size:11px;color:var(--ds-dim);margin-top:3px;display:block}}
.ds-other-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:7px}}
.ds-other-row{{display:flex;align-items:center;gap:10px;padding:9px 13px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:8px;font-size:12px;overflow:hidden}}
.ds-other-row .ds-tname{{color:var(--ds-accent);flex-shrink:0;font-size:11px}}
.ds-other-row .ds-tvalue{{color:var(--ds-muted);margin-left:auto;font-family:var(--ds-mono);font-size:11px}}
.ds-other-row .ds-tlabel{{color:var(--ds-dim);font-size:11px;white-space:nowrap}}
.ds-arb-grid{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}}
.ds-arb-swatch{{display:flex;flex-direction:column;align-items:center;gap:6px}}
.ds-arb-fill{{width:56px;height:56px;border-radius:10px;border:1px solid var(--ds-border)}}
.ds-checker{{background-image:linear-gradient(45deg,#222 25%,transparent 25%),linear-gradient(-45deg,#222 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#222 75%),linear-gradient(-45deg,transparent 75%,#222 75%);background-size:8px 8px;position:relative}}
.ds-checker::after{{content:'';position:absolute;inset:0;border-radius:9px;background:var(--sfill)}}
.ds-arb-val{{font-family:var(--ds-mono);font-size:9px;color:var(--ds-muted);text-align:center;max-width:64px;word-break:break-all}}
.ds-grad-item{{display:flex;flex-direction:column;gap:6px}}
.ds-grad-preview{{width:120px;height:56px;border-radius:10px;border:1px solid var(--ds-border)}}

/* Typography — formato 40px / 48px */
.ds-typo-row{{display:grid;grid-template-columns:180px 1fr 200px;gap:20px;align-items:center;padding:18px 0;border-bottom:1px solid var(--ds-border)}}
.ds-typo-meta{{display:flex;flex-direction:column;gap:5px}}
.ds-typo-label{{font-size:12px;font-weight:600;color:var(--ds-text)}}
.ds-typo-size{{font-size:11px;color:var(--ds-accent);font-family:var(--ds-mono)}}
.ds-typo-tag{{font-family:var(--ds-mono);font-size:10px;color:var(--ds-muted);background:var(--ds-acc-bg);padding:2px 7px;border-radius:4px;width:fit-content}}
.ds-typo-live{{overflow:hidden}}
.ds-typo-live h1,.ds-typo-live h2,.ds-typo-live h3,.ds-typo-live h4,.ds-typo-live p{{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.ds-typo-cls{{overflow:hidden}}
.ds-typo-cls code{{font-family:var(--ds-mono);font-size:9px;color:var(--ds-dim);word-break:break-all;line-height:1.6}}

/* Spacing */
.ds-spacing-list{{display:flex;flex-direction:column;gap:10px}}
.ds-space-row{{display:flex;align-items:center;gap:16px}}
.ds-space-bar{{height:28px;min-width:4px;background:var(--ds-acc-bg);border:1px solid rgba(99,102,241,0.2);border-radius:5px;transition:background 0.2s}}
.ds-space-row:hover .ds-space-bar{{background:rgba(99,102,241,0.16)}}
.ds-space-meta{{display:flex;flex-direction:column;gap:2px}}
.ds-space-px{{font-family:var(--ds-mono);font-size:13px;color:var(--ds-text)}}
.ds-space-rem{{font-family:var(--ds-mono);font-size:11px;color:var(--ds-muted)}}

/* Components */
.ds-canvas{{padding:22px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:12px;margin-bottom:8px;overflow-x:auto}}
.ds-comp-row{{display:flex;flex-wrap:wrap;gap:12px;padding:20px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:12px;margin-bottom:8px;align-items:center}}
.ds-comp-item{{display:flex;align-items:center}}
.ds-cards-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
.ds-card-item{{overflow:hidden}}

/* Motion — exact, never approximate */
.ds-motion-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.ds-motion-item{{background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:10px;padding:16px}}
.ds-motion-label{{margin-bottom:10px}}
.ds-motion-label code{{font-family:var(--ds-mono);font-size:12px;color:var(--ds-accent)}}
.ds-code{{font-family:var(--ds-mono);font-size:11px;color:var(--ds-muted);background:rgba(0,0,0,0.3);border:1px solid var(--ds-border);border-radius:6px;padding:10px;overflow-x:auto;white-space:pre-wrap;line-height:1.7;margin:0;display:block}}

/* Icons — same markup, same classes */
.ds-icons-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:10px}}
.ds-icon-item{{display:flex;flex-direction:column;align-items:center;gap:7px;padding:14px 8px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:10px;transition:background 0.15s,border-color 0.15s}}
.ds-icon-item:hover{{background:var(--ds-surf2);border-color:var(--ds-border2)}}
.ds-icon-preview{{display:flex;align-items:center;justify-content:center;color:var(--ds-text)}}
.ds-icon-name{{font-family:var(--ds-mono);font-size:9px;color:var(--ds-muted);text-align:center;word-break:break-all}}

@media(max-width:768px){{
  .ds-section{{padding:48px 20px}}
  .ds-topnav{{padding:11px 20px}}
  .ds-topnav-badge,.ds-nav-link{{display:none}}
  .ds-typo-row{{grid-template-columns:1fr;gap:8px}}
  .ds-typo-cls{{display:none}}
}}
</style>
</head>
<body>

<nav class="ds-topnav">
  <span class="ds-topnav-logo">Design System</span>
  <span class="ds-topnav-badge">pattern library v3</span>
  {nav_html}
</nav>

<!-- 00 — HERO: clone exato, só texto substituído -->
<div class="ds-hero-wrap" id="hero">{hero_html}</div>

<!-- 01 — TOKENS -->
<section class="ds-section" id="tokens">
  <p class="ds-tag">01 — Tokens</p>
  <h2 class="ds-title">Color tokens & variables.</h2>
  <p class="ds-desc">CSS custom properties extraídas de :root com label semântico inferido. Apenas o que existe no DOM.</p>
  {tokens_section}
</section>

<!-- 02 — TYPOGRAPHY: formato 40px / 48px -->
<section class="ds-section" id="typography">
  <p class="ds-tag">02 — Typography</p>
  <h2 class="ds-title">Type scale & hierarchy.</h2>
  <p class="ds-desc">Escala tipográfica com elemento HTML real, classes originais e anotação font-size / line-height.</p>
  <div>
    {typo_rows or '<p class="ds-empty">Nenhum elemento tipográfico encontrado no DOM.</p>'}
  </div>
</section>

<!-- 03 — SPACING -->
<section class="ds-section" id="spacing">
  <p class="ds-tag">03 — Spacing</p>
  <h2 class="ds-title">Spacing scale.</h2>
  <p class="ds-desc">Valores de gap, padding e margin extraídos do CSS real. Nenhum valor aproximado.</p>
  <div class="ds-spacing-list">{spacing_items}</div>
</section>

<!-- 04 — COMPONENTS: state matrix default/hover/active -->
<section class="ds-section" id="components">
  <p class="ds-tag">04 — Components</p>
  <h2 class="ds-title">Components & states.</h2>
  <p class="ds-desc">Markup exato do DOM. Classes, hover states e interações preservados.</p>
  {comp_html}
</section>

<!-- 05 — MOTION: exact keyframes, never approximate easing -->
<section class="ds-section" id="motion">
  <p class="ds-tag">05 — Motion</p>
  <h2 class="ds-title">Motion & animation.</h2>
  <p class="ds-desc">Keyframes e timing curves exatos do CSS original. Nenhuma curva aproximada.</p>
  <div class="ds-motion-grid">{motion_items}</div>
</section>

<!-- 06 — ICONS: conditional -->
{icons_section}

<!-- 07 — SCROLL SYSTEM: conditional -->
{scroll_section}

<!-- 08 — CANVAS/3D: conditional -->
{canvas_section}

</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point para o downloader.py
# (lê ZIP, gera design-system e devolve string HTML — downloader inclui no ZIP)
# ---------------------------------------------------------------------------
def generate_from_zip(zip_path: Path) -> str | None:
    """
    Lê o index.html de dentro do ZIP e retorna o HTML
    do design-system.html como string, ou None se falhar.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            html_name = next((n for n in names if n.endswith("index.html")), None)
            if not html_name:
                return None
            html_content = zf.read(html_name).decode("utf-8", errors="replace")
        return generate_design_system(html_content)
    except Exception as e:
        print(f"[design_system_generator v3] erro: {e}")
        import traceback; traceback.print_exc()
        return None
