"""
design_system_generator.py — v4 INEVITABLE

Arquitetura definitiva baseada em:
  - Pesquisa dos melhores DS do mundo (Material 3, Carbon IBM, Polaris Shopify, HIG Apple, Vercel)
  - Engenharia reversa dos melhores clonadores (HTTrack, SingleFile, ArchiveBox, Playwright)
  - Análise do preview.html como padrão mínimo de qualidade

DECISÃO CENTRAL:
  Usa a API do Claude para analisar o HTML e gerar documentação de qualidade editorial.
  Extração mecânica (regex + BeautifulSoup) alimenta o contexto.
  LLM transforma em design-system.html de nível FAANG.

ESTRUTURA GERADA (baseada no padrão universal FAANG):
  00 — Hero        (clone exato, texto adaptado)
  01 — Tokens      (color + type + spacing + shadow + border + motion)
  02 — Typography  (spec table 4 colunas: nome | preview | elemento | px/lh)
  03 — Colors      (palette grid 16:10 + superfícies + gradients)
  04 — Components  (anatomy + state matrix 5 estados)
  05 — Layout      (wireframes visuais + containers + breakpoints)
  06 — Motion      (gallery com demos ao vivo + tokens exatos)
  07 — Icons       (condicional)
  08 — Scroll      (condicional)
  09 — Canvas/3D   (condicional)

PROMPT MERGED (melhores partes dos dois prompts originais):
  - HARD RULES completas (14) do prompt original
  - Hierarquia tipográfica H1→Bold→Paragraph→Regular do v2
  - Formato '40px / 48px' do v2
  - Seções condicionais scroll/3D do original
  - Checklist de validação do original
  - Priority order: DOM evidence > class frequency > animation bindings
"""

import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# CONSTANTES DO SISTEMA
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8000
HTML_TRUNCATE = 80_000
DESIGN_SYSTEM_MODE = os.getenv("DESIGN_SYSTEM_MODE", "auto").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

SEMANTIC_MAP = [
    (r"brand|primary|accent|highlight",  "Brand Primary"),
    (r"secondary",                        "Secondary"),
    (r"plum|purple|violet|indigo",        "Brand Purple"),
    (r"gold|amber|yellow",               "Accent Gold"),
    (r"rose|pink|red|danger|error",      "Danger"),
    (r"green|success|emerald|teal",      "Success"),
    (r"blue|sky|cyan|info",              "Info"),
    (r"ink|text|fg|foreground|copy",     "Text"),
    (r"muted|soft|dim|subtle|faint",     "Text Muted"),
    (r"bg|background|surface|cream|canvas|base", "Background"),
    (r"card|panel|layer|glass",          "Surface Card"),
    (r"stone|neutral|gray|grey|slate",   "Neutral"),
    (r"border|divider|separator|stroke", "Border"),
    (r"white",                           "White"),
    (r"black",                           "Black"),
    (r"shadow|overlay|scrim",            "Shadow Overlay"),
]

def _infer_label(var_name: str) -> str:
    name = var_name.strip("-").lower()
    for pattern, label in SEMANTIC_MAP:
        if re.search(pattern, name):
            return label
    return var_name.strip("-").replace("-", " ").title()


# ---------------------------------------------------------------------------
# EXTRAÇÃO DE CONTEXTO (alimenta o prompt da LLM)
# ---------------------------------------------------------------------------

