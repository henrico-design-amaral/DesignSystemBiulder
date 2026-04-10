from __future__ import annotations

import html
import json
import os
import re
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 6000
DESIGN_SYSTEM_MODE = os.getenv("DESIGN_SYSTEM_MODE", "local").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

TOKEN_PATTERN = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
KEYFRAME_BLOCK_PATTERN = re.compile(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]+|\{[^{}]*\})*\}", re.DOTALL)
MEDIA_PATTERN = re.compile(r"@media\s*\((min|max)-width\s*:\s*([^\)]+)\)", re.IGNORECASE)
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^\)]+\)|hsla?\([^\)]+\)|oklch\([^\)]+\)|color\([^\)]+\)")
TRANSITION_PATTERN = re.compile(r"transition[^;]*:[^;]+;|animation[^;]*:[^;]+;|cubic-bezier\([^\)]+\)", re.IGNORECASE)

SEMANTIC_MAP = [
    (r"brand|primary|accent|highlight", "Brand Primary"),
    (r"secondary", "Secondary"),
    (r"plum|purple|violet|indigo", "Brand Purple"),
    (r"gold|amber|yellow", "Accent Gold"),
    (r"rose|pink|red|danger|error", "Danger"),
    (r"green|success|emerald|teal", "Success"),
    (r"blue|sky|cyan|info", "Info"),
    (r"ink|text|fg|foreground|copy", "Text"),
    (r"muted|soft|dim|subtle|faint", "Text Muted"),
    (r"bg|background|surface|canvas|base", "Background"),
    (r"card|panel|layer|glass", "Surface Card"),
    (r"stone|neutral|gray|grey|slate", "Neutral"),
    (r"border|divider|separator|stroke", "Border"),
    (r"white", "White"),
    (r"black", "Black"),
    (r"shadow|overlay|scrim", "Shadow Overlay"),
]


def _infer_label(var_name: str) -> str:
    name = var_name.strip("-").lower()
    for pattern, label in SEMANTIC_MAP:
        if re.search(pattern, name):
            return label
    return var_name.strip("-").replace("-", " ").title()



def _call_claude_api(system: str, user: str) -> str | None:
    if DESIGN_SYSTEM_MODE not in {"auto", "llm"} or not ANTHROPIC_API_KEY:
        return None

    payload = json.dumps(
        {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user[:120000]}],
        }
    ).encode("utf-8")

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
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text")
    except Exception:
        return None
    return None



def _collect_css_sources(bundle: dict) -> tuple[list[str], list[str]]:
    asset_root = Path(bundle.get("asset_root") or ".")
    resources = bundle.get("resources", {})
    css_links: list[str] = []
    css_chunks: list[str] = []

    for resource in resources.values():
        local_path = resource.get("local_path", "")
        content_type = (resource.get("content_type") or "").lower()
        if local_path.endswith(".css") or "text/css" in content_type:
            css_links.append(local_path)
            try:
                css_chunks.append((asset_root / Path(local_path).name).read_text(encoding="utf-8", errors="ignore"))
            except FileNotFoundError:
                try:
                    css_chunks.append((asset_root.parent / local_path).read_text(encoding="utf-8", errors="ignore"))
                except FileNotFoundError:
                    continue

    for page in bundle.get("pages", {}).values():
        soup = BeautifulSoup(page.get("html", ""), "html.parser")
        css_chunks.extend(tag.string or "" for tag in soup.find_all("style") if tag.string)

    return css_links, css_chunks



