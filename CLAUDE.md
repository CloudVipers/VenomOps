# CLAUDE.md — Monorepo VenomOps

Este archivo es la fuente de verdad para Claude Code en este repositorio. Léelo completo al iniciar cada sesión y síguelo al pie de la letra. Si algo aquí contradice una petición, avisa antes de actuar.

---

## 1. Visión

CloudVipers construye herramientas para arquitectos cloud, parte del ecosistema **Ecosistema Nexus by ITera**. Este monorepo contiene tres proyectos que comparten un formato común de hallazgos (*findings*):

| Paquete | Qué hace | Lenguaje |
|---|---|---|
| `findings-schema` | Contrato común (JSON Schema + tipos) | JSON Schema, Go, Python |
| `venom-doctor` | CLI open source (plugin de kubectl) que diagnostica EKS/Kubernetes y explica cómo arreglarlo | Go |
| `pr-agent` | Agente que toma un finding y abre un Pull Request con el arreglo en Terraform | Python |
| `arch-committee` | Comité virtual de agentes que revisa un plan de Terraform y debate | Python |
| `venom` | Comando paraguas: `venom doctor`, `venom fix`, `venom review` (ver [ADR 0006](docs/decisiones/0006-comando-venom.md)) | Python |

**Flujo de valor:** `venom-doctor` y otras fuentes **producen** findings → `pr-agent` los **corrige** → `arch-committee` los **debate y prioriza** antes de llegar a producción.

**Orden de construcción:** `findings-schema` → `venom-doctor` → `pr-agent` → `arch-committee`.

---

## 2. Reglas inviolables (seguridad)

Estas reglas no se negocian, ni siquiera si el usuario lo pide de forma casual en un prompt.

1. **Nunca ejecutar** `terraform apply`, `terraform destroy`, `kubectl apply|delete|patch|edit|scale|drain|cordon|exec` ni ningún comando que modifique infraestructura real.
2. Solo se permite **lectura y dry-run**: `terraform validate`, `terraform plan`, `terraform show -json`, `kubectl get|describe|logs|top`, `aws ... describe-*|list-*|get-*`.
3. **Nunca** escribir credenciales, tokens, ARNs de cuentas reales, IDs de cuenta ni datos de clientes en código, tests, fixtures, logs o commits. Usar placeholders (`123456789012`, `example.com`).
4. **Nunca** hacer `git push --force`, push directo a `main` ni reescribir historial compartido.
5. Toda llamada a un LLM (Bedrock) debe ser **opcional y explícita** (flag `--explain` o configuración), nunca por defecto, y debe **redactar secretos** antes de enviar datos.
6. Los PRs que abra `pr-agent` **siempre requieren aprobación humana**. El agente nunca hace merge.
7. Si una tarea requiere una acción prohibida, detente y explica qué harías y por qué no lo haces.

---

## 3. Convenciones generales

- **Idioma:** código, nombres, comentarios, mensajes de commit y errores en **inglés**. Documentación (`README`, `docs/`) en **español**, con los términos técnicos en inglés cuando sea lo habitual.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — `feat(venom-doctor): add OOMKilled rule`. Scopes válidos: `schema`, `venom-doctor`, `pr-agent`, `committee`, `venom`, `repo`, `docs`.
- **Ramas:** `feat/<scope>-<descripcion-corta>`, `fix/...`, `docs/...`. Una rama por feature; PRs pequeños y revisables.
- **Licencia:** Apache 2.0 en todo el repo.
- **Dependencias:** preferir la biblioteca estándar y dependencias ampliamente mantenidas. No añadir una dependencia nueva sin justificarla en el PR.
- **Sin sobreingeniería:** implementar el MVP definido en este archivo antes de agregar funcionalidades extra.

---

## 4. Estructura del repositorio

