from __future__ import annotations

import html
import json
import os
import re
import urllib.request
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8000
DESIGN_SYSTEM_MODE = os.getenv("DESIGN_SYSTEM_MODE", "local").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

TOKEN_PATTERN = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
KEYFRAME_BLOCK_PATTERN = re.compile(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]+|\{[^{}]*\})*\}", re.DOTALL)
MEDIA_PATTERN = re.compile(r"@media\s*\((min|max)-width\s*:\s*([^\)]+)\)", re.IGNORECASE)
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^\)]+\)|hsla?\([^\)]+\)|oklch\([^\)]+\)|color\([^\)]+\)")
TRANSITION_PATTERN = re.compile(r"transition[^;]*:[^;]+;|animation[^;]*:[^;]+;|cubic-bezier\([^\)]+\)", re.IGNORECASE)
CSS_RULE_PATTERN = re.compile(r"([^{}@]+)\{([^{}]+)\}")
DECL_PATTERN = re.compile(r"([\w-]+)\s*:\s*([^;}{]+)")
GRADIENT_PATTERN = re.compile(r"(?:linear|radial|conic)-gradient\([^\)]+\)", re.IGNORECASE)

STYLE_ORDER = [
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "Heading 4",
    "Bold L",
    "Bold M",
    "Bold S",
    "Paragraph",
    "Regular L",
    "Regular M",
    "Regular S",
]

PROMPT_TEXT = """You are a Design System Showcase Builder.

You are given a reference website HTML:
$ARGUMENTS

Your task is to create one new intermediate HTML file that acts as a living design system + pattern library for this exact design.

GOAL

Generate one single file called: design-system.html and place it in the same folder of the html file.

This file must preserve the exact look & behavior of the reference design by reusing the original HTML, CSS classes, animations, keyframes, transitions, effects, and layout patterns — not approximations.

HARD RULES (NON-NEGOTIABLE)

Do not redesign or invent new styles.
Reuse exact class names, animations, timing, easing, hover/focus states.
Reference the same CSS/JS assets used by the original.
If a style/component is not used in the reference HTML, do not add it.
The file must be self-explanatory by structure (sections = documentation).
Include a top horizontal nav with anchor links to each section.

OBJECTIVE

Build a single page composed of canonical examples of the design system, organized in sections.

Hero (Exact Clone, Text Adapted)

The first section MUST be a direct clone of the original Hero:

• Same HTML structure
• Same class names
• Same layout
• Same images and components
• Same animations and interactions
• Same buttons and background
• Same UI components (if any)

Allowed change (only this):

• Replace the hero text content to present the Design System
• Keep similar text length and hierarchy

Forbidden:

• Do not change layout, spacing, alignment, or animations
• Do not add or remove elements

Typography

Create a Typography section rendered as a spec table / vertical list.

Each row MUST contain:

• Style name (e.g. “Heading 1”, “Bold M”)
• Live text preview using the exact original HTML element and CSS classes
• Font size / line-height label aligned right (format: 40px / 48px)

Include ONLY styles that exist in the reference HTML, in this order:

• Heading 1
• Heading 2
• Heading 3
• Heading 4
• Bold L / Bold M / Bold S
• Paragraph (larger body, if exists)
• Regular L / Regular M / Regular S

Rules:

• No inline styles
• No normalization
• Typography, colors, spacing, and gradients MUST come from original CSS
• If a style uses gradient text, show it exactly the same
• If a style does not exist, DO NOT include it

This section must communicate hierarchy, scale, and rhythm at a glance.

Colors & Surfaces

• Backgrounds (page, section, card, glass/blur if exists)
• Borders, dividers, overlays
• Gradients (as swatches + usage context)

UI Components

• Buttons, inputs, cards, etc. (only those that exist)
• Show states side-by-side: default / hover / active / focus / disabled
• Inputs only if present (default/focus/error if applicable)

Layout & Spacing

• Containers, grids, columns, section paddings
• Show 2–3 real layout patterns from the reference (hero layout, grid, split)

Motion & Interaction

Show all motion behaviors present:

• Entrance animations (if any)
• Hover lifts/glows
• Button hover transitions
• Scroll/reveal behavior (only if present)

Include a small Motion Gallery demonstrating each animation class.

Icons

If the reference uses icons:

• Display the same icon style/system
• Show size variants and color inheritance
• Use the same markup and classes

If icons are not present, omit this section entirely."""

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
            "messages": [{"role": "user", "content": user[:180000]}],
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