def _extract_context(bundle: dict) -> dict:
    pages = bundle.get("pages", {})
    css_links, css_chunks = _collect_css_sources(bundle)
    all_css = "\n\n".join(css_chunks)

    title = "Site"
    root_page = pages.get(bundle.get("root_url", ""))
    if root_page and root_page.get("title"):
        title = root_page["title"]
    elif pages:
        title = next(iter(pages.values())).get("title") or title

    soup_all = BeautifulSoup("\n".join(page.get("html", "") for page in pages.values()), "html.parser")
    body_root = BeautifulSoup(root_page.get("html", "") if root_page else "", "html.parser")

    tokens = {}
    for name, value in TOKEN_PATTERN.findall(all_css):
        if name not in tokens:
            clean_value = value.strip()
            tokens[name] = {
                "value": clean_value,
                "label": _infer_label(name),
                "is_color": bool(COLOR_PATTERN.search(clean_value)),
            }

    colors = []
    seen_colors = set()
    for match in COLOR_PATTERN.finditer(all_css):
        color = match.group(0).strip()
        if color.lower() not in seen_colors:
            seen_colors.add(color.lower())
            colors.append(color)
        if len(colors) >= 40:
            break

    keyframes = []
    for block in KEYFRAME_BLOCK_PATTERN.findall(all_css):
        if block not in keyframes:
            keyframes.append(block)
        if len(keyframes) >= 16:
            break

    transitions = []
    seen_transitions = set()
    for match in TRANSITION_PATTERN.finditer(all_css):
        value = match.group(0).strip()
        key = value[:220]
        if key not in seen_transitions:
            seen_transitions.add(key)
            transitions.append(value)
        if len(transitions) >= 16:
            break

    breakpoints = []
    seen_bp = set()
    for bound, value in MEDIA_PATTERN.findall(all_css):
        bp = f"{bound}-width: {value.strip()}"
        if bp not in seen_bp:
            seen_bp.add(bp)
            breakpoints.append(bp)
        if len(breakpoints) >= 12:
            break

    font_families = []
    for match in re.finditer(r"font-family\s*:\s*([^;}{]+)", all_css):
        value = match.group(1).strip()
        if value not in font_families:
            font_families.append(value)
        if len(font_families) >= 10:
            break

    typography = []
    samples = {
        "h1": "Heading one sample",
        "h2": "Heading two sample",
        "h3": "Heading three sample",
        "h4": "Heading four sample",
        "p": "The quick brown fox jumps over the lazy dog.",
        "a": "Link sample",
        "button": "Button sample",
        "span": "Label sample",
    }
    for tag_name in ["h1", "h2", "h3", "h4", "p", "a", "button", "span"]:
        for el in soup_all.find_all(tag_name):
            classes = " ".join(el.get("class", []))
            text = el.get_text(" ", strip=True)
            if not classes and not text:
                continue
            typography.append(
                {
                    "style": tag_name.upper() if tag_name.startswith("h") else tag_name.capitalize(),
                    "tag": tag_name,
                    "classes": classes,
                    "sample": samples.get(tag_name, text[:60] or "Sample"),
                    "source_text": text[:90],
                }
            )
            break

    components = []
    seen_components = set()
    component_queries = [
        ("Navigation", lambda s: s.find("nav")),
        ("Primary button", lambda s: s.find("button")),
        ("Link / CTA", lambda s: s.find("a", href=True)),
        (
            "Card / tile",
            lambda s: next(
                (
                    el
                    for el in s.find_all(["div", "article", "a"])
                    if any(k in " ".join(el.get("class", [])).lower() for k in ["card", "tile", "project", "case", "feature"])
                    and len(str(el)) < 2500
                ),
                None,
            ),
        ),
        (
            "Hero block",
            lambda s: next(
                (
                    el
                    for el in s.find_all(["section", "header", "main", "div"])
                    if len(el.get_text(" ", strip=True)) > 120 and len(str(el)) < 3500
                ),
                None,
            ),
        ),
    ]
    for label, finder in component_queries:
        node = finder(body_root or soup_all)
        if node is None:
            continue
        markup = str(node)
        key = re.sub(r"\s+", " ", markup[:220])
        if key in seen_components:
            continue
        seen_components.add(key)
        components.append({"label": label, "markup": markup[:4000]})

    class_counter = Counter()
    for el in soup_all.find_all(class_=True):
        for cls in el.get("class", []):
            class_counter[cls] += 1

    nav_links = []
    for a in body_root.find_all("a", href=True)[:12] if body_root else []:
        text = a.get_text(" ", strip=True)
        href = a.get("href")
        if text and href:
            nav_links.append({"text": text[:60], "href": href})

    manifest = bundle.get("manifest", {})
    return {
        "title": title,
        "css_links": css_links,
        "all_css": all_css,
        "tokens": tokens,
        "colors": colors,
        "keyframes": keyframes,
        "transitions": transitions,
        "breakpoints": breakpoints,
        "font_families": font_families,
        "typography": typography,
        "components": components,
        "pages": manifest.get("pages", []),
        "nav_links": nav_links,
        "top_classes": class_counter.most_common(18),
        "root_markup": root_page.get("html", "") if root_page else "",
        "page_count": manifest.get("page_count", len(pages)),
        "asset_count": manifest.get("asset_count", len(bundle.get("resources", {}))),
        "unresolved": manifest.get("unresolved_urls", []),
    }