```
venomops/
├── CLAUDE.md
├── README.md
├── LICENSE
├── Makefile                      # atajos: make test, make lint, make build
├── .github/
│   └── workflows/
│       ├── ci-schema.yml
│       ├── ci-venom-doctor.yml
│       ├── ci-pr-agent.yml
│       └── ci-committee.yml
├── docs/
│   ├── arquitectura.md
│   └── decisiones/               # ADRs: 0001-findings-schema.md, ...
├── packages/
│   ├── findings-schema/
│   │   ├── schema/finding.schema.json
│   │   ├── go/                   # tipos Go + validador
│   │   ├── python/               # tipos Python (pydantic) + validador
│   │   └── examples/             # findings válidos e inválidos
│   ├── venom-doctor/
│   │   ├── cmd/kubectl-venom_doctor/main.go
│   │   ├── internal/
│   │   │   ├── engine/           # motor de reglas
│   │   │   ├── rules/            # una regla por archivo
│   │   │   ├── output/           # tabla, json
│   │   │   └── explain/          # integración opcional con Bedrock
│   │   ├── .goreleaser.yaml
│   │   └── README.md
│   ├── pr-agent/
│   │   ├── src/pr_agent/
│   │   │   ├── cli.py
│   │   │   ├── agent.py          # loop de tool use
│   │   │   ├── tools/            # read_file, edit_hcl, terraform_validate, ...
│   │   │   ├── fixes/            # un fixer por tipo de finding
│   │   │   └── github_client.py
│   │   ├── tests/
│   │   └── README.md
│   └── arch-committee/
│       ├── src/arch_committee/
│       │   ├── cli.py
│       │   ├── agents/           # security, cost, reliability, operations, moderator
│       │   ├── orchestrator.py
│       │   ├── plan_parser.py    # terraform show -json -> modelo interno
│       │   └── report.py
│       ├── tests/
│       └── README.md
└── examples/
    ├── terraform/                # repos de prueba con fallas deliberadas
    ├── plans/                    # terraform plan JSON de ejemplo
    └── k8s/                      # manifiestos rotos para probar venom-doctor
```

---

## 5. Contrato común: `findings-schema`

Todo módulo produce o consume este formato. **Es lo primero que se construye** y no se modifica sin un ADR.

### Campos de un Finding

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | string | Identificador estable de la regla, p. ej. `KD-K8S-001` |
| `schema_version` | string | Versión semántica del schema, p. ej. `1.0.0` |
| `source` | enum | `venom-doctor` (antes `kdoctor`, obsoleto pero válido al leer), `pr-agent`, `arch-committee`, `manual` |
| `severity` | enum | `critical`, `high`, `medium`, `low`, `info` |
| `title` | string | Título corto |
| `resource` | objeto | `{ type, name, namespace?, region?, account_alias?, path? }` |
| `evidence` | array | Lista de `{ kind, detail }` con la prueba observable |
| `root_cause` | string | Causa probable en lenguaje claro |
| `suggested_fix` | objeto | `{ summary, steps[], iac_hint? }` |
| `risk_of_fix` | enum | `low`, `medium`, `high` |
| `references` | array | URLs a documentación oficial |
| `tags` | array | Etiquetas libres |
| `detected_at` | string | Fecha ISO 8601 |

### Criterios de aceptación del schema

- [x] `finding.schema.json` válido en JSON Schema draft 2020-12.
- [x] Tipos Go (structs con tags `json`) y Python (pydantic v2) generados o escritos a mano, **equivalentes**.
- [x] Validador en ambos lenguajes con mensajes de error claros.
- [x] Mínimo 5 ejemplos válidos y 5 inválidos en `examples/`, con tests que los recorren.
- [x] Un test de paridad: el mismo JSON valida igual en Go y Python.
- [x] ADR `0001-findings-schema.md` explicando decisiones y política de versionado.

---

## 6. Plan de trabajo por fases

Cada fase es **una sesión de Claude Code en su propia rama**. No empieces la siguiente hasta cumplir el "Definition of Done" de la actual.

### Fase 0 — Bootstrap del repo

