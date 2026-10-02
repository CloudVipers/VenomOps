# La web de VenomOps

Sitio estático (HTML, CSS y un poco de JavaScript, **sin peticiones externas**) publicado en <https://cloudvipers.github.io/VenomOps/>.

- **La portada** (`templates/index.html`) está escrita a mano.
- **Las páginas de documentación** se generan a partir de las guías en Markdown de [`docs/guia/`](../guia): una sola fuente que se lee bien en GitHub y en la web.
  Para cambiar el contenido, edita la guía y no el HTML.
- El tema claro/oscuro sigue las preferencias del sistema y se puede cambiar a mano; hay copiado de comandos, pestañas de instalación y menú móvil.

## Generar y ver en local

```bash
python3 -m venv /tmp/web-venv && /tmp/web-venv/bin/pip install -r docs/web/requirements.txt
/tmp/web-venv/bin/python docs/web/build.py --out /tmp/site --version 0.1.5
python3 docs/web/check_site.py /tmp/site          # enlaces, anclas, recursos, metadatos y que no reaparezca el nombre antiguo
python3 -m http.server -d /tmp/site 8000          # abre http://localhost:8000
```

`check_site.py --strict` exige además las claves públicas del repositorio de paquetes (`venom-repo.asc/.gpg`); lo usa el workflow de publicación.

## Cómo se publica

El workflow [`publish-repo.yml`](../../.github/workflows/publish-repo.yml) construye la web junto al repositorio firmado de paquetes (Pages reemplaza el sitio entero en
cada despliegue) y la publica **en cada push a `main` que cambie `docs/guia/` o `docs/web/`**, usando la última versión publicada de `venom`. También se puede lanzar a mano.
Los PR que tocan estos directorios pasan por [`ci-web.yml`](../../.github/workflows/ci-web.yml): genera la web y la comprueba.

## Estructura

| Ruta | Qué es |
|---|---|
| `build.py` | Generador: Markdown → HTML (con enlaces entre guías y a GitHub, anclas iguales a las de GitHub, bloques de código con botón de copiar) |
| `check_site.py` | Comprobador de la web generada |
| `templates/` | `index.html` (portada), `page.html` (guías), `_header.html` y `_footer.html` |
| `assets/` | `style.css` (temas claro/oscuro), `site.js` y el logo |
| `tests/behaviour.py` | Prueba de comportamiento en un navegador real (Playwright): pestañas, tema, copiar, menú móvil y tabla de contenidos |

## Convenciones al escribir las guías

- Los títulos no llevan `·` ni otros signos raros: GitHub y el generador calculan las anclas igual solo con letras, números, espacios y guiones.
- Los enlaces a otras guías son relativos (`uso.md#anclas`); los enlaces a archivos del repositorio, relativos también, y el generador los convierte a GitHub.
- Los bloques `text` son **salida** (sin botón de copiar); `bash`, `yaml`, etc. son comandos copiables.
- Los ejemplos de salida son reales: captúralos ejecutando las herramientas, no los inventes.
