# VenomOps

**Encuentra qué está roto en tu clúster. Arréglalo con un Pull Request.**

Herramientas para arquitectos cloud, parte del ecosistema **Ecosistema Nexus by ITera** y desarrolladas por [CloudVipers](https://cloudvipers.com).
VenomOps explica en lenguaje claro por qué falla Kubernetes o EKS, convierte el hallazgo en el cambio mínimo de Terraform y, si quieres una segunda opinión con IA, revisa tus
planes con un comité de agentes antes del `apply`. Es de **solo lectura y simulación**: nada se aplica solo, cada cambio pasa por una persona,
y la IA solo se usa si la pides.

**Web con guías, casos de uso y el catálogo de reglas: <https://cloudvipers.github.io/VenomOps/>**

## Instalación

```bash
# RHEL 9, Rocky, Alma, Amazon Linux 2023
sudo tee /etc/yum.repos.d/venom.repo <<'REPO'
[venom]
name=VenomOps
baseurl=https://cloudvipers.github.io/VenomOps/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://cloudvipers.github.io/VenomOps/venom-repo.asc
REPO
sudo dnf install venom

# Debian 12+, Ubuntu 22.04+
sudo curl -fsSL https://cloudvipers.github.io/VenomOps/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] https://cloudvipers.github.io/VenomOps/deb stable main' | sudo tee /etc/apt/sources.list.d/venom.list
sudo apt update && sudo apt install venom

# Solo el diagnóstico de clústeres, como plugin de kubectl (Linux, macOS y Windows)
kubectl krew install --manifest-url=https://raw.githubusercontent.com/CloudVipers/VenomOps/main/packages/venom-doctor/venom-doctor.yaml
```

Los paquetes están firmados con GPG (huella `A7BE 1F5E 03EC 7AA9 C797  A9DE 3237 E8D7 9E6E 29C5`). Más opciones, requisitos y cómo verificar la
instalación en la [guía de instalación](docs/guia/instalacion.md).

## Empieza en un minuto

```bash
venom doctor -n mi-namespace        # diagnostica un namespace: causa, evidencia y pasos para arreglarlo (solo lectura)
venom doctor -A -o json             # todo el clúster, en JSON
venom doctor -A -o json | venom fix --finding - --supported --repo ./infra --dry-run   # del hallazgo al PR (simulado)
venom review --plan plan.json --model-id <modelo> --out ./informe                      # un comité revisa tu plan de Terraform
```

| Comando | Qué hace | Toca algo | Usa IA |
|---|---|---|---|
| `venom doctor` | Explica por qué algo está roto en un clúster y cómo arreglarlo | No, solo lee | Solo con `--explain` |
| `venom fix` | Convierte un hallazgo en un Pull Request con el cambio mínimo en Terraform | Abre un PR (nunca lo mezcla) | Solo con `--agent` |
| `venom review` | Un comité de agentes revisa un plan de Terraform y debate | Escribe un informe local | **Sí** (Bedrock), la única que la necesita |
| `venom update` | Descarga la última versión y verifica su firma | Con `--install`, instala el paquete | No |

## Documentación

| Guía | Para qué |
|---|---|
| [Instalación](docs/guia/instalacion.md) | Instalar `venom` o solo `venom-doctor` |
| [Uso](docs/guia/uso.md) | Los tres comandos, sus opciones y los permisos de solo lectura |
| [Casos de uso](docs/guia/casos-de-uso.md) | Situaciones reales: de guardia, antes de un drain, del OOMKilled al PR, revisar un plan, CI |
| [La IA es opcional](docs/guia/ia.md) | Qué usa IA y qué no, qué aporta de verdad, qué datos salen y qué necesitas |
| [Catálogo de reglas](docs/guia/reglas.md) | Las nueve reglas: cómo reconocer cada problema, confirmarlo a mano y arreglarlo |
| [Seguridad](docs/guia/seguridad.md) | Garantías, dónde se hacen cumplir y cómo verificar lo que instalas |
| [Ayuda](docs/guia/ayuda.md) | Qué hacer cuando algo no funciona, con los mensajes exactos |

La documentación técnica vive junto al código: [arquitectura](docs/arquitectura.md), las [decisiones de diseño](docs/decisiones) (ADRs) y el README de cada paquete.

## Paquetes

| Paquete | Qué hace | Lenguaje |
|---|---|---|
| [`venom`](packages/venom) | El comando único: `venom doctor`, `venom fix`, `venom review` | Python |
| [`venom-doctor`](packages/venom-doctor) | Plugin de `kubectl` que diagnostica Kubernetes/EKS y explica cómo arreglarlo (nueve reglas) | Go |
| [`pr-agent`](packages/pr-agent) | Convierte un hallazgo en un Pull Request con el arreglo mínimo en Terraform | Python |
| [`arch-committee`](packages/arch-committee) | Comité virtual de agentes que revisa un plan de Terraform y debate | Python |
| [`findings-schema`](packages/findings-schema) | Contrato común de hallazgos (*findings*): JSON Schema + tipos | JSON Schema, Go, Python |

## Cómo se conectan

Todo habla el mismo formato, el *finding*, validado en Go y en Python:

```
clúster ──▶ venom doctor ──▶ findings ──▶ venom fix ──▶ Pull Request ──▶ una persona
                               │
                               └──(contexto)──▶ venom review ◀── plan de Terraform
                                                      │
                                                      └──▶ informe (decisiones, desacuerdos, riesgos aceptados)
```

El recorrido completo (kind → diagnóstico → PR → comité) está automatizado en [`examples/demo/run-demo.sh`](examples/demo/run-demo.sh), y el flujo con
diagramas y dónde se hace cumplir cada regla de seguridad, en [`docs/arquitectura.md`](docs/arquitectura.md).

## Seguridad

VenomOps es de **solo lectura y dry-run** sobre infraestructura real: nada de `terraform apply/destroy` ni comandos mutantes de `kubectl`. Las llamadas a un LLM son siempre
opcionales y explícitas, no hay modelo por defecto y se enmascaran los secretos antes de enviar datos. Los límites se hacen cumplir en el código, no solo en las instrucciones.
Las reglas completas están en la sección 2 de [`CLAUDE.md`](CLAUDE.md), el detalle en la [guía de seguridad](docs/guia/seguridad.md) y cómo reportar una vulnerabilidad en
[`SECURITY.md`](SECURITY.md).

## Estructura

```
packages/   venom, venom-doctor, pr-agent, arch-committee, findings-schema
packaging/  paquetes .rpm/.deb, repositorio firmado, scripts de release
examples/   k8s/, terraform/, plans/, findings/, rbac/ y demo/: escenarios con fallas deliberadas para probar
docs/       guia/ (las guías de uso), web/ (generador del sitio), arquitectura.md y decisiones/ (ADRs)
```

La web se genera a partir de `docs/guia/` con `docs/web/build.py`; ver [`docs/web/README.md`](docs/web/README.md).

## Desarrollo

```bash
make lint    # lint de todos los paquetes
make test    # tests de todos los paquetes
make build   # build de todos los paquetes
```

Cada paquete puede traer su propio `Makefile`; los que aún no lo tienen se omiten.

Opcional, para formato y chequeos básicos antes de cada commit:

```bash
pip install pre-commit && pre-commit install
```

## Cómo contribuir

1. Crea una rama desde `main`: `feat/<scope>-<descripcion-corta>`, `fix/...` o `docs/...`. **No se hace push directo a `main`.**
2. Commits con [Conventional Commits](https://www.conventionalcommits.org/) y en inglés, p. ej. `feat(venom-doctor): add OOMKilled rule`. Scopes válidos: `schema`, `venom-doctor`, `pr-agent`, `committee`, `venom`, `repo`, `docs`.
3. Abre un PR pequeño y revisable. Los tests y el lint deben pasar (`make lint && make test`).
4. Nunca incluyas credenciales, tokens, IDs de cuenta ni datos de clientes: usa placeholders (`123456789012`, `example.com`).

## Licencia

[Apache 2.0](LICENSE).