def _parse_inline_style(style_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in DECL_PATTERN.findall(style_text or ""):
        values[name.strip().lower()] = value.strip()
    return values


def _collect_css_sources(bundle: dict) -> tuple[list[str], list[str], list[str]]:
    asset_root = Path(bundle.get("asset_root") or ".")
    resources = bundle.get("resources", {})
    css_links: list[str] = []
    css_chunks: list[str] = []
    script_links: list[str] = []

    for resource in resources.values():
        local_path = resource.get("local_path", "")
        content_type = (resource.get("content_type") or "").lower()
        disk_candidate = asset_root / Path(local_path).name
        if local_path.endswith(".css") or "text/css" in content_type:
            css_links.append(local_path)
            try:
                css_chunks.append(disk_candidate.read_text(encoding="utf-8", errors="ignore"))
            except FileNotFoundError:
                try:
                    css_chunks.append((asset_root.parent / local_path).read_text(encoding="utf-8", errors="ignore"))
                except FileNotFoundError:
                    pass
        if local_path.endswith(".js") or "javascript" in content_type:
            script_links.append(local_path)

    for page in bundle.get("pages", {}).values():
        soup = BeautifulSoup(page.get("html", ""), "html.parser")
        css_chunks.extend(tag.string or "" for tag in soup.find_all("style") if tag.string)
        for script in soup.find_all("script"):
            src = script.get("src")
            if src and src not in script_links:
                script_links.append(src)

    return css_links, css_chunks, script_links


def _collect_rule_maps(all_css: str) -> list[tuple[str, dict[str, str]]]:
    rules: list[tuple[str, dict[str, str]]] = []
    for selector_blob, body in CSS_RULE_PATTERN.findall(all_css):
        selector = selector_blob.strip()
        if not selector or selector.startswith("@"):
            continue
        decls = {name.strip().lower(): value.strip() for name, value in DECL_PATTERN.findall(body)}
        if decls:
            rules.append((selector, decls))
    return rules


def _selector_matches(selector: str, tag_name: str, classes: set[str]) -> bool:
    for part in selector.split(","):
        s = re.sub(r':[\w\-()]+', '', part).strip()
        if not s:
            continue
        if any(tok in s for tok in ['[', '>', '+', '~']):
            continue
        tag_match = re.match(r'^([a-zA-Z][\w-]*)', s)
        if tag_match and tag_match.group(1).lower() != tag_name.lower():
            continue
        needed_classes = {c for c in re.findall(r'\.([\w-]+)', s)}
        if needed_classes and not needed_classes.issubset(classes):
            continue
        if tag_match or needed_classes:
            return True
    return False


def _compute_metrics(el: Tag, rule_maps: list[tuple[str, dict[str, str]]]) -> dict[str, str]:
    classes = set(el.get('class', []))
    tag_name = el.name.lower()
    style_values = _parse_inline_style(el.get('style', ''))
    resolved: dict[str, str] = {}
    for selector, decls in rule_maps:
        if _selector_matches(selector, tag_name, classes):
            for key in ('font-size', 'line-height', 'font-weight', 'letter-spacing'):
                if key in decls:
                    resolved[key] = decls[key]
    resolved.update({k: v for k, v in style_values.items() if k in {'font-size', 'line-height', 'font-weight', 'letter-spacing'}})
    return resolved


def _metric_label(metrics: dict[str, str]) -> str:
    return f"{metrics.get('font-size', '—')} / {metrics.get('line-height', '—')}"


def _clone_and_replace_text(node: Tag, replacements: list[str]) -> str:
    clone = BeautifulSoup(str(node), 'html.parser')
    strings = [s for s in clone.find_all(string=True) if isinstance(s, NavigableString) and s.strip()]
    rep_iter = iter(replacements)
    for s in strings:
        if s.parent and s.parent.name in {'script', 'style'}:
            continue
        try:
            s.replace_with(next(rep_iter))
        except StopIteration:
            break
    return str(clone)


def _extract_hero_markup(root_soup: BeautifulSoup) -> str:
    for tag in root_soup.find_all(['section', 'header', 'main', 'div']):
        if tag.find('h1') and len(str(tag)) < 70000:
            return _clone_and_replace_text(
                tag,
                [
                    'Design system, motion & asset fidelity for the exact captured site.',
                    'A living evidence file that reuses the original classes, assets, transitions and patterns from the exported bundle.',
                    'See sections',
                    'Open manifest',
                ],
            )
    return ''


def _style_bucket(metrics: dict[str, str], tag_name: str) -> tuple[str, float]:
    size_match = re.search(r'([\d.]+)', metrics.get('font-size', '0'))
    weight_match = re.search(r'([\d.]+)', metrics.get('font-weight', '400'))
    size = float(size_match.group(1)) if size_match else 0.0
    weight = float(weight_match.group(1)) if weight_match else 400.0

    if tag_name == 'h1':
        return 'Heading 1', size
    if tag_name == 'h2':
        return 'Heading 2', size
    if tag_name == 'h3':
        return 'Heading 3', size
    if tag_name == 'h4':
        return 'Heading 4', size
    if tag_name == 'p' and size >= 18:
        return 'Paragraph', size
    if weight >= 600:
        if size >= 20:
            return 'Bold L', size
        if size >= 16:
            return 'Bold M', size
        return 'Bold S', size
    if size >= 20:
        return 'Regular L', size
    if size >= 16:
        return 'Regular M', size
    return 'Regular S', size


def _extract_typography(root_soup: BeautifulSoup, rule_maps: list[tuple[str, dict[str, str]]]) -> list[dict]:
    candidates: list[dict] = []
    for el in root_soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'span', 'a', 'strong', 'button', 'label', 'div']):
        text = el.get_text(' ', strip=True)
        if not text or len(text) > 120 or el.find(['img', 'svg', 'video']):
            continue
        metrics = _compute_metrics(el, rule_maps)
        label, size = _style_bucket(metrics, el.name.lower())
        candidates.append(
            {
                'label': label,
                'size': size,
                'metric_label': _metric_label(metrics),
                'markup': str(el),
                'classes': ' '.join(el.get('class', [])),
            }
        )

    chosen: dict[str, dict] = {}
    for wanted in STYLE_ORDER:
        matches = [c for c in candidates if c['label'] == wanted]
        if not matches:
            continue
        matches.sort(key=lambda c: (c['size'], len(c['classes'])), reverse=True)
        chosen[wanted] = matches[0]
    return [chosen[name] for name in STYLE_ORDER if name in chosen]


