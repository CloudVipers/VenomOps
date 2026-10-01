# venom

Un solo comando para las tres herramientas de VenomOps. No reimplementa nada: `review` y `fix` son los mismos
comandos de `arch-committee` y `pr-agent`, y `doctor` ejecuta el binario `kubectl-venom_doctor` (que sigue siendo el plugin
de krew `kubectl venom-doctor`).

| Comando | Equivale a | Qué hace |
|---|---|---|
| `venom doctor -n NS [--explain] [-o json]` | `kubectl venom-doctor …` | Explica por qué algo está roto en un clúster (solo lectura) |
| `venom fix --finding f.json --repo ./tf [--dry-run]` | `pr-agent fix …` | Abre un PR con el arreglo mínimo en Terraform (nunca hace merge) |
| `venom review --plan plan.json --model-id <id>` | `arch-committee review …` | Un comité virtual revisa un plan de Terraform y reporta |

```bash
venom doctor -A -o json | venom fix --finding - --supported --repo ./infra --dry-run
venom review --plan plan.json --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --out ./out
```

Las reglas de seguridad no cambian: los comandos originales siguen disponibles, las llamadas a Bedrock son opcionales y
explícitas (`--explain`, `--agent`, `--model-id`), no hay modelo por defecto ([ADR 0005](../../docs/decisiones/0005-modelos-bedrock.md))
y nunca se ejecuta `terraform apply` ni nada que modifique infraestructura. Decisión: [ADR 0006](../../docs/decisiones/0006-comando-venom.md).

## Instalación

**Paquete (recomendado, sin Python):** `.rpm` para RHEL 9 / Rocky / Alma / Amazon Linux 2023 y `.deb` para Debian 12+ /
Ubuntu 22.04+; instalan `venom` y `kubectl-venom_doctor`. Ver [`packaging/`](../../packaging/README.md).

```bash
sudo dnf install https://github.com/CloudVipers/VenomOps/releases/download/venom-v0.1.2/venom-0.1.2-1.x86_64.rpm
```

**Desde el monorepo (desarrollo):**

```bash
cd packages/venom && make setup   # venv con findings-schema, pr-agent, arch-committee y venom
.venv/bin/venom --help
```

`venom doctor` necesita `kubectl-venom_doctor` en el `PATH` (lo trae el paquete; si no, plugin de kubectl o `go build ./cmd/kubectl-venom_doctor`).
`venom fix` necesita `terraform` en el `PATH` para validar el arreglo.