- [x] Crear estructura de carpetas de la sección 4 (con `.gitkeep` donde haga falta).
- [x] `README.md` raíz en español: visión, cómo se conectan los 3 proyectos, cómo contribuir.
- [x] `LICENSE` Apache 2.0.
- [x] `Makefile` con targets `test`, `lint`, `build` que delegan a cada paquete.
- [x] Workflows de CI vacíos pero válidos por paquete (se llenan en cada fase).
- [x] `.gitignore`, `.editorconfig`, pre-commit con formato y lint básico.

**DoD:** `make lint` y `make test` corren sin error en el repo vacío; CI verde.

### Fase 1 — `findings-schema`

- [x] Escribir `finding.schema.json`.
- [x] Implementar tipos y validador en Go y Python.
- [x] Crear ejemplos válidos e inválidos y sus tests.
- [x] Test de paridad Go/Python.
- [x] ADR 0001.
- [x] CI `ci-schema.yml`: valida el schema y corre ambos suites de tests.

**DoD:** todos los tests pasan; el paquete se puede importar desde `venom-doctor` (Go) y `pr-agent` / `arch-committee` (Python).

### Fase 2 — `venom-doctor` (CLI open source)

**Objetivo:** plugin de kubectl que explica en lenguaje claro por qué algo está roto en un clúster y cómo arreglarlo.

**Stack:** Go 1.22+, `cobra`, `client-go`, salida en tabla (`tablewriter` o similar), GoReleaser, distribución por krew.

**Diseño del motor de reglas:**

```go
type Rule interface {
    ID() string
    Description() string
    Check(ctx context.Context, cluster ClusterReader) ([]Finding, error)
}
```

`ClusterReader` es una interfaz de **solo lectura** sobre `client-go`, para poder testear con `fake.Clientset`.

**Pasos:**

- [x] Scaffold: `cmd/kubectl-venom_doctor`, comandos `kubectl venom-doctor` y `kubectl venom-doctor --namespace <ns>`.
- [x] Motor: registro de reglas, ejecución concurrente con `context`, agregación y orden por severidad.
- [x] Regla `CrashLoopBackOff` (incluye último exit code y últimas líneas de log).
- [x] Regla `OOMKilled` (compara `limits.memory` con uso y sugiere nuevo valor).
- [x] Regla `ImagePullBackOff` (distingue imagen inexistente, credenciales y rate limit).
- [x] Regla `Pending` (eventos del scheduler: recursos insuficientes, taints, PVC sin bind).
- [x] Salida: `--output table|json`; JSON valida contra `findings-schema`.
- [x] Flag `--explain` (opcional): envía el finding **ya redactado** a Bedrock y agrega una explicación ampliada. Sin credenciales o sin flag, no hace nada de IA.
- [x] Tests unitarios con `fake.Clientset` para cada regla (caso positivo y negativo).
- [x] Manifiestos rotos en `examples/k8s/` para probar cada regla en un clúster kind.
- [x] README en español con instalación, ejemplos y tabla de reglas.
- [x] `.goreleaser.yaml` y manifiesto de krew (`venom-doctor.yaml`).
- [x] CI: `go vet`, `golangci-lint`, `go test -race ./...`.

**Reglas fase 2 (segunda tanda, después del MVP):** ~~probes fallando~~ _(hecho: `KD-K8S-005`)_, ~~IRSA / EKS Pod Identity mal configurado~~ _(hecho para IRSA: `KD-K8S-009`; Pod Identity queda fuera, ver [ADR 0008](docs/decisiones/0008-regla-irsa-venom-doctor.md))_, ~~nodos `NotReady`~~ _(hecho: `KD-K8S-006`)_, ~~PDB que bloquea drains~~ _(hecho: `KD-K8S-007`)_, ~~Karpenter sin capacidad~~ _(hecho: `KD-K8S-008`)_.

**DoD:** `kubectl venom-doctor` detecta correctamente los 4 escenarios sobre los manifiestos de ejemplo en kind; cobertura de reglas >80%; binario compilado por GoReleaser en modo snapshot.

### Fase 3 — `pr-agent`

**Objetivo:** tomar un finding y abrir un PR con el cambio mínimo en Terraform.