def _find_components(root_soup: BeautifulSoup) -> dict[str, list[str]]:
    out = {'buttons': [], 'cards': [], 'inputs': [], 'icons': [], 'layouts': []}

    for node in root_soup.find_all(['a', 'button']):
        text = node.get_text(' ', strip=True)
        if text and len(text) <= 50 and len(str(node)) < 2500 and str(node) not in out['buttons']:
            out['buttons'].append(str(node))
        if len(out['buttons']) >= 4:
            break

    for node in root_soup.find_all(['input', 'textarea', 'select']):
        if len(str(node)) < 2500 and str(node) not in out['inputs']:
            out['inputs'].append(str(node))
        if len(out['inputs']) >= 3:
            break

    for node in root_soup.find_all(['article', 'a', 'div', 'section']):
        classes = ' '.join(node.get('class', [])).lower()
        if any(k in classes for k in ['card', 'tile', 'project', 'case', 'feature']) and len(str(node)) < 14000:
            if str(node) not in out['cards']:
                out['cards'].append(str(node))
        if len(out['cards']) >= 4:
            break

    for node in root_soup.find_all(['section', 'div']):
        if node.find(['h1', 'h2', 'h3']) and len(str(node)) < 28000:
            markup = str(node)
            if markup not in out['layouts']:
                out['layouts'].append(markup)
        if len(out['layouts']) >= 3:
            break

    for node in root_soup.find_all(['svg', 'img']):
        if node.name == 'svg' and len(str(node)) < 5000:
            out['icons'].append(str(node))
        if len(out['icons']) >= 12:
            break

    return out