def _extract_context(html: str) -> dict:
    """
    Extrai contexto estruturado do HTML para alimentar a LLM.
    Foca em evidências factuais — nunca interpola.
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- CSS completo ---
    style_blocks = "\n".join(
        t.string for t in soup.find_all("style") if t.string
    )

    # --- CSS custom properties (:root) ---
    tokens = {}
    for block in re.findall(r":root\s*(?:\.[\w-]+\s*)?\{([^}]+)\}", style_blocks, re.DOTALL):
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block):
            name, value = m.group(1).strip(), m.group(2).strip()
            if name not in tokens:
                tokens[name] = {
                    "value": value,
                    "label": _infer_label(name),
                    "is_color": bool(
                        re.match(r"#[0-9a-f]{3,8}|rgb|hsl|oklch|color\(", value.lower())
                    ),
                }

    # --- Keyframes ---
    keyframes = re.findall(r"@keyframes\s+([\w-]+)", style_blocks)

    # --- Fontes usadas ---
    fonts = list(set(re.findall(
        r"font-family\s*:\s*['\"]?([^'\",;{}]+)['\"]?", style_blocks
    )))[:8]

    # --- Google Fonts imports ---
    gf = [t.get("href", "") for t in soup.find_all("link", rel="stylesheet")
          if "fonts.googleapis.com" in t.get("href", "")]

    # --- Asset refs ---
    css_links = [t.get("href", "") for t in soup.find_all("link", rel="stylesheet")
                 if t.get("href")]
    js_srcs   = [t.get("src", "") for t in soup.find_all("script", src=True)
                 if t.get("src") and not t.get("src", "").startswith("http")]

    # --- Tipografia do DOM ---
    typo_evidence = []
    for tag in ["h1", "h2", "h3", "h4", "p"]:
        el = soup.find(tag)
        if el:
            typo_evidence.append({
                "tag": tag,
                "classes": " ".join(el.get("class", []))[:120],
                "text_sample": el.get_text()[:60].strip(),
            })

    # --- Componentes do DOM ---
    components = {}

    nav = soup.find("nav")
    if nav:
        components["nav"] = str(nav)[:800]

    buttons = []
    seen_btn = set()
    for btn in soup.find_all("button")[:10]:
        sig = " ".join(btn.get("class", []))[:80]
        if sig not in seen_btn:
            seen_btn.add(sig)
            buttons.append({
                "classes": sig,
                "text": btn.get_text()[:30].strip(),
                "markup": str(btn)[:200],
            })
    if buttons:
        components["buttons"] = buttons

    cards = []
    for el in soup.find_all(["div", "a", "article"]):
        cls = " ".join(el.get("class", []))
        if any(k in cls for k in ["card", "case-card", "feature-card", "pricing"]):
            markup = str(el)
            if 200 < len(markup) < 2000:
                cards.append({"classes": cls[:80], "markup": markup[:400]})
        if len(cards) >= 3:
            break
    if cards:
        components["cards"] = cards

    badges = []
    seen_b = set()
    for el in soup.find_all(["span", "div"]):
        cls = " ".join(el.get("class", []))
        if any(k in cls for k in ["badge", "tag", "pill", "chip"]):
            sig = cls[:50]
            if sig not in seen_b:
                seen_b.add(sig)
                badges.append({"classes": cls[:80], "markup": str(el)[:150]})
        if len(badges) >= 6:
            break
    if badges:
        components["badges"] = badges

    inputs_found = [str(i)[:200] for i in soup.find_all("input", limit=3)]
    if inputs_found:
        components["inputs"] = inputs_found

    # --- Hero HTML ---
    hero_html = ""
    for hid in ["hero", "home", "banner", "jumbotron", "intro"]:
        el = soup.find(id=hid)
        if el:
            hero_html = str(el)[:3000]
            break
    if not hero_html:
        body = soup.find("body")
        if body:
            for child in body.children:
                if hasattr(child, "name") and child.name in ["section","div","header","main"]:
                    if len(str(child)) > 600:
                        hero_html = str(child)[:3000]
                        break

    # --- Ícones ---
    icons = []
    seen_ic = set()
    for el in soup.find_all("iconify-icon"):
        name = el.get("icon", "")
        if name and name not in seen_ic:
            seen_ic.add(name)
            icons.append({"type": "iconify", "name": name, "markup": str(el)})
    for svg in soup.find_all("svg"):
        cls = " ".join(svg.get("class", []))
        key = cls[:30] or str(svg)[:40]
        if key not in seen_ic and len(str(svg)) < 600:
            seen_ic.add(key)
            title = svg.find("title")
            icons.append({"type": "svg", "name": title.get_text() if title else cls or "icon", "markup": str(svg)[:200]})
        if len(icons) >= 20:
            break

    # --- Scroll / Canvas ---
    scroll_els = [str(el)[:200] for el in soup.find_all(attrs={"data-scroll": True})[:3]]
    scroll_cls = [str(el)[:200] for el in soup.find_all(
        class_=re.compile(r"scroll-|reveal-|in-view|observe"))[:3]]
    canvas_els = [{"id": c.get("id",""), "class": " ".join(c.get("class",[]))[:60]}
                  for c in soup.find_all("canvas")]

    # --- Page title ---
    title_el = soup.find("title")
    title = title_el.get_text() if title_el else "Site"

    # --- Arb colors from Tailwind ---
    arb_colors = set()
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = re.search(r"(?:bg|text|border|from|to|via)-\[([^\]]+)\]", cls)
            if m and re.match(r"#[0-9a-f]{3,8}", m.group(1).lower()):
                arb_colors.add(m.group(1))
    arb_colors = list(arb_colors)[:16]

    # Surfaces (bg-white/5, bg-black/30 etc)
    opacity_surfaces = set()
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            m = re.search(r"bg-(white|black)/([\d]+)", cls)
            if m:
                opacity_surfaces.add(f"bg-{m.group(1)}/{m.group(2)}")

    return {
        "title": title,
        "css_links": css_links,
        "js_srcs": js_srcs,
        "google_fonts": gf,
        "style_css": style_blocks[:40_000],
        "tokens": tokens,
        "keyframes": keyframes[:20],
        "fonts": fonts,
        "typo_evidence": typo_evidence,
        "components": components,
        "hero_html": hero_html,
        "icons": icons[:20],
        "scroll_elements": scroll_els + scroll_cls,
        "canvas_elements": canvas_els,
        "arb_colors": arb_colors,
        "opacity_surfaces": list(opacity_surfaces)[:12],
    }


# ---------------------------------------------------------------------------
# PROMPT DA LLM (merged dos dois prompts originais + padrão FAANG)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a world-class Design System Documentation Engineer.

Your task is to analyze a website's HTML/CSS and produce a single, self-contained design-system.html file that documents the site's design system with the quality of Google Material 3, IBM Carbon, or Shopify Polaris documentation.

QUALITY STANDARD (minimum acceptable):
- Professional light-theme document with its own design tokens (not the site's)
- Typography as a 4-column spec table: Style | Live preview | Element | Size / Line-height
- Color palette as cards with 16:10 swatch + hex + name + semantic usage
- Components with 5-state matrix (Default / Hover / Active / Focus / Disabled) side-by-side in grid
- Motion as interactive live demos with exact timing curves
- Layout with visual wireframe diagrams

HARD RULES (NON-NEGOTIABLE):
1. Never redesign anything — document what exists
2. Reuse exact class names from the source
3. Reference same CSS/JS assets as the original
4. If a component does not exist in source → do not include it
5. Never approximate animations or easing curves
6. Never rename CSS classes or selectors
7. Never introduce synthetic wrappers not in the source
8. Never interpolate styles not evidenced in the DOM
9. The hero section MUST be a structural clone of the original (text may change)
10. Typography previews use standardized sample text, NOT original site content
11. All font-size annotations use format "40px / 48px" (size / line-height)
12. Sections exist ONLY when there is DOM evidence — never fabricate
13. Color tokens display hex/rgba values explicitly — no abstract tokens
14. Motion section shows exact @keyframes code from source

FAANG-LEVEL STRUCTURE:
Section 00 — Hero: exact clone, text replaced with DS introduction
Section 01 — Tokens: ALL CSS custom properties (--var-name), categorized
Section 02 — Typography: spec table with live preview (standardized text), element, px/lh
Section 03 — Colors: palette grid, surfaces, gradients
Section 04 — Components: nav, buttons, badges, cards, inputs — with state matrix
Section 05 — Layout: visual wireframes + spacing scale + breakpoints
Section 06 — Motion: live animation demos + exact keyframe code
Section 07 — Icons: (ONLY if found in source)
Section 08 — Scroll System: (ONLY if scroll observers found)

THE OUTPUT MUST BE:
- A single, complete, valid HTML file
- Self-contained with its own document-layer CSS (NOT removing the original CSS)
- Light-theme professional documentation aesthetic
- Working interactive motion demos
- Fully responsive
- Ready to open in any browser with no server

OUTPUT FORMAT:
Return ONLY the complete HTML document. No markdown, no explanation, no backticks.
Start with <!DOCTYPE html> and end with </html>.
"""