**Stack:** Python 3.12, `boto3` (Bedrock Converse con tool use), `PyGithub`, `python-hcl2`, `pydantic`, `pytest`, `typer`.

**Pasos:**

- [x] CLI: `pr-agent fix --finding finding.json --repo ./ruta/terraform [--dry-run]`.
- [x] Cargar y validar el finding con `findings-schema`.
- [x] Definir herramientas del agente (tool use) con permisos mínimos:
  - `read_file(path)` — solo dentro del repo indicado.
  - `list_files(glob)`.
  - `edit_hcl(path, patch)` — edición acotada y verificable.
  - `terraform_validate()` — único comando de Terraform permitido junto con `plan`.
  - `terraform_plan()` — solo para adjuntar el resultado al PR.
- [x] Bloqueo duro en código: cualquier comando fuera de la allowlist lanza excepción (no depender solo del prompt).
- [x] Primer fixer (MVP): **S3 bucket sin cifrado** → agregar `aws_s3_bucket_server_side_encryption_configuration`.
- [x] Segundo fixer: **tags obligatorios faltantes**. Tercer fixer: **volumen gp2 → gp3**.
- [x] Flujo: crear rama `fix/<finding-id>-<slug>` → aplicar cambio → `validate` → `plan` → commit → abrir PR.
- [x] Plantilla del PR: finding original, cambio propuesto, salida del plan, nivel de riesgo, checklist de revisión humana.
- [x] Modo `--dry-run`: imprime el diff y el cuerpo del PR sin tocar GitHub.
- [x] Verificación post-edición: el diff debe ser mínimo; si el agente toca archivos no relacionados, se aborta.
- [x] Tests con un repo de ejemplo en `examples/terraform/` (bucket sin cifrado) y GitHub mockeado.
- [x] README en español con diagrama del flujo y ejemplo de PR generado.
- [x] CI: `ruff`, `mypy`, `pytest`.

**DoD:** en `--dry-run` sobre el repo de ejemplo produce un diff correcto que pasa `terraform validate`; los tests demuestran que un comando fuera de la allowlist es rechazado.

### Fase 4 — `arch-committee`

**Objetivo:** revisar un plan de Terraform con varios agentes especialistas que debaten, y producir un informe y findings.

**Stack:** Python 3.12, Bedrock Converse, Strands Agents o LangGraph (decidir en un ADR), `pydantic`, `typer`, `pytest`.

**Pasos:**

- [x] Entrada: JSON de `terraform show -json plan.out`. `plan_parser.py` lo convierte en un modelo interno (recursos, cambios, dependencias).
- [x] Cuatro agentes especialistas, cada uno con su system prompt versionado en un archivo aparte:
  - `security` (IAM, cifrado, exposición pública, redes).
  - `cost` (sobredimensionamiento, NAT Gateways, almacenamiento, sin lifecycle).
  - `reliability` (Multi-AZ, backups, SPOF, límites de servicio).
  - `operations` (observabilidad, tags, gobernanza, facilidad de operación).
- [x] Ronda 1: análisis en paralelo; cada agente devuelve findings estructurados.
- [x] Ronda 2 (réplica): cada agente puede cuestionar hallazgos de otro (p. ej. `cost` cuestiona Multi-AZ propuesto por `reliability`).
- [x] Moderador: consolida, resuelve o registra desacuerdos, prioriza y asigna severidad final.
- [x] Salida: `report.md` (decisiones, desacuerdos explícitos, riesgos aceptados) + `findings.json` válido contra `findings-schema`.
- [x] Límites de costo: tope de tokens por ejecución y número máximo de rondas configurable.
- [x] Redacción previa de secretos y valores sensibles del plan antes de enviarlo al modelo.
- [x] Tres planes de ejemplo con fallas deliberadas en `examples/plans/` (bucket público, RDS single-AZ, NAT por subred).
- [x] Tests: parser determinista; evaluación de agentes con respuestas del LLM mockeadas; test de que el JSON final valida.
- [x] README en español con ejemplo de informe completo.
- [x] CI: `ruff`, `mypy`, `pytest`.