def _render_local_design_system(ctx: dict) -> str:
    css_tags = "\n".join(f'<link rel="stylesheet" href="{html.escape(h)}">' for h in ctx["css_links"])
    token_rows = []
    for name, meta in list(ctx["tokens"].items())[:120]:
        swatch = (
            f'<span class="ds-swatch" style="background:{html.escape(meta["value"])}"></span>'
            if meta["is_color"]
            else '<span class="ds-swatch ds-empty"></span>'
        )
        token_rows.append(
            f"<tr><td><code>{html.escape(name)}</code></td><td>{html.escape(meta['label'])}</td>"
            f"<td>{swatch}<code>{html.escape(meta['value'])}</code></td></tr>"
        )
    if not token_rows:
        token_rows.append('<tr><td colspan="3">Nenhum token CSS custom property detectado.</td></tr>')

    color_cards = []
    for color in ctx["colors"][:32]:
        color_cards.append(
            f"<div class='ds-color-card'><div class='ds-color-sample' style='background:{html.escape(color)}'></div>"
            f"<div class='ds-color-meta'><strong>{html.escape(color)}</strong><span>literal encontrado no CSS</span></div></div>"
        )
    if not color_cards:
        color_cards.append("<div class='ds-empty-block'>Nenhuma cor literal detectada.</div>")

    typo_rows = []
    for item in ctx["typography"]:
        tag = item["tag"]
        classes = item["classes"]
        klass = f' class="{html.escape(classes)}"' if classes else ""
        sample = html.escape(item["sample"])
        preview = f"<{tag}{klass}>{sample}</{tag}>"
        typo_rows.append(
            f"<tr><td>{html.escape(item['style'])}</td><td><div class='ds-preview'>{preview}</div></td>"
            f"<td><code>&lt;{tag}&gt;</code></td><td><code>{html.escape(classes or '—')}</code></td></tr>"
        )
    if not typo_rows:
        typo_rows.append('<tr><td colspan="4">Sem evidência tipográfica suficiente nas páginas capturadas.</td></tr>')

    component_cards = []
    for item in ctx["components"][:6]:
        component_cards.append(
            f"<article class='ds-component-card'><header><strong>{html.escape(item['label'])}</strong></header>"
            f"<div class='ds-component-preview'>{item['markup']}</div>"
            f"<details><summary>Markup</summary><pre>{html.escape(item['markup'])}</pre></details></article>"
        )
    if not component_cards:
        component_cards.append("<div class='ds-empty-block'>Nenhum componente estrutural foi isolado.</div>")

    motion_blocks = []
    for block in ctx["keyframes"][:12]:
        motion_blocks.append(f"<pre>{html.escape(block)}</pre>")
    for tr in ctx["transitions"][:12]:
        motion_blocks.append(f"<pre>{html.escape(tr)}</pre>")
    if not motion_blocks:
        motion_blocks.append("<div class='ds-empty-block'>Nenhuma evidência de motion detectada.</div>")

    pages_rows = []
    for page in ctx["pages"]:
        pages_rows.append(
            f"<tr><td><code>{html.escape(page.get('local_path', ''))}</code></td>"
            f"<td>{html.escape(page.get('title', ''))}</td><td><code>{html.escape(page.get('url', ''))}</code></td></tr>"
        )
    if not pages_rows:
        pages_rows.append('<tr><td colspan="3">Nenhuma página listada no manifesto.</td></tr>')

    class_rows = []
    for cls, count in ctx["top_classes"]:
        class_rows.append(f"<tr><td><code>.{html.escape(cls)}</code></td><td>{count}</td></tr>")
    if not class_rows:
        class_rows.append('<tr><td colspan="2">Nenhuma classe recorrente identificada.</td></tr>')

    breakpoint_items = "".join(f"<li><code>{html.escape(bp)}</code></li>" for bp in ctx["breakpoints"]) or "<li>Sem media queries detectadas.</li>"
    font_items = "".join(f"<li><code>{html.escape(font)}</code></li>" for font in ctx["font_families"]) or "<li>Sem fontes detectadas.</li>"
    unresolved_list = "".join(f"<li><code>{html.escape(u)}</code></li>" for u in ctx["unresolved"][:50]) or "<li>Sem referências remotas pendentes.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design System — {html.escape(ctx['title'])}</title>
{css_tags}
<style>
:root {{
  --ds-bg:#f5f7fb;
  --ds-surface:#ffffff;
  --ds-surface-2:#eef2f8;
  --ds-border:#d9e0ea;
  --ds-text:#10131a;
  --ds-muted:#516072;
  --ds-accent:#245bff;
  --ds-shadow:0 12px 40px rgba(16,19,26,.08);
  --ds-radius:18px;
  --ds-radius-sm:12px;
  --ds-max:1280px;
}}
html,body {{ margin:0; padding:0; background:var(--ds-bg); color:var(--ds-text); font-family:Inter, system-ui, sans-serif; }}
a {{ color:inherit; }}
.ds-doc * {{ box-sizing:border-box; }}
.ds-doc {{ width:min(100% - 32px, var(--ds-max)); margin:0 auto; padding:32px 0 80px; }}
.ds-hero {{ background:linear-gradient(180deg, #fff 0%, #f7f9fc 100%); border:1px solid var(--ds-border); border-radius:28px; padding:32px; box-shadow:var(--ds-shadow); margin-bottom:24px; }}
.ds-hero h1 {{ margin:0 0 12px; font-size:clamp(32px, 4vw, 54px); line-height:1; letter-spacing:-.04em; }}
.ds-hero p {{ margin:0; color:var(--ds-muted); max-width:900px; line-height:1.65; }}
.ds-grid {{ display:grid; gap:20px; grid-template-columns:repeat(12, minmax(0,1fr)); }}
.ds-panel {{ grid-column:span 12; background:var(--ds-surface); border:1px solid var(--ds-border); border-radius:var(--ds-radius); box-shadow:var(--ds-shadow); padding:24px; overflow:hidden; }}
.ds-panel.half {{ grid-column:span 6; }}
.ds-panel.third {{ grid-column:span 4; }}
@media (max-width: 980px) {{ .ds-panel.half, .ds-panel.third {{ grid-column:span 12; }} }}
.ds-eyebrow {{ display:inline-block; font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--ds-accent); margin-bottom:10px; font-weight:700; }}
.ds-panel h2 {{ margin:0 0 12px; font-size:24px; letter-spacing:-.03em; }}
.ds-panel p.lead {{ margin:0 0 16px; color:var(--ds-muted); line-height:1.65; }}
.ds-metrics {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:18px; }}
.ds-metric {{ border:1px solid var(--ds-border); background:var(--ds-surface-2); border-radius:999px; padding:10px 14px; font-size:13px; }}
.ds-table {{ width:100%; border-collapse:collapse; }}
.ds-table th, .ds-table td {{ border-top:1px solid var(--ds-border); padding:12px 10px; vertical-align:top; text-align:left; font-size:13px; }}
.ds-table th {{ color:var(--ds-muted); font-size:11px; letter-spacing:.12em; text-transform:uppercase; }}
.ds-table code {{ font-family:ui-monospace, SFMono-Regular, monospace; font-size:12px; word-break:break-word; }}
.ds-swatch {{ width:14px; height:14px; border-radius:999px; display:inline-block; vertical-align:middle; margin-right:8px; border:1px solid rgba(0,0,0,.08); }}
.ds-swatch.ds-empty {{ background:repeating-linear-gradient(45deg, #f1f4f8, #f1f4f8 4px, #e2e8f0 4px, #e2e8f0 8px); }}
.ds-colors {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:14px; }}
.ds-color-card {{ border:1px solid var(--ds-border); border-radius:16px; overflow:hidden; background:var(--ds-surface); }}
.ds-color-sample {{ aspect-ratio:16/10; width:100%; }}
.ds-color-meta {{ padding:12px; display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--ds-muted); }}
.ds-preview {{ padding:16px; border:1px dashed var(--ds-border); border-radius:12px; background:#fff; min-width:240px; }}
.ds-component-list {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }}
.ds-component-card {{ border:1px solid var(--ds-border); border-radius:16px; background:var(--ds-surface); overflow:hidden; }}
.ds-component-card header {{ padding:14px 16px; border-bottom:1px solid var(--ds-border); font-size:13px; }}
.ds-component-preview {{ padding:16px; max-height:420px; overflow:auto; background:#fff; }}
.ds-component-card details {{ border-top:1px solid var(--ds-border); padding:12px 16px; }}
.ds-component-card pre, .ds-motion pre {{ white-space:pre-wrap; word-break:break-word; margin:0; font-size:11px; line-height:1.5; font-family:ui-monospace, monospace; }}
.ds-motion {{ display:grid; gap:12px; }}
.ds-empty-block {{ border:1px dashed var(--ds-border); background:var(--ds-surface-2); border-radius:16px; padding:20px; color:var(--ds-muted); }}
.ds-lists {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
@media (max-width: 760px) {{ .ds-lists {{ grid-template-columns:1fr; }} }}
.ds-lists ul {{ margin:0; padding-left:18px; color:var(--ds-muted); line-height:1.6; }}
.ds-footer-note {{ margin-top:24px; color:var(--ds-muted); font-size:12px; }}
</style>
</head>
<body>
<div class="ds-doc">
  <section class="ds-hero">
    <span class="ds-eyebrow">Design System Evidence Pack</span>
    <h1>{html.escape(ctx['title'])}</h1>
    <p>This document is derived from the captured pages, local CSS and captured assets. It documents what was actually found in the exported site bundle, not an invented redesign.</p>
    <div class="ds-metrics">
      <div class="ds-metric">Pages captured: <strong>{ctx['page_count']}</strong></div>
      <div class="ds-metric">Assets captured: <strong>{ctx['asset_count']}</strong></div>
      <div class="ds-metric">CSS vars: <strong>{len(ctx['tokens'])}</strong></div>
      <div class="ds-metric">Literal colors: <strong>{len(ctx['colors'])}</strong></div>
      <div class="ds-metric">Keyframes: <strong>{len(ctx['keyframes'])}</strong></div>
      <div class="ds-metric">Pending remotes: <strong>{len(ctx['unresolved'])}</strong></div>
    </div>
  </section>

  <section class="ds-grid">
    <article class="ds-panel half">
      <span class="ds-eyebrow">01 — Tokens</span>
      <h2>CSS custom properties</h2>
      <p class="lead">Exact custom properties extracted from the captured CSS set.</p>
      <table class="ds-table"><thead><tr><th>Name</th><th>Label</th><th>Value</th></tr></thead><tbody>{''.join(token_rows)}</tbody></table>
    </article>

    <article class="ds-panel half">
      <span class="ds-eyebrow">02 — Structure</span>
      <h2>Coverage and layout evidence</h2>
      <div class="ds-lists">
        <div>
          <h3>Breakpoints</h3>
          <ul>{breakpoint_items}</ul>
        </div>
        <div>
          <h3>Font families</h3>
          <ul>{font_items}</ul>
        </div>
      </div>
      <div class="ds-footer-note">Top recurring classes are listed below to show where the visual system concentrates its selectors.</div>
      <table class="ds-table"><thead><tr><th>Class</th><th>Occurrences</th></tr></thead><tbody>{''.join(class_rows)}</tbody></table>
    </article>

    <article class="ds-panel">
      <span class="ds-eyebrow">03 — Colors</span>
      <h2>Literal color evidence</h2>
      <p class="lead">These swatches come from exact CSS values detected in the local bundle.</p>
      <div class="ds-colors">{''.join(color_cards)}</div>
    </article>

    <article class="ds-panel">
      <span class="ds-eyebrow">04 — Typography</span>
      <h2>Type styles in context</h2>
      <table class="ds-table"><thead><tr><th>Style</th><th>Live preview</th><th>Element</th><th>Classes</th></tr></thead><tbody>{''.join(typo_rows)}</tbody></table>
    </article>

    <article class="ds-panel">
      <span class="ds-eyebrow">05 — Components</span>
      <h2>Recovered structural components</h2>
      <p class="lead">Previews use markup recovered from the captured pages and the original local CSS assets.</p>
      <div class="ds-component-list">{''.join(component_cards)}</div>
    </article>

    <article class="ds-panel half">
      <span class="ds-eyebrow">06 — Motion</span>
      <h2>Animations and transitions</h2>
      <div class="ds-motion">{''.join(motion_blocks)}</div>
    </article>

    <article class="ds-panel half">
      <span class="ds-eyebrow">07 — Pages</span>
      <h2>Local route map</h2>
      <table class="ds-table"><thead><tr><th>Local path</th><th>Title</th><th>Original URL</th></tr></thead><tbody>{''.join(pages_rows)}</tbody></table>
    </article>

    <article class="ds-panel">
      <span class="ds-eyebrow">08 — Integrity</span>
      <h2>Remaining unresolved remote references</h2>
      <p class="lead">This is the explicit gap list. If this section is empty, the local package is substantially more self-contained.</p>
      <ul>{unresolved_list}</ul>
    </article>
  </section>
</div>
</body>
</html>"""



def generate_design_system(source) -> str:
    if isinstance(source, str):
        bundle = {
            "root_url": "https://captured.local",
            "pages": {"https://captured.local": {"local_path": "index.html", "html": source, "title": "Captured page"}},
            "resources": {},
            "manifest": {"pages": [{"local_path": "index.html", "url": "https://captured.local", "title": "Captured page"}]},
        }
    else:
        bundle = source

    ctx = _extract_context(bundle)

    if DESIGN_SYSTEM_MODE in {"auto", "llm"} and ANTHROPIC_API_KEY:
        llm_html = _call_claude_api(
            "You transform structured site evidence into a rigorous design-system.html document. Output HTML only.",
            json.dumps(
                {
                    "title": ctx["title"],
                    "page_count": ctx["page_count"],
                    "asset_count": ctx["asset_count"],
                    "tokens": ctx["tokens"],
                    "colors": ctx["colors"],
                    "keyframes": ctx["keyframes"],
                    "breakpoints": ctx["breakpoints"],
                    "typography": ctx["typography"],
                    "pages": ctx["pages"],
                    "top_classes": ctx["top_classes"],
                    "unresolved": ctx["unresolved"],
                },
                ensure_ascii=False,
            ),
        )
        if llm_html and llm_html.lstrip().startswith("<!DOCTYPE html>"):
            return llm_html

    return _render_local_design_system(ctx)
