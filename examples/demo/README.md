# Demo reproducible

`run-demo.sh` recorre el flujo completo de VenomOps en un clúster **kind local**:

```
kind  →  rompe 4 cosas  →  venom-doctor diagnostica  →  pr-agent arma el PR (--dry-run)  →  arch-committee revisa el plan
```

```bash
examples/demo/run-demo.sh
```

Tarda ~3-4 minutos. Requiere `docker`, `kind`, `kubectl`, `go`, `terraform` y `uv` o `python3.12`.

## Qué hace, paso a paso

1. Prepara los entornos de Python de `pr-agent` y `arch-committee`.
2. Compila `venom-doctor`.
3. Crea el clúster `venomops-demo` y aplica [`examples/k8s/`](../k8s) (CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending).
4. **venom-doctor** diagnostica y emite JSON.
5. **pr-agent** elige el único finding con arreglo (`KD-K8S-002`) y, en `--dry-run`, muestra el diff
   (`limits.memory` 32Mi → 64Mi en [`examples/terraform/k8s-oom-demo`](../terraform/k8s-oom-demo)) y el cuerpo del PR,
   con `terraform validate` y `plan` reales.
6. **arch-committee** revisa [`examples/plans/k8s-oom.json`](../plans/k8s-oom.json) con el diagnóstico como contexto.
7. Borra el clúster.

## Seguridad

- Usa un **kubeconfig temporal propio**: nunca toca tu kubeconfig ni tus contextos.
- `kubectl apply` solo corre tras comprobar que el contexto es el del kind de la demo.
- `pr-agent` va siempre con `--dry-run`; nada se escribe, se comitea ni se sube. Nada ejecuta `terraform apply`.
- `arch-committee` **no llama a ningún modelo** salvo que definas tú `ARCH_COMMITTEE_BEDROCK_MODEL` (entonces usa tus
  credenciales de AWS y cuesta tokens); por defecto muestra con `--dry-run` lo que se enviaría, ya redactado.

## Variables opcionales

| Variable | Efecto |
|---|---|
| `KEEP_CLUSTER=1` | No borra el clúster al terminar (el kubeconfig queda impreso) |
| `DEMO_SAVE_FINDINGS=ruta.json` | Guarda la salida JSON de `venom-doctor` (así se regenera `examples/findings/venom-doctor-output.json`) |
| `TF_PLUGIN_CACHE_DIR=ruta` | Reutiliza los providers de Terraform entre ejecuciones |
| `ARCH_COMMITTEE_BEDROCK_MODEL=<modelId>` | Ejecuta el comité de verdad en el paso 6 |

La misma tubería, a mano: `kubectl venom-doctor -n venom-demo -o json | pr-agent fix --finding - --supported --repo
examples/terraform/k8s-oom-demo --dry-run`.