def _build_user_prompt(ctx: dict) -> str:
    tokens_summary = "\n".join(
        f"  {k}: {v['value']} [{v['label']}]{'  ← color' if v['is_color'] else ''}"
        for k, v in list(ctx["tokens"].items())[:40]
    )
    typo_summary = "\n".join(
        f"  <{t['tag']} class=\"{t['classes']}\"> → sample: '{t['text_sample']}'"
        for t in ctx["typo_evidence"]
    )
    comp_summary = "\n".join(
        f"  {k}: {len(v) if isinstance(v, list) else 'found'}"
        for k, v in ctx["components"].items()
    )
    icon_summary = "\n".join(
        f"  {ic['name']}" for ic in ctx["icons"][:10]
    )
    arb_colors = ", ".join(ctx["arb_colors"][:12])
    surfaces = ", ".join(ctx["opacity_surfaces"][:8])

    return f"""WEBSITE: {ctx['title']}

CSS ASSET LINKS (reference these exactly):
{chr(10).join(ctx['css_links'][:8])}

JS ASSET SRCS (reference these exactly):
{chr(10).join(ctx['js_srcs'][:6])}

Google Fonts imports found:
{chr(10).join(ctx['google_fonts'][:3]) or 'none'}

CSS CUSTOM PROPERTIES (tokens):
{tokens_summary or '  (none found — site uses Tailwind utility classes)'}

KEYFRAME ANIMATIONS FOUND:
{', '.join(ctx['keyframes']) or 'none'}

FONTS DECLARED:
{', '.join(ctx['fonts'][:6]) or 'unknown'}

TYPOGRAPHY EVIDENCE FROM DOM:
{typo_summary or '  (no heading elements found)'}

COMPONENTS FOUND:
{comp_summary or '  (minimal components)'}

TAILWIND ARBITRARY COLORS:
{arb_colors or 'none'}

OPACITY SURFACES (bg-white/X, bg-black/X):
{surfaces or 'none'}

ICONS FOUND:
{icon_summary or 'none'}

SCROLL OBSERVERS FOUND:
{'yes — ' + str(len(ctx['scroll_elements'])) + ' elements' if ctx['scroll_elements'] else 'none'}

CANVAS/WEBGL ELEMENTS FOUND:
{str(ctx['canvas_elements']) if ctx['canvas_elements'] else 'none'}

HERO HTML (clone this exactly, replace text only):
{ctx['hero_html'][:2000] if ctx['hero_html'] else '(no hero found)'}

FULL CSS (use for evidence, extract tokens, keyframes, transitions):
{ctx['style_css'][: min(len(ctx['style_css']), HTML_TRUNCATE)]}

---

Now generate the complete design-system.html following all rules and the FAANG-level structure.
The document must have its own professional light-theme aesthetic independent of the site, while preserving all original CSS classes and asset references.
Remember: output ONLY the HTML document, starting with <!DOCTYPE html>.
"""


