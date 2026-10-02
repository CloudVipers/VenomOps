"""Behaviour test of the built site in a real browser (tabs, theme, copy buttons, mobile menu, table of contents).

Needs Playwright with a browser. The easiest way is the official Docker image:

  docker run --rm -v "$PWD/site:/site" -v "$PWD/docs/web/tests/behaviour.py:/behaviour.py" \
    mcr.microsoft.com/playwright/python:v1.47.0-jammy sh -c 'pip install -q playwright==1.47.0 && python /behaviour.py'

SITE (default /site) is the directory where the site was built.
"""

import os

from playwright.sync_api import sync_playwright

SITE = os.environ.get("SITE", "/site")
ok = []


def check(name, cond):
    ok.append(cond)
    print(("OK   " if cond else "FALLO"), name)


with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    # --- escritorio
    ctx = b.new_context(
        viewport={"width": 1280, "height": 900}, color_scheme="dark", permissions=["clipboard-read", "clipboard-write"]
    )
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(f"file://{SITE}/index.html")
    # pestañas
    check("pestaña inicial visible (rpm)", pg.is_visible("#p-rpm") and not pg.is_visible("#p-deb"))
    pg.click("#t-deb")
    check("clic en la pestaña Debian muestra su panel", pg.is_visible("#p-deb") and not pg.is_visible("#p-rpm"))
    check(
        "aria-selected se actualiza",
        pg.get_attribute("#t-deb", "aria-selected") == "true"
        and pg.get_attribute("#t-rpm", "aria-selected") == "false",
    )
    pg.focus("#t-deb")
    pg.keyboard.press("ArrowRight")
    check("flecha derecha pasa a la siguiente pestaña (krew)", pg.is_visible("#p-krew"))
    # tema
    before = pg.evaluate("document.documentElement.dataset.theme || 'sin-definir'")
    pg.click(".theme-btn")
    after = pg.evaluate("document.documentElement.dataset.theme")
    check(f"el botón de tema alterna ({before} → {after})", after == "light")
    check("la preferencia se guarda", pg.evaluate("localStorage.getItem('venom-theme')") == "light")
    pg.reload()
    pg.wait_for_timeout(200)
    check("la preferencia sobrevive a recargar", pg.evaluate("document.documentElement.dataset.theme") == "light")
    # copiar (el pill del hero y un bloque)
    pg.click(".install-pill .copy")
    pg.wait_for_timeout(250)
    check("copiar muestra «Copiado»", pg.inner_text(".install-pill .copy") == "Copiado")
    clip = pg.evaluate("navigator.clipboard.readText()")
    check(f"el portapapeles contiene el comando ({clip!r})", clip == "sudo dnf install venom")
    pg.goto(f"file://{SITE}/uso.html")
    first = pg.query_selector("figure.code-cmd .copy")
    first.click()
    pg.wait_for_timeout(250)
    clip = pg.evaluate("navigator.clipboard.readText()")
    check("copiar un bloque de código copia su texto (venom doctor…)", clip.startswith("venom doctor"))
    check("los bloques de salida NO tienen botón de copiar", pg.query_selector("figure.code-out .copy") is None)
    # tabla de contenidos activa
    pg.evaluate("document.documentElement.style.scrollBehavior='auto'; window.scrollTo(0, 2500)")
    pg.wait_for_timeout(400)
    check("la tabla de contenidos marca la sección actual", pg.query_selector(".toc a.active") is not None)
    ctx.close()
    # --- móvil
    ctx = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(f"file://{SITE}/index.html")
    check("en móvil el menú principal está oculto", not pg.is_visible(".main-nav"))
    pg.click(".menu-btn")
    check(
        "el botón de menú lo despliega",
        pg.is_visible(".main-nav") and pg.get_attribute(".menu-btn", "aria-expanded") == "true",
    )
    pg.click(".main-nav a:nth-child(3)")
    pg.wait_for_timeout(300)
    check("elegir un enlace navega a la página", pg.url.endswith("instalacion.html"))
    ctx.close()
    check("sin errores de JavaScript ni de consola", errors == [])
    if errors:
        print(errors)
    b.close()
print("\nRESULTADO:", "todo bien" if all(ok) else "HAY FALLOS", f"({sum(ok)}/{len(ok)})")