def _build_state_mirror_css(all_css: str) -> str:
    mirrored: list[str] = []
    for selector_blob, body in CSS_RULE_PATTERN.findall(all_css):
        selector = selector_blob.strip()
        if ':hover' in selector:
            mirrored.append(selector.replace(':hover', '.ds-force-hover') + '{' + body + '}')
        if ':focus-visible' in selector:
            mirrored.append(selector.replace(':focus-visible', '.ds-force-focus-visible') + '{' + body + '}')
        if ':focus' in selector and ':focus-visible' not in selector:
            mirrored.append(selector.replace(':focus', '.ds-force-focus') + '{' + body + '}')
        if ':active' in selector:
            mirrored.append(selector.replace(':active', '.ds-force-active') + '{' + body + '}')
    return '\n'.join(mirrored)


def _extract_context(bundle: dict) -> dict:
    pages = bundle.get('pages', {})
    css_links, css_chunks, script_links = _collect_css_sources(bundle)
    all_css = '\n\n'.join(css_chunks)
    rule_maps = _collect_rule_maps(all_css)

    title = 'Site'
    root_page = pages.get(bundle.get('root_url', ''))
    if root_page and root_page.get('title'):
        title = root_page['title']
    elif pages:
        title = next(iter(pages.values())).get('title') or title

    soup_all = BeautifulSoup('\n'.join(page.get('html', '') for page in pages.values()), 'html.parser')
    root_soup = BeautifulSoup(root_page.get('html', '') if root_page else '', 'html.parser')

    tokens: dict[str, dict] = {}
    for name, value in TOKEN_PATTERN.findall(all_css):
        if name not in tokens:
            clean_value = value.strip()
            tokens[name] = {
                'value': clean_value,
                'label': _infer_label(name),
                'is_color': bool(COLOR_PATTERN.search(clean_value)),
            }

    colors: list[str] = []
    seen_colors: set[str] = set()
    for value in [meta['value'] for meta in tokens.values()] + [m.group(0) for m in COLOR_PATTERN.finditer(all_css)]:
        if not COLOR_PATTERN.search(value):
            continue
        key = value.lower()
        if key not in seen_colors:
            seen_colors.add(key)
            colors.append(value)
        if len(colors) >= 48:
            break

    gradients: list[str] = []
    seen_gradients: set[str] = set()
    for match in GRADIENT_PATTERN.finditer(all_css):
        value = match.group(0).strip()
        if value not in seen_gradients:
            seen_gradients.add(value)
            gradients.append(value)
        if len(gradients) >= 16:
            break

    keyframes: list[str] = []
    for block in KEYFRAME_BLOCK_PATTERN.findall(all_css):
        if block not in keyframes:
            keyframes.append(block)
        if len(keyframes) >= 20:
            break

    transitions: list[str] = []
    seen_transitions: set[str] = set()
    for match in TRANSITION_PATTERN.finditer(all_css):
        value = match.group(0).strip()
        key = value[:220]
        if key not in seen_transitions:
            seen_transitions.add(key)
            transitions.append(value)
        if len(transitions) >= 20:
            break

    components = _find_components(root_soup)
    typography = _extract_typography(root_soup, rule_maps)
    class_counter = Counter()
    for el in soup_all.find_all(class_=True):
        for cls in el.get('class', []):
            class_counter[cls] += 1

    manifest = bundle.get('manifest', {})
    return {
        'title': title,
        'prompt_text': PROMPT_TEXT,
        'css_links': css_links,
        'script_links': script_links,
        'all_css': all_css,
        'state_mirror_css': _build_state_mirror_css(all_css),
        'tokens': tokens,
        'colors': colors,
        'gradients': gradients,
        'keyframes': keyframes,
        'transitions': transitions,
        'typography': typography,
        'components': components,
        'pages': manifest.get('pages', []),
        'top_classes': class_counter.most_common(24),
        'hero_markup': _extract_hero_markup(root_soup),
        'page_count': manifest.get('page_count', len(pages)),
        'asset_count': manifest.get('asset_count', len(bundle.get('resources', {}))),
        'unresolved': manifest.get('unresolved_urls', []),
    }