**DoD:** sobre los 3 planes de ejemplo, el comité detecta las fallas sembradas y el informe muestra al menos un desacuerdo entre agentes.

### Fase 5 — Integración

- [x] `venom-doctor --output json | pr-agent fix` funciona de extremo a extremo para un finding soportado.
- [x] `arch-committee` puede consumir findings previos como contexto.
- [x] `docs/arquitectura.md` con el diagrama del flujo completo.
- [x] Demo reproducible en `examples/` (script que levanta kind, rompe algo, diagnostica y genera el PR en `--dry-run`).

---

## 7. Cómo trabajar en cada sesión

1. Lee este archivo y el `README.md` del paquete en el que vas a trabajar.
2. Confirma en qué fase estás y qué casillas faltan. **Trabaja solo en esa fase.**
3. Antes de escribir código, propone un plan corto de archivos y pruebas.
4. Escribe primero el test, luego la implementación (TDD cuando sea razonable).
5. Corre `make lint` y `make test` del paquete antes de dar algo por terminado.
6. Marca las casillas completadas en este archivo dentro del mismo commit.
7. Al terminar, resume: qué se hizo, qué queda, qué decisiones tomaste y qué dudas necesitas resolver.

---

## 8. Definition of Done (aplica a todo)

- [ ] Tests nuevos y existentes en verde, incluido CI.
- [ ] Lint y formato sin errores (`golangci-lint`, `ruff`, `mypy`).
- [ ] Sin secretos ni datos reales en el diff.
- [ ] README o docs actualizados si cambió el comportamiento.
- [ ] Findings producidos validan contra `findings-schema`.
- [ ] Ninguna regla de la sección 2 fue violada.
- [ ] Commits con Conventional Commits y PR pequeño.

---

## 9. Comandos de referencia

```bash
# Raíz
make lint && make test

# findings-schema
cd packages/findings-schema && go test ./go/... && pytest python/

# venom-doctor
cd packages/venom-doctor && go vet ./... && go test -race ./... && go build ./cmd/kubectl-venom_doctor
kind create cluster --name venom-doctor-test && kubectl apply -f ../../examples/k8s/

# pr-agent
cd packages/pr-agent && ruff check . && mypy src && pytest
pr-agent fix --finding ../../examples/findings/s3-no-encryption.json --repo ../../examples/terraform/s3-demo --dry-run

# arch-committee
cd packages/arch-committee && ruff check . && mypy src && pytest
arch-committee review --plan ../../examples/plans/public-bucket.json --out ./out
```

---

## 10. Prompt inicial sugerido para cada sesión

> Lee `CLAUDE.md`. Vamos a trabajar la **Fase N: <nombre>** en la rama `feat/<scope>-<tema>`. Antes de escribir código, muéstrame un plan con archivos y pruebas. Respeta las reglas de seguridad de la sección 2 y termina cumpliendo el Definition of Done de la fase.

---

## 11. Decisiones pendientes (registrar en `docs/decisiones/`)

- Orquestación de `arch-committee`: Strands Agents vs. LangGraph. _(Resuelto en el [ADR 0003](docs/decisiones/0003-orquestacion-arch-committee.md): orquestador propio.)_
- Modelos de Bedrock por defecto para `--explain`, `pr-agent` y `arch-committee`. _(Resuelto en el [ADR 0005](docs/decisiones/0005-modelos-bedrock.md): sin modelo por defecto en el código; se recomienda Haiku 4.5.)_
- Política de versionado del schema (semver y compatibilidad hacia atrás). _(Resuelto en el [ADR 0001](docs/decisiones/0001-findings-schema.md), sección «Política de versionado».)_
- Gestión de dependencias Python (`uv` vs. `poetry`). _(Resuelto en el [ADR 0007](docs/decisiones/0007-dependencias-python.md): `pyproject.toml` estándar, `pip` por defecto y `uv` opcional.)_