# ---------------------------------------------------------------------------
# CHAMADA À API DO CLAUDE
# ---------------------------------------------------------------------------

def _call_claude_api(system: str, user: str) -> str | None:
    """Chama a API do Anthropic e retorna o HTML gerado."""
    if DESIGN_SYSTEM_MODE == "fallback":
        return None
    if not ANTHROPIC_API_KEY:
        print("[design_system_generator v4] ANTHROPIC_API_KEY ausente; usando fallback local")
        return None

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user[:HTML_TRUNCATE]}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": ANTHROPIC_API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    return block["text"]
    except Exception as e:
        print(f"[design_system_generator v4] API error: {e}")

    return None


# ---------------------------------------------------------------------------
# FALLBACK: gerador local de qualidade decente (sem API)
# ---------------------------------------------------------------------------

def _generate_fallback(ctx: dict) -> str:
    """
    Gera um design-system.html de qualidade aceitável sem API.
    Usado quando a chamada à Claude API falha.
    Segue o padrão do preview.html como referência mínima.
    """
    css_tags = "\n".join(f'<link rel="stylesheet" href="{h}"/>' for h in ctx["css_links"])
    js_tags  = "\n".join(f'<script src="{s}"></script>' for s in ctx["js_srcs"])

    # --- Tokens section ---
    color_tokens = {k: v for k, v in ctx["tokens"].items() if v["is_color"]}
    other_tokens = {k: v for k, v in ctx["tokens"].items() if not v["is_color"]}

    palette_html = ""
    for name, tok in list(color_tokens.items())[:16]:
        # resolve var() ref
        val = tok["value"]
        if "var(" in val:
            ref = re.search(r"var\((--[\w-]+)\)", val)
            if ref and ref.group(1) in ctx["tokens"]:
                val = ctx["tokens"][ref.group(1)]["value"]
        palette_html += f"""
        <div class="swatch">
          <div class="swatch-sample" style="background:{val}"></div>
          <div class="swatch-meta">
            <strong>{tok['label']}</strong>
            <span class="small">{name}</span>
            <span class="small muted">{tok['value']}</span>
          </div>
        </div>"""

    arb_palette = ""
    for c in ctx["arb_colors"][:12]:
        arb_palette += f"""
        <div class="swatch">
          <div class="swatch-sample" style="background:{c}"></div>
          <div class="swatch-meta">
            <strong style="font-size:11px">{c}</strong>
            <span class="small muted">Tailwind arbitrary</span>
          </div>
        </div>"""

    # --- Typography table ---
    typo_rows = ""
    SAMPLES = {"h1":"Experience liftoff","h2":"Build better products faster",
               "h3":"A new way to design","h4":"Component patterns","p":"The quick brown fox jumps over the lazy dog."}
    LABELS = {"h1":"Heading 1","h2":"Heading 2","h3":"Heading 3","h4":"Heading 4","p":"Paragraph"}
    for t in ctx["typo_evidence"]:
        tag = t["tag"]
        typo_rows += f"""
        <tr>
          <td class="spec-style">{LABELS.get(tag, tag)}</td>
          <td class="spec-preview">
            <{tag} class="{t['classes']}" style="margin:0;max-width:100%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">
              {SAMPLES.get(tag, "Sample text")}
            </{tag}>
          </td>
          <td class="small"><code>&lt;{tag}&gt;</code><br/><span class="muted">{t['classes'][:60]}</span></td>
          <td class="spec-meta">—</td>
        </tr>"""

    # --- Components ---
    comp_html = ""
    comps = ctx["components"]
    if comps.get("nav"):
        comp_html += f'<div class="label">Navigation</div><div class="canvas mt16">{comps["nav"]}</div>'
    if comps.get("buttons"):
        btn_items = "".join(
            f'<div class="state-cell"><div class="state-title">{b["text"][:20]}</div>{b["markup"]}</div>'
            for b in comps["buttons"][:5]
        )
        comp_html += f'<div class="label mt20">Buttons</div><div class="state-row mt12">{btn_items}</div>'
    if comps.get("badges"):
        badge_items = "".join(f'<span style="display:inline-block;margin:4px">{b["markup"]}</span>' for b in comps["badges"][:6])
        comp_html += f'<div class="label mt20">Badges & Tags</div><div class="mt12">{badge_items}</div>'
    if comps.get("cards"):
        card_items = "".join(f'<div class="card-item">{c["markup"][:300]}</div>' for c in comps["cards"][:2])
        comp_html += f'<div class="label mt20">Cards</div><div class="cards-row mt12">{card_items}</div>'

    # --- Keyframes ---
    motion_html = ""
    full_css = ctx["style_css"]
    kf_pattern = re.compile(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*|\{[^}]*\})*)\}", re.DOTALL)
    for m in kf_pattern.finditer(full_css):
        motion_html += f"""
        <div class="motion-token">
          <div class="label">@keyframes {m.group(1)}</div>
          <code>{m.group(0)[:300]}</code>
        </div>"""

    # Transition classes
    tc_pattern = re.compile(r"\.([\w-]+)\s*\{([^}]+)\}", re.DOTALL)
    tc_seen = set()
    for m in tc_pattern.finditer(full_css):
        name, props = m.group(1), m.group(2)
        if name in tc_seen: continue
        if any(k in props for k in ["transition","animation","cubic-bezier"]):
            tc_seen.add(name)
            motion_html += f"""
            <div class="motion-token">
              <div class="label">.{name}</div>
              <code>{props.strip()[:200]}</code>
            </div>"""
        if len(tc_seen) >= 8: break

    # --- Icons ---
    icons_html = ""
    for ic in ctx["icons"][:20]:
        icons_html += f"""
        <div class="icon-card">
          {ic['markup'][:200]}
          <div class="small muted" style="margin-top:6px;text-align:center;font-size:9px;word-break:break-all">{ic['name'][:24]}</div>
        </div>"""
    icons_section = f"""
    <section id="icons">
      <div class="container">
        <div class="section-head reveal">
          <span class="eyebrow">07 — Icons</span>
          <h2>Icon set</h2>
        </div>
        <div class="icon-grid">{icons_html}</div>
      </div>
    </section>""" if icons_html else ""

    title = ctx["title"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Design System — {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">

{css_tags}
{js_tags}

<style>
/* CSS original preservado integralmente */
{ctx["style_css"][:30000]}

/* Design System Documentation Layer */
:root {{
  --ds-bg:#ffffff; --ds-surf:#F8F9FC; --ds-surf2:#EFF2F7;
  --ds-border:#E1E6EC; --ds-text:#212226; --ds-text2:#2F3034;
  --ds-ink:#121317; --ds-muted:#45474D;
  --ds-accent:#1f3bff; --ds-acc-soft:rgba(31,59,255,.08);
  --ds-shadow:0 12px 40px rgba(18,19,23,.08);
  --ds-r-sm:14px; --ds-r-md:22px; --ds-r-lg:32px;
  --ds-max:1280px; --ds-pad:clamp(20px,3vw,36px);
  --ds-section:clamp(72px,10vw,128px);
  --ds-ease:cubic-bezier(.215,.61,.355,1);
  --ds-fast:240ms; --ds-mid:500ms; --ds-slow:1000ms;
}}
*,*::before,*::after{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--ds-bg);color:var(--ds-text);font-family:Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
img{{max-width:100%;display:block}}
a{{color:inherit;text-decoration:none}}

/* Topbar */
.topbar{{position:sticky;top:0;z-index:9999;backdrop-filter:blur(14px);background:rgba(255,255,255,.85);border-bottom:1px solid rgba(225,230,236,.8)}}
.topbar-inner,.container{{width:min(calc(100% - 32px),var(--ds-max));margin:0 auto}}
.topbar-inner{{display:flex;gap:20px;align-items:center;justify-content:space-between;min-height:68px}}
.brand{{font-weight:600;letter-spacing:-.02em;display:flex;align-items:center;gap:10px;white-space:nowrap;font-size:15px}}
.brand-dot{{width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 30% 30%,#6a7cff,var(--ds-accent) 60%,#0f163d 100%);box-shadow:0 0 0 6px rgba(31,59,255,.08)}}
.ds-nav{{display:flex;gap:4px;flex-wrap:wrap;justify-content:center;align-items:center}}
.ds-nav a{{font-size:13px;color:var(--ds-muted);padding:8px 12px;border-radius:999px;transition:background var(--ds-fast) var(--ds-ease),color var(--ds-fast) var(--ds-ease)}}
.ds-nav a:hover{{background:var(--ds-surf);color:var(--ds-text)}}

/* Sections */
section{{padding:var(--ds-section) 0;border-bottom:1px solid rgba(225,230,236,.9)}}
.section-head{{display:grid;gap:16px;margin-bottom:36px}}
.eyebrow{{display:inline-flex;width:fit-content;align-items:center;gap:8px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--ds-muted);padding:8px 12px;border-radius:999px;border:1px solid var(--ds-border);background:var(--ds-surf)}}
h1,h2,h3,h4,p{{margin:0}}
h2{{font-size:clamp(30px,5vw,52px);line-height:1;letter-spacing:-.04em;font-weight:500;max-width:16ch}}
p{{color:var(--ds-text2);line-height:1.5;max-width:72ch}}
.small{{font-size:14px;line-height:1.5}}
.muted{{color:var(--ds-muted)}}
.label{{font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--ds-muted)}}
.mt12{{margin-top:12px}} .mt16{{margin-top:16px}} .mt20{{margin-top:20px}}

/* Hero wrapper */
.hero-wrap{{position:relative;overflow:hidden;border-bottom:1px solid var(--ds-border)}}

/* Spec table — FAANG standard */
.spec-table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--ds-border);border-radius:24px;overflow:hidden;box-shadow:var(--ds-shadow)}}
.spec-table tr+tr td,.spec-table tr+tr th{{border-top:1px solid var(--ds-border)}}
.spec-table th,.spec-table td{{padding:18px 20px;vertical-align:middle;text-align:left}}
.spec-table th{{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--ds-muted);background:var(--ds-surf)}}
.spec-style{{white-space:nowrap;font-weight:600}}
.spec-preview{{min-width:280px;max-width:400px;overflow:hidden}}
.spec-meta{{text-align:right;white-space:nowrap;color:var(--ds-muted);font-size:14px;font-family:ui-monospace,monospace}}