def _render_states(markup: str, include_disabled: bool = True) -> str:
    variants = []
    states = [('Default', None), ('Hover', 'ds-force-hover'), ('Active', 'ds-force-active'), ('Focus', 'ds-force-focus')]
    for label, state in states:
        clone = BeautifulSoup(markup, 'html.parser')
        target = clone.find(True)
        if not target:
            continue
        if state:
            target['class'] = list(target.get('class', [])) + [state]
        variants.append(f"<article class='ds-state-card'><div class='ds-state-label'>{html.escape(label)}</div><div class='ds-state-preview'>{str(target)}</div></article>")
    if include_disabled:
        clone = BeautifulSoup(markup, 'html.parser')
        target = clone.find(True)
        if target and target.name in {'button', 'input', 'select', 'textarea'}:
            target['disabled'] = 'disabled'
            variants.append(f"<article class='ds-state-card'><div class='ds-state-label'>Disabled</div><div class='ds-state-preview'>{str(target)}</div></article>")
    return ''.join(variants)


def _render_local_design_system(ctx: dict) -> str:
    css_tags = '\n'.join(f'<link rel="stylesheet" href="{html.escape(h)}">' for h in ctx['css_links'])
    script_tags = '\n'.join(f'<script src="{html.escape(h)}"></script>' for h in ctx['script_links'])

    typography_rows = ''.join(
        f"<article class='ds-type-row'><div class='ds-type-name'>{html.escape(item['label'])}</div><div class='ds-type-preview'>{item['markup']}</div><div class='ds-type-metric'>{html.escape(item['metric_label'])}</div></article>"
        for item in ctx['typography']
    ) or "<div class='ds-empty'>No typography evidence extracted.</div>"

    token_rows = ''.join(
        "<article class='ds-token'>"
        + (f"<span class='ds-token-swatch' style='background:{html.escape(meta['value'])}'></span>" if meta['is_color'] else "<span class='ds-token-swatch ds-token-empty'></span>")
        + f"<div class='ds-token-meta'><strong>{html.escape(meta['label'])}</strong><code>{html.escape(name)}</code><span>{html.escape(meta['value'])}</span></div></article>"
        for name, meta in list(ctx['tokens'].items())[:80]
    ) or "<div class='ds-empty'>No tokens found.</div>"

    gradient_rows = ''.join(
        f"<article class='ds-token'><span class='ds-token-swatch' style='background:{html.escape(value)}'></span><div class='ds-token-meta'><strong>Gradient</strong><span>{html.escape(value)}</span></div></article>"
        for value in ctx['gradients'][:12]
    )

    button_states = ''.join(_render_states(markup, include_disabled=True) for markup in ctx['components']['buttons'][:3])
    input_states = ''.join(_render_states(markup, include_disabled=True) for markup in ctx['components']['inputs'][:2])
    card_states = ''.join(_render_states(markup, include_disabled=False) for markup in ctx['components']['cards'][:2])
    layouts_markup = ''.join(f"<article class='ds-layout-pattern'>{markup}</article>" for markup in ctx['components']['layouts'][:3]) or "<div class='ds-empty'>No layout patterns recovered.</div>"
    motion_markup = ''.join(f"<article class='ds-motion-card'><div class='ds-motion-preview'>{markup}</div></article>" for markup in (ctx['components']['buttons'][:2] + ctx['components']['cards'][:2])) or "<div class='ds-empty'>No motion gallery components isolated.</div>"
    motion_specs = ''.join(f'<pre>{html.escape(block)}</pre>' for block in (ctx['keyframes'][:8] + ctx['transitions'][:8])) or "<div class='ds-empty'>No motion evidence found.</div>"
    icons_markup = ''.join(f"<article class='ds-icon-card'>{markup}</article>" for markup in ctx['components']['icons'][:12])

    nav_items = [('hero', 'Hero'), ('typography', 'Typography'), ('colors', 'Colors & Surfaces'), ('components', 'UI Components'), ('layout', 'Layout & Spacing'), ('motion', 'Motion & Interaction')]
    if icons_markup:
        nav_items.append(('icons', 'Icons'))
    nav_html = ''.join(f'<a href="#{anchor}">{label}</a>' for anchor, label in nav_items)

    page_rows = ''.join(
        f"<tr><td><code>{html.escape(page.get('local_path',''))}</code></td><td>{html.escape(page.get('title',''))}</td><td><code>{html.escape(page.get('url',''))}</code></td></tr>"
        for page in ctx['pages']
    ) or '<tr><td colspan="3">No pages in manifest.</td></tr>'
    unresolved_list = ''.join(f'<li><code>{html.escape(url)}</code></li>' for url in ctx['unresolved'][:40]) or '<li>None</li>'
    hero_markup = ctx['hero_markup'] or '<section><h1>Design System</h1><p>Hero evidence unavailable.</p></section>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design System — {html.escape(ctx['title'])}</title>
{css_tags}
{script_tags}
<style>
{ctx['all_css']}
{ctx['state_mirror_css']}
html {{ scroll-behavior: smooth; }}
body {{ margin:0; }}
.ds-topnav {{ position:sticky; top:0; z-index:999; display:flex; gap:12px; flex-wrap:wrap; padding:14px 20px; backdrop-filter:blur(20px); background:rgba(255,255,255,.72); border-bottom:1px solid rgba(0,0,0,.08); }}
.ds-topnav a {{ text-decoration:none; }}
.ds-doc-frame {{ width:min(100% - 48px, 1440px); margin:0 auto; padding:56px 0; }}
.ds-doc-header {{ display:grid; gap:16px; margin-bottom:28px; }}
.ds-doc-header h2, .ds-doc-header p {{ margin:0; }}
.ds-spec-list {{ display:grid; gap:12px; }}
.ds-type-row {{ display:grid; grid-template-columns:minmax(120px, 220px) minmax(0,1fr) 140px; gap:20px; align-items:center; padding:16px 0; border-top:1px solid rgba(0,0,0,.08); }}
.ds-type-row:last-child {{ border-bottom:1px solid rgba(0,0,0,.08); }}
.ds-type-name, .ds-type-metric {{ font-size:12px; letter-spacing:.06em; text-transform:uppercase; opacity:.72; }}
.ds-type-metric {{ text-align:right; }}
.ds-token-grid, .ds-state-grid, .ds-layout-grid, .ds-icon-grid {{ display:grid; gap:16px; grid-template-columns:repeat(12,minmax(0,1fr)); }}
.ds-token, .ds-state-card, .ds-layout-pattern, .ds-icon-card, .ds-motion-card {{ border:1px solid rgba(0,0,0,.08); border-radius:16px; overflow:hidden; padding:16px; }}
.ds-token {{ grid-column:span 3; display:flex; gap:12px; align-items:center; min-height:88px; }}
.ds-token-swatch {{ width:56px; height:56px; border-radius:14px; display:block; border:1px solid rgba(0,0,0,.08); flex:none; }}
.ds-token-empty {{ background:repeating-linear-gradient(45deg, rgba(0,0,0,.04), rgba(0,0,0,.04) 6px, rgba(0,0,0,.08) 6px, rgba(0,0,0,.08) 12px); }}
.ds-token-meta {{ display:flex; flex-direction:column; gap:4px; min-width:0; }}
.ds-token-meta code, .ds-token-meta span {{ overflow-wrap:anywhere; }}
.ds-state-card, .ds-motion-card {{ grid-column:span 3; }}
.ds-layout-pattern {{ grid-column:span 4; }}
.ds-icon-card {{ grid-column:span 2; display:flex; align-items:center; justify-content:center; min-height:120px; }}
.ds-state-label {{ margin-bottom:12px; font-size:12px; letter-spacing:.06em; text-transform:uppercase; opacity:.72; }}
.ds-state-preview, .ds-motion-preview {{ min-height:64px; display:flex; align-items:center; }}
.ds-motion-specs {{ display:grid; gap:12px; margin-top:20px; }}
.ds-motion-specs pre {{ margin:0; padding:14px; border:1px solid rgba(0,0,0,.08); border-radius:14px; overflow:auto; }}
.ds-empty {{ padding:16px; border:1px dashed rgba(0,0,0,.18); border-radius:16px; opacity:.72; }}
.ds-integrity table {{ width:100%; border-collapse:collapse; }}
.ds-integrity td, .ds-integrity th {{ padding:12px 10px; border-top:1px solid rgba(0,0,0,.08); text-align:left; vertical-align:top; }}
@media (max-width: 1100px) {{
  .ds-token, .ds-state-card, .ds-motion-card {{ grid-column:span 6; }}
  .ds-layout-pattern {{ grid-column:span 12; }}
  .ds-icon-card {{ grid-column:span 3; }}
}}
@media (max-width: 760px) {{
  .ds-doc-frame {{ width:min(100% - 24px, 1440px); padding:40px 0; }}
  .ds-type-row {{ grid-template-columns:1fr; gap:10px; }}
  .ds-type-metric {{ text-align:left; }}
  .ds-token, .ds-state-card, .ds-layout-pattern, .ds-icon-card, .ds-motion-card {{ grid-column:span 12; }}
}}
</style>
</head>
<body>
<nav class="ds-topnav">{nav_html}</nav>
<section id="hero">{hero_markup}</section>
<section id="typography"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>Typography</h2><p>Exact elements and classes found in the captured HTML, ordered as requested. Metrics are inferred from the captured CSS bundle.</p></header><div class="ds-spec-list">{typography_rows}</div></div></section>
<section id="colors"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>Colors &amp; Surfaces</h2><p>Backgrounds, borders, gradients and surfaces extracted from the actual bundle. No invented palette.</p></header><div class="ds-token-grid">{token_rows}</div><div class="ds-token-grid">{gradient_rows}</div></div></section>
<section id="components"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>UI Components</h2><p>States are mirrored from the original pseudo-state selectors. The styling is reused from the captured CSS and classes.</p></header><div class="ds-state-grid">{button_states}{input_states}{card_states}</div></div></section>
<section id="layout"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>Layout &amp; Spacing</h2><p>Real layout patterns from the site. This section reuses actual wrappers instead of redrawing a fake grid.</p></header><div class="ds-layout-grid">{layouts_markup}</div></div></section>
<section id="motion"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>Motion &amp; Interaction</h2><p>Animation classes, transitions and interaction states from the original CSS. The gallery below reuses live markup.</p></header><div class="ds-state-grid">{motion_markup}</div><div class="ds-motion-specs">{motion_specs}</div></div></section>
{f'<section id="icons"><div class="ds-doc-frame"><header class="ds-doc-header"><h2>Icons</h2><p>Exact icon style and markup recovered from the captured HTML.</p></header><div class="ds-icon-grid">{icons_markup}</div></div></section>' if icons_markup else ''}
<section><div class="ds-doc-frame ds-integrity"><header class="ds-doc-header"><h2>Integrity</h2><p>Coverage evidence for the exported site package.</p></header><table><thead><tr><th>Local path</th><th>Title</th><th>Original URL</th></tr></thead><tbody>{page_rows}</tbody></table><div><h3>Pending remote references</h3><ul>{unresolved_list}</ul></div><details><summary>Prompt used to organize this design system</summary><pre>{html.escape(ctx['prompt_text'])}</pre></details></div></section>
</body>
</html>'''


def generate_design_system(source) -> str:
    if isinstance(source, str):
        bundle = {
            'root_url': 'https://captured.local',
            'pages': {'https://captured.local': {'local_path': 'index.html', 'html': source, 'title': 'Captured page'}},
            'resources': {},
            'manifest': {'pages': [{'local_path': 'index.html', 'url': 'https://captured.local', 'title': 'Captured page'}]},
        }
    else:
        bundle = source

    ctx = _extract_context(bundle)

    if DESIGN_SYSTEM_MODE in {'auto', 'llm'} and ANTHROPIC_API_KEY:
        llm_html = _call_claude_api(
            'Return a complete design-system.html document only. Preserve exact classes, assets and layout patterns from the provided site evidence. Never invent tokens or components.',
            json.dumps(
                {
                    'prompt': ctx['prompt_text'],
                    'title': ctx['title'],
                    'page_count': ctx['page_count'],
                    'asset_count': ctx['asset_count'],
                    'tokens': ctx['tokens'],
                    'colors': ctx['colors'],
                    'gradients': ctx['gradients'],
                    'keyframes': ctx['keyframes'],
                    'transitions': ctx['transitions'],
                    'typography': ctx['typography'],
                    'components': ctx['components'],
                    'pages': ctx['pages'],
                    'unresolved': ctx['unresolved'],
                    'hero_markup': ctx['hero_markup'],
                    'css_links': ctx['css_links'],
                    'script_links': ctx['script_links'],
                },
                ensure_ascii=False,
            ),
        )
        if llm_html and llm_html.lstrip().startswith('<!DOCTYPE html>'):
            return llm_html

    return _render_local_design_system(ctx)
