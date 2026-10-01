#!/usr/bin/env bash
# Demo reproducible de VenomOps (Fase 5):
#   kind -> rompe 4 cosas -> kdoctor diagnostica -> pr-agent arma el PR (--dry-run) -> arch-committee revisa el plan.
#
# Seguridad (CLAUDE.md, sección 2):
#   * El clúster es un kind LOCAL y todo kubectl/kdoctor usa un kubeconfig temporal propio: nunca tu kubeconfig.
#   * `kubectl apply` solo corre tras comprobar que el contexto es el del kind de la demo.
#   * pr-agent corre SIEMPRE con --dry-run: no toca GitHub ni el repo. Nada ejecuta `terraform apply`.
#   * arch-committee solo llama a Bedrock si TÚ defines ARCH_COMMITTEE_BEDROCK_MODEL; si no, usa --dry-run.
#
# Requisitos: docker, kind, kubectl, go (1.26+), terraform, uv o python3.12 (para los venv de pr-agent y arch-committee).
# Variables opcionales:
#   KEEP_CLUSTER=1                 no borra el clúster al terminar (para inspeccionarlo)
#   DEMO_SAVE_FINDINGS=ruta.json   guarda la salida JSON de kdoctor
#   TF_PLUGIN_CACHE_DIR=ruta       reutiliza los providers de Terraform entre ejecuciones
#   ARCH_COMMITTEE_BEDROCK_MODEL   ejecuta el comité DE VERDAD (usa tus credenciales de AWS y cuesta tokens)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="venomops-demo"
WORKDIR="$(mktemp -d)"
export KUBECONFIG="$WORKDIR/kubeconfig"   # aislado: jamás el kubeconfig por defecto

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  if [ "${KEEP_CLUSTER:-0}" = "1" ]; then
    echo "KEEP_CLUSTER=1: el clúster '$CLUSTER' sigue activo. kubeconfig: $KUBECONFIG"
    return
  fi
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null 2>&1 || true
  rm -f "$KUBECONFIG"
  rmdir "$WORKDIR" 2>/dev/null || true
}
trap cleanup EXIT

for cmd in docker kind kubectl go terraform; do
  command -v "$cmd" >/dev/null 2>&1 || die "falta '$cmd' en el PATH"
done
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" && die "ya existe un clúster kind '$CLUSTER'; bórralo con: kind delete cluster --name $CLUSTER"

say "1/6 Dependencias de Python (pr-agent y arch-committee)"
make -C "$ROOT/packages/pr-agent" setup >/dev/null
make -C "$ROOT/packages/arch-committee" setup >/dev/null
PR_AGENT="$ROOT/packages/pr-agent/.venv/bin/pr-agent"
COMMITTEE="$ROOT/packages/arch-committee/.venv/bin/arch-committee"

say "2/6 Compilando kdoctor"
(cd "$ROOT/packages/kdoctor" && go build -o "$WORKDIR/kubectl-venom_doctor" ./cmd/kubectl-venom_doctor)

say "3/6 Levantando un clúster kind local ($CLUSTER) y rompiendo 4 cosas a propósito"
kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 120s >/dev/null
[ "$(kubectl config current-context)" = "kind-$CLUSTER" ] || die "el contexto no es kind-$CLUSTER: no se aplica nada"
kubectl apply -f "$ROOT/examples/k8s/" >/dev/null

echo "Esperando a que los Pods lleguen a sus estados de fallo (hasta ~3 min)..."
ready() {
  local get; get() { kubectl get pod "$1" -n kdoctor-demo -o jsonpath="$2" 2>/dev/null || true; }
  [ "$(get oom '{.status.containerStatuses[0].lastState.terminated.reason}')" = "OOMKilled" ] || return 1
  case "$(get badimage '{.status.containerStatuses[0].state.waiting.reason}')" in ImagePullBackOff|ErrImagePull) ;; *) return 1 ;; esac
  [ "$(get pending '{.status.phase}')" = "Pending" ] || return 1
  [ "$(get crashloop '{.status.containerStatuses[0].restartCount}')" -ge 3 ] 2>/dev/null || return 1
}
for _ in $(seq 1 60); do ready && break; sleep 3; done
ready || die "los Pods no llegaron al estado esperado a tiempo"
kubectl get pods -n kdoctor-demo

say "4/6 kdoctor: diagnóstico (solo lectura)"
"$WORKDIR/kubectl-venom_doctor" -n kdoctor-demo
"$WORKDIR/kubectl-venom_doctor" -n kdoctor-demo -o json > "$WORKDIR/kdoctor.json"
[ -z "${DEMO_SAVE_FINDINGS:-}" ] || { cp "$WORKDIR/kdoctor.json" "$DEMO_SAVE_FINDINGS"; echo "Guardado en $DEMO_SAVE_FINDINGS"; }

say "5/6 pr-agent: kdoctor -o json | pr-agent fix --dry-run  (elige el único finding con arreglo)"
# Con `kubectl venom-doctor -n ... -o json | pr-agent fix --finding - ...` el resultado es el mismo; aquí se usa el archivo.
"$PR_AGENT" fix --finding "$WORKDIR/kdoctor.json" --supported \
  --repo "$ROOT/examples/terraform/k8s-oom-demo" --dry-run

say "6/6 arch-committee: revisa el plan de Terraform con el diagnóstico como contexto"
if [ -n "${ARCH_COMMITTEE_BEDROCK_MODEL:-}" ]; then
  echo "ARCH_COMMITTEE_BEDROCK_MODEL definido: ejecución REAL del comité (usa tus credenciales de AWS)."
  "$COMMITTEE" review --plan "$ROOT/examples/plans/k8s-oom.json" --context-findings "$WORKDIR/kdoctor.json" \
    --out "$WORKDIR/committee-out"
  echo "Informe: $WORKDIR/committee-out/report.md"
else
  echo "Sin ARCH_COMMITTEE_BEDROCK_MODEL: --dry-run (muestra lo que se enviaría, ya redactado; no se llama a ningún modelo)."
  "$COMMITTEE" review --plan "$ROOT/examples/plans/k8s-oom.json" --context-findings "$WORKDIR/kdoctor.json" --dry-run
fi

say "Listo. Se borra el clúster kind (KEEP_CLUSTER=1 para conservarlo)."