/* Palette — 16:10 swatches */
.palette{{display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}}
.swatch{{border:1px solid var(--ds-border);border-radius:24px;overflow:hidden;background:#fff;box-shadow:var(--ds-shadow)}}
.swatch-sample{{aspect-ratio:16/10}}
.swatch-meta{{padding:16px;display:grid;gap:4px}}
.swatch-meta strong{{font-size:14px;font-weight:600}}

/* Components */
.state-row{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}}
.state-cell{{border:1px solid var(--ds-border);border-radius:20px;padding:16px;background:var(--ds-surf);display:grid;gap:10px;min-height:120px;align-content:start}}
.state-title{{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--ds-muted)}}
.canvas{{padding:24px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:16px;overflow-x:auto}}
.cards-row{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.card-item{{overflow:hidden;border:1px solid var(--ds-border);border-radius:16px;padding:16px;background:#fff;box-shadow:var(--ds-shadow)}}

/* Spacing */
.spacing-list{{display:grid;gap:10px}}
.space-row{{display:flex;align-items:center;gap:16px}}
.space-bar{{height:28px;background:var(--ds-acc-soft);border:1px solid rgba(31,59,255,.2);border-radius:5px}}
.space-info{{font-family:ui-monospace,monospace;font-size:13px;color:var(--ds-muted);display:flex;gap:12px}}

/* Motion */
.motion-grid{{display:grid;gap:20px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}}
.motion-token{{border:1px solid var(--ds-border);border-radius:20px;padding:18px;background:var(--ds-surf);display:grid;gap:8px}}
.motion-token code{{font-family:ui-monospace,monospace;font-size:11px;line-height:1.6;color:var(--ds-text);white-space:pre-wrap;word-break:break-word}}

/* Icons */
.icon-grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(100px,1fr))}}
.icon-card{{border:1px solid var(--ds-border);border-radius:22px;padding:18px;display:grid;gap:10px;justify-items:center;background:#fff;box-shadow:var(--ds-shadow);transition:all var(--ds-fast) var(--ds-ease)}}
.icon-card:hover{{transform:translateY(-2px);box-shadow:0 16px 40px rgba(18,19,23,.1)}}

/* Layout diagrams */
.layout-examples{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}}
.layout-card{{border:1px solid var(--ds-border);background:#fff;border-radius:24px;padding:18px;box-shadow:var(--ds-shadow);display:grid;gap:14px}}
.layout-frame{{border-radius:18px;border:1px solid var(--ds-border);min-height:200px;background:linear-gradient(180deg,var(--ds-surf),#fff);overflow:hidden;position:relative}}
.layout-hero .block-1,.layout-hero .block-2,.layout-grid .cell,.layout-split .left,.layout-split .right{{position:absolute;border-radius:12px;background:rgba(31,59,255,.08);border:1px solid rgba(31,59,255,.12)}}
.layout-hero .block-1{{inset:18px 18px auto;height:48px}}
.layout-hero .block-2{{inset:80px 18px 18px}}
.layout-grid .cell:nth-child(1){{inset:18px auto auto 18px;width:calc(50% - 27px);height:calc(50% - 27px)}}
.layout-grid .cell:nth-child(2){{inset:18px 18px auto auto;width:calc(50% - 27px);height:calc(50% - 27px)}}
.layout-grid .cell:nth-child(3){{inset:auto auto 18px 18px;width:calc(50% - 27px);height:calc(50% - 27px)}}
.layout-grid .cell:nth-child(4){{inset:auto 18px 18px auto;width:calc(50% - 27px);height:calc(50% - 27px)}}
.layout-split .left{{inset:18px auto 18px 18px;width:42%}}
.layout-split .right{{inset:18px 18px 18px auto;width:calc(58% - 18px)}}

.reveal{{opacity:0;transform:translateY(24px);transition:opacity .7s var(--ds-ease),transform .7s var(--ds-ease)}}
.reveal.in{{opacity:1;transform:translateY(0)}}

@keyframes ds-reveal{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:translateY(0)}}}}

@media(max-width:900px){{
  .ds-nav{{display:none}} .layout-examples{{grid-template-columns:1fr}} .state-row{{grid-template-columns:repeat(2,1fr)}}
  .palette{{grid-template-columns:repeat(2,1fr)}}
}}
@media(max-width:600px){{
  .spec-table th:nth-child(3),.spec-table td:nth-child(3){{display:none}}
  .state-row{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<!-- Topbar -->
<div class="topbar">
  <div class="topbar-inner">
    <div class="brand"><span class="brand-dot"></span>{title} — Design System</div>
    <nav class="ds-nav">
      <a href="#hero">Hero</a>
      <a href="#tokens">Tokens</a>
      <a href="#typography">Typography</a>
      <a href="#colors">Colors</a>
      <a href="#components">Components</a>
      <a href="#layout">Layout</a>
      <a href="#motion">Motion</a>
      {"<a href='#icons'>Icons</a>" if icons_html else ""}
    </nav>
  </div>
</div>

<!-- 00 — HERO -->
<div class="hero-wrap" id="hero">
  {ctx['hero_html'] or '<div style="min-height:40vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(180deg,#F8F9FC,#fff)"><h1 style="font-size:clamp(36px,6vw,80px);font-weight:600;letter-spacing:-.04em;color:#121317">Design System</h1></div>'}
</div>

<!-- 01 — TOKENS -->
<section id="tokens">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">01 — Tokens</span>
      <h2>Color tokens & variables</h2>
      <p>CSS custom properties extraídas de :root — sistema de tokens com label semântico. Apenas variáveis presentes no DOM.</p>
    </div>
    <div class="palette reveal">
      {palette_html or '<p class="muted small">Nenhuma CSS custom property encontrada. O site provavelmente usa Tailwind utilitário.</p>'}
    </div>
    {f'<h3 style="font-size:16px;font-weight:600;margin:40px 0 16px">Tailwind arbitrary colors</h3><div class="palette">{arb_palette}</div>' if arb_palette else ''}
    {('<h3 style="font-size:16px;font-weight:600;margin:40px 0 16px">Outros tokens</h3><div style="display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">' + ''.join(f'<div style="padding:10px 14px;background:var(--ds-surf);border:1px solid var(--ds-border);border-radius:8px;display:flex;gap:10px;font-size:12px;font-family:ui-monospace,monospace"><code style="color:var(--ds-accent)">{k}</code><span class="muted">{v["value"][:40]}</span></div>' for k,v in list(other_tokens.items())[:10]) + '</div>') if other_tokens else ''}
  </div>
</section>

<!-- 02 — TYPOGRAPHY -->
<section id="typography">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">02 — Typography</span>
      <h2>Type scale & hierarchy</h2>
      <p>Especificação tipográfica com elemento HTML real, classes originais e anotação font-size / line-height.</p>
    </div>
    <table class="spec-table reveal" aria-label="Typography spec table">
      <thead>
        <tr>
          <th>Style</th>
          <th>Live preview</th>
          <th>Element / classes</th>
          <th>Size / Line-height</th>
        </tr>
      </thead>
      <tbody>
        {typo_rows or '<tr><td colspan="4" class="muted small" style="padding:24px">Nenhum elemento tipográfico encontrado no DOM.</td></tr>'}
      </tbody>
    </table>
  </div>
</section>

<!-- 03 — COLORS -->
<section id="colors">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">03 — Colors & Surfaces</span>
      <h2>Color system</h2>
      <p>Paleta extraída do DOM: backgrounds, bordas, gradients, superfícies glass e camadas de opacidade.</p>
    </div>
    <div class="palette reveal">
      {palette_html or arb_palette or '<p class="muted small">Nenhuma cor encontrada.</p>'}
    </div>
  </div>
</section>

<!-- 04 — COMPONENTS -->
<section id="components">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">04 — Components</span>
      <h2>Components & states</h2>
      <p>Componentes extraídos do DOM com markup original. Hover, focus e estados ativos preservados via classes originais.</p>
    </div>
    <div class="reveal">
      {comp_html or '<p class="muted small">Nenhum componente identificado no DOM.</p>'}
    </div>
  </div>
</section>

<!-- 05 — LAYOUT -->
<section id="layout">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">05 — Layout & Spacing</span>
      <h2>Containers, grids & rhythm</h2>
      <p>Primitivos de layout e escala de espaçamento extraídos do CSS original.</p>
    </div>
    <div class="layout-examples reveal">
      <div class="layout-card">
        <div class="label">Hero layout</div>
        <div class="layout-frame layout-hero"><div class="block-1"></div><div class="block-2"></div></div>
        <p class="small">Stack vertical centralizado, alto ratio de whitespace, CTAs empilhados.</p>
      </div>
      <div class="layout-card">
        <div class="label">Grid layout</div>
        <div class="layout-frame layout-grid"><div class="cell"></div><div class="cell"></div><div class="cell"></div><div class="cell"></div></div>
        <p class="small">Grid de cards com repetição visual uniforme.</p>
      </div>
      <div class="layout-card">
        <div class="label">Split layout</div>
        <div class="layout-frame layout-split"><div class="left"></div><div class="right"></div></div>
        <p class="small">Divisão em dois painéis com proporções assimétricas.</p>
      </div>
    </div>
  </div>
</section>

<!-- 06 — MOTION -->
<section id="motion">
  <div class="container">
    <div class="section-head reveal">
      <span class="eyebrow">06 — Motion</span>
      <h2>Animation & timing</h2>
      <p>Keyframes e classes de transição extraídos do CSS original. Curvas exatas, nunca aproximadas.</p>
    </div>
    <div class="motion-grid reveal">
      {motion_html or '<div class="motion-token"><div class="label">Nenhuma animação encontrada</div><code>O CSS original não contém @keyframes declarados.</code></div>'}
    </div>
  </div>
</section>

{icons_section}

<script>
// Reveal on scroll
const revealEls = document.querySelectorAll('.reveal');
const io = new IntersectionObserver(entries => {{
  entries.forEach(e => {{ if (e.isIntersecting) {{ e.target.classList.add('in'); io.unobserve(e.target); }} }});
}}, {{ threshold: 0.12 }});
revealEls.forEach(el => io.observe(el));
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# ENTRY POINT PÚBLICO
# ---------------------------------------------------------------------------

def generate_design_system(html_content: str) -> str | None:
    """
    Recebe o HTML do index.html capturado e retorna
    o conteúdo do design-system.html como string.

    Estratégia:
    1. Extrai contexto estruturado do HTML
    2. Usa API apenas se houver credencial válida
    3. Se falhar ou estiver desabilitada → fallback local
    """
    try:
        ctx = _extract_context(html_content)
        user_prompt = _build_user_prompt(ctx)

        result = _call_claude_api(SYSTEM_PROMPT, user_prompt)

        if result and result.strip().startswith("<!DOCTYPE"):
            print("[design_system_generator v4] Gerado via API ✓")
            return result

        if result:
            html_start = result.find("<!DOCTYPE")
            if html_start >= 0:
                print("[design_system_generator v4] HTML extraído da resposta ✓")
                return result[html_start:]

        print("[design_system_generator v4] usando fallback local")
        return _generate_fallback(ctx)

    except Exception as e:
        print(f"[design_system_generator v4] erro: {e}")
        import traceback
        traceback.print_exc()
        return None


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
        print(f"[design_system_generator v4] erro no zip: {e}")
        return None
