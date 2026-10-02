# Documentación de VenomOps

VenomOps ayuda a **detectar**, **corregir** y **debatir** problemas de infraestructura antes de que lleguen a producción. Es **de solo lectura y simulación**:
nada de `apply`, ningún cambio sin que una persona lo revise, y la IA solo si la pides.

Estas guías también están publicadas en la web: <https://cloudvipers.github.io/VenomOps/>.

| Guía | Para qué |
|---|---|
| [Instalación](instalacion.md) | Instalar `venom` o solo `venom-doctor` en Linux, macOS o Windows |
| [Uso](uso.md) | Los comandos `venom doctor`, `venom fix` y `venom review`, sus opciones y permisos |
| [Casos de uso](casos-de-uso.md) | Situaciones reales, con los comandos y qué esperar |
| [La IA es opcional](ia.md) | Qué funciona sin IA, qué aporta de verdad, qué datos salen y qué necesitas en AWS |
| [Catálogo de reglas](reglas.md) | Qué detecta cada una de las nueve reglas, cómo reconocerla y cómo arreglarla |
| [Seguridad](seguridad.md) | Qué garantiza, dónde se hace cumplir y cómo verificar lo que instalas |
| [Ayuda](ayuda.md) | Qué hacer cuando algo no funciona, con los mensajes exactos |

## Empieza en un minuto

```bash
sudo dnf install venom          # tras añadir el repositorio: ver Instalación
venom doctor -n mi-namespace    # diagnostica un namespace, sin modificar nada
```

## Para quien contribuye

La documentación técnica del proyecto vive junto al código: [arquitectura](../arquitectura.md), las [decisiones de diseño](../decisiones/) (ADRs) y el README de cada paquete en
[`packages/`](../../packages/).
