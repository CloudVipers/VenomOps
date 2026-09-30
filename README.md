# VenomOps

Herramientas para arquitectos cloud, parte del ecosistema **Ecosistema Nexus by ITera** y desarrolladas por [CloudVipers](https://cloudvipers.com). VenomOps ayuda a **detectar**, **corregir** y **debatir** problemas de infraestructura antes de que lleguen a producción.

> Estado: **Fase 3** en curso. `findings-schema`, el MVP de `kdoctor` y el MVP de `pr-agent` están construidos; `arch-committee` sigue (ver [`CLAUDE.md`](CLAUDE.md), sección 6).

## Paquetes

| Paquete | Qué hace | Lenguaje |
|---|---|---|
| [`findings-schema`](packages/findings-schema) | Contrato común de hallazgos (*findings*): JSON Schema + tipos | JSON Schema, Go, Python |
| [`kdoctor`](packages/kdoctor) | Plugin de `kubectl` (open source) que diagnostica EKS/Kubernetes y explica cómo arreglarlo | Go |
| [`pr-agent`](packages/pr-agent) | Agente que toma un finding y abre un Pull Request con el arreglo en Terraform | Python |
| [`arch-committee`](packages/arch-committee) | Comité virtual de agentes que revisa un plan de Terraform y debate | Python |

## Cómo se conectan

Los tres proyectos comparten el formato de `findings-schema`:

```
kdoctor / otras fuentes ──produce──▶ findings ──▶ pr-agent ──▶ Pull Request (Terraform)
                                        │
                                        └──────▶ arch-committee ──▶ report.md + findings.json
```

- `kdoctor` y otras fuentes **producen** findings.
- `pr-agent` los **corrige** abriendo un PR que siempre requiere aprobación humana.
- `arch-committee` los **debate y prioriza** antes de llegar a producción.

Orden de construcción: `findings-schema` → `kdoctor` → `pr-agent` → `arch-committee`.

## Seguridad

VenomOps es de **solo lectura y dry-run** sobre infraestructura real: nada de `terraform apply/destroy` ni comandos mutantes de `kubectl`. Las llamadas a un LLM son siempre opcionales y explícitas, y redactan secretos antes de enviar datos. Las reglas completas están en la sección 2 de [`CLAUDE.md`](CLAUDE.md).

## Estructura

```
packages/   findings-schema, kdoctor, pr-agent, arch-committee
examples/   terraform/, plans/ y k8s/ con fallas deliberadas para pruebas
docs/       arquitectura.md y decisiones/ (ADRs)
```

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
2. Commits con [Conventional Commits](https://www.conventionalcommits.org/) y en inglés, p. ej. `feat(kdoctor): add OOMKilled rule`. Scopes válidos: `schema`, `kdoctor`, `pr-agent`, `committee`, `repo`, `docs`.
3. Abre un PR pequeño y revisable. Los tests y el lint deben pasar (`make lint && make test`).
4. Nunca incluyas credenciales, tokens, IDs de cuenta ni datos de clientes: usa placeholders (`123456789012`, `example.com`).

## Licencia

[Apache 2.0](LICENSE).
