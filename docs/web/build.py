#!/usr/bin/env python3
"""Builds the VenomOps website (GitHub Pages) from the Markdown guides in docs/guia/.

The guides are the single source of truth: they read well on GitHub and become the site's documentation pages.
The landing page (templates/index.html) is hand-written. Output is plain static HTML/CSS/JS with no external requests.

Usage: python docs/web/build.py --out site [--url https://cloudvipers.github.io/VenomOps] [--version 0.1.5]
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

import markdown
from markdown.extensions.toc import TocExtension

WEB = Path(__file__).resolve().parent
ROOT = WEB.parents[1]
GUIA = ROOT / "docs" / "guia"
REPO_URL = "https://github.com/CloudVipers/VenomOps"
DEFAULT_URL = "https://cloudvipers.github.io/VenomOps"

# (markdown file, output file, nav title, description, nav group)
PAGES: list[tuple[str, str, str, str, str]] = [
    (
        "README.md",
        "documentacion.html",
        "Documentación",
        "Guías de uso de VenomOps: instalar, usar, casos de uso, reglas y seguridad.",
        "Empezar",
    ),
    (
        "instalacion.md",
        "instalacion.html",
        "Instalación",
        "Cómo instalar venom y venom-doctor en Linux, macOS y Windows, y comprobar que funciona.",
        "Empezar",
    ),
    (
        "uso.md",
        "uso.html",
        "Uso",
        "Los comandos venom doctor, venom fix y venom review: opciones, permisos y cómo encadenarlos.",
        "Usar",
    ),
    (
        "casos-de-uso.md",
        "casos-de-uso.html",
        "Casos de uso",
        "Situaciones reales en las que usar venom, con los comandos y qué esperar.",
        "Usar",
    ),
    (
        "ia.md",
        "ia.html",
        "La IA es opcional",
        "Qué funciona sin IA, qué aporta de verdad, qué datos salen y qué necesitas en AWS si decides usarla.",
        "Usar",
    ),
    (
        "reglas.md",
        "reglas.html",
        "Catálogo de reglas",
        "Qué detecta cada regla, cómo reconocer el problema, cómo confirmarlo a mano y cómo arreglarlo.",
        "Referencia",
    ),
    (
        "seguridad.md",
        "seguridad.html",
        "Seguridad",
        "Qué garantiza venom, dónde se hace cumplir en el código y cómo verificar lo que instalas.",
        "Referencia",
    ),
    (
        "ayuda.md",
        "ayuda.html",
        "Ayuda",
        "Qué hacer cuando algo no funciona o el resultado no es el esperado, con los mensajes exactos.",
        "Referencia",
    ),
]
MD_TO_HTML = {src: out for src, out, *_ in PAGES}


def github_slug(value: str, sep: str = "-") -> str:
    """Same anchors GitHub generates, so a link works both on GitHub and on the site."""
    value = re.sub(r"[^\w\- ]", "", value.strip().lower())
    return value.replace(" ", sep)


def repo_link(target: str, page_dir: Path) -> str:
    """Turns a relative link to a repository file or folder into a GitHub URL."""
    path, _, anchor = target.partition("#")
    resolved = (page_dir / path).resolve()
    try:
        rel = resolved.relative_to(ROOT)
    except ValueError:
        return target
    if not resolved.exists():
        raise SystemExit(f"broken link in a guide: {target!r} does not exist in the repository")
    kind = "tree" if resolved.is_dir() or path.endswith("/") else "blob"
    return f"{REPO_URL}/{kind}/main/{rel.as_posix()}" + (f"#{anchor}" if anchor else "")


def check_repo_url(href: str) -> None:
    """A guide may link to the repository with an absolute GitHub URL: make sure the file or folder exists."""
    m = re.match(rf"^{re.escape(REPO_URL)}/(?:blob|tree)/main/([^#?]+)", href)
    if m and not (ROOT / m.group(1)).exists():
        raise SystemExit(f"broken link in a guide: {href!r} points to a path that does not exist in the repository")


def rewrite_href(href: str, page_dir: Path) -> str:
    if re.match(r"^(https?:|mailto:|#|/)", href):
        check_repo_url(href)
        return href
    path, _, anchor = href.partition("#")
    if path in MD_TO_HTML:  # a link to another guide
        return MD_TO_HTML[path] + (f"#{anchor}" if anchor else "")
    return repo_link(href, page_dir)


def render_markdown(text: str) -> tuple[str, list[dict[str, str]], list[str]]:
    langs = re.findall(r"^```([\w+-]*)\s*$", text, flags=re.M)[::2]  # the opening fence of each block, in order
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "sane_lists",
            "attr_list",
            "codehilite",
            TocExtension(slugify=github_slug, toc_depth="2-3"),
        ],
        extension_configs={"codehilite": {"css_class": "highlight", "guess_lang": False, "noclasses": False}},
    )
    body = md.convert(text)
    toc: list[dict[str, str]] = []

    def flatten(tokens: list[dict]) -> None:
        for token in tokens:
            toc.append({"id": token["id"], "name": token["name"], "level": str(token["level"])})
            flatten(token.get("children", []))

    flatten(md.toc_tokens)  # type: ignore[attr-defined]
    return body, toc, langs


def decorate(body: str, langs: list[str], page_dir: Path) -> str:
    # 1. code blocks: language label + copy button (the output blocks tagged `text` are not copyable commands)
    blocks = list(re.finditer(r'<div class="highlight">', body))
    if len(blocks) != len(langs):
        raise SystemExit(f"code block / fence mismatch: {len(blocks)} rendered vs {len(langs)} fenced")
    it = iter(langs)
    body = re.sub(
        r'<div class="highlight">',
        lambda _m: _code_open(next(it)),
        body,
    )
    body = body.replace("</pre></div>", "</pre></div></figure>")
    # 2. tables scroll sideways on small screens instead of breaking the layout
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    # 3. block quotes become callouts
    body = re.sub(r"<blockquote>\s*", '<aside class="callout">', body).replace("</blockquote>", "</aside>")
    # 4. anchors on headings
    body = re.sub(
        r'<(h[23]) id="([^"]+)">(.*?)</\1>',
        lambda m: (
            f'<{m.group(1)} id="{m.group(2)}">{m.group(3)}'
            f'<a class="anchor" href="#{m.group(2)}" aria-label="Enlace a esta sección">#</a></{m.group(1)}>'
        ),
        body,
        flags=re.S,
    )
    # 5. links between guides and to the repository
    body = re.sub(
        r'href="([^"]+)"',
        lambda m: f'href="{html.escape(rewrite_href(html.unescape(m.group(1)), page_dir), quote=True)}"',
        body,
    )
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" rel="noopener"', body)
    return body


def _code_open(lang: str) -> str:
    label = {"bash": "bash", "yaml": "yaml", "text": "salida", "": "", "diff": "diff", "json": "json"}.get(lang, lang)
    copy = (
        ""
        if lang == "text"
        else '<button class="copy" type="button" aria-label="Copiar al portapapeles">Copiar</button>'
    )
    kind = "out" if lang == "text" else "cmd"
    caption = f"<figcaption><span>{html.escape(label)}</span>{copy}</figcaption>"
    return f'<figure class="code code-{kind}">{caption}<div class="highlight">'


def nav_html(current: str) -> str:
    groups: dict[str, list[tuple[str, str]]] = {}
    for _src, out, title, _desc, group in PAGES:
        groups.setdefault(group, []).append((out, title))
    parts = []
    for group, items in groups.items():
        links = []
        for out, title in items:
            current_attr = ' aria-current="page"' if out == current else ""
            links.append(f'<li><a href="{out}"{current_attr}>{html.escape(title)}</a></li>')
        parts.append(f'<div class="nav-group"><p>{html.escape(group)}</p><ul>{"".join(links)}</ul></div>')
    return "".join(parts)


def toc_html(toc: list[dict[str, str]]) -> str:
    items = [t for t in toc if int(t["level"]) in (2, 3)]
    if len(items) < 3:
        return ""
    lis = "".join(
        f'<li class="l{t["level"]}"><a href="#{t["id"]}">{html.escape(re.sub(r"<[^>]+>", "", t["name"]))}</a></li>'
        for t in items
    )
    return f'<nav class="toc" aria-label="En esta página"><p>En esta página</p><ul>{lis}</ul></nav>'


def pager_html(index: int) -> str:
    prev = PAGES[index - 1] if index > 0 else None
    nxt = PAGES[index + 1] if index + 1 < len(PAGES) else None
    left = (
        f'<a class="prev" href="{prev[1]}"><span>Anterior</span>{html.escape(prev[2])}</a>' if prev else "<span></span>"
    )
    right = (
        f'<a class="next" href="{nxt[1]}"><span>Siguiente</span>{html.escape(nxt[2])}</a>' if nxt else "<span></span>"
    )
    return f'<nav class="pager" aria-label="Navegación entre guías">{left}{right}</nav>'


TOP_NAV = [
    ("index.html", "Inicio"),
    ("documentacion.html", "Documentación"),
    ("instalacion.html", "Instalación"),
    ("casos-de-uso.html", "Casos de uso"),
    ("reglas.html", "Reglas"),
    ("seguridad.html", "Seguridad"),
]


def chrome(current: str) -> dict[str, str]:
    """Header and footer shared by every page; the current section is marked in the top navigation."""
    links = ""
    for href, label in TOP_NAV:
        current_attr = ' aria-current="page"' if href == current else ""
        links += f'<a href="{href}"{current_attr}>{html.escape(label)}</a>'
    header = (WEB / "templates" / "_header.html").read_text(encoding="utf-8").replace("{{NAVLINKS}}", links)
    footer = (WEB / "templates" / "_footer.html").read_text(encoding="utf-8")
    return {"HEADER": header, "FOOTER": footer}


def fill(template: str, values: dict[str, str]) -> str:
    out = template
    # header and footer carry placeholders of their own, so expand them first and fill everything in a second pass
    for key in ("HEADER", "FOOTER"):
        if key in values:
            out = out.replace("{{" + key + "}}", values[key])
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    leftovers = re.findall(r"\{\{[a-zA-Z_]+\}\}", out)
    if leftovers:
        raise SystemExit(f"unreplaced placeholders: {sorted(set(leftovers))}")
    return out


def build(out: Path, url: str, version: str) -> None:
    if out.exists():
        for child in out.iterdir():
            if child.name in {"rpm", "deb"} or child.suffix in {".asc", ".gpg"}:
                continue  # the signed package repository lives next to the site: never touch it
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WEB / "assets", out / "assets", dirs_exist_ok=True)

    page_t = (WEB / "templates" / "page.html").read_text(encoding="utf-8")
    common = {"URL": url, "VERSION": version, "REPO": REPO_URL}
    for i, (src, dest, title, desc, _group) in enumerate(PAGES):
        text = (GUIA / src).read_text(encoding="utf-8")
        body, toc, langs = render_markdown(text)
        body = decorate(body, langs, GUIA)
        edit = f"{REPO_URL}/edit/main/docs/guia/{src}"
        page = fill(
            page_t,
            {
                **common,
                "TITLE": html.escape(title),
                "DESCRIPTION": html.escape(desc, quote=True),
                "PAGE": dest,
                "NAV": nav_html(dest),
                "TOC": toc_html(toc),
                "CONTENT": body,
                "PAGER": pager_html(i),
                "EDIT": edit,
                **chrome(dest),
            },
        )
        (out / dest).write_text(page, encoding="utf-8")
    index_t = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(fill(index_t, {**common, **chrome("index.html")}), encoding="utf-8")
    (out / ".nojekyll").touch()
    print(f"built {len(PAGES) + 1} pages into {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--version", default="0.0.0")
    args = ap.parse_args()
    build(args.out, args.url.rstrip("/"), args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
