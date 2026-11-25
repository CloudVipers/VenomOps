# research/eval — arnés de evaluación

Ejecuta los experimentos de la [metodología](../../docs/investigacion/metodologia.md) sobre VenomOps y guarda cada
corrida de forma reproducible. Vive **fuera** de `packages/`: no se publica en los paquetes `.rpm`/`.deb` ni agrega
dependencias a `pr-agent` o `arch-committee` ([ADR 0011](../../docs/decisiones/0011-linea-de-investigacion.md)).

```bash
make setup && . .venv/bin/activate
make test      # pruebas deterministas: ninguna llama a Bedrock
make e4        # experimento E4 (redacción): determinista, sin modelo
```

## Comandos

| Comando | Qué hace | ¿Llama al modelo? |
|---|---|---|
| `venom-eval cases [--target T]` | Lista los 145 casos del [corpus de inyección](../../examples/benchmark/injection) | No |
| `venom-eval materialize --case ID --out DIR` | Escribe un caso en disco para ejecutarlo a mano (p. ej. `venom-doctor --explain`) | No |
| `venom-eval e1 --model-id M [--confirm]` | E1: integridad de las acciones de `pr-agent`, condiciones `prompt-only` y `prompt+code` | Sí |
| `venom-eval e2 --model-id M [--rounds 1 2] [--confirm]` | E2: supresión o degradación de hallazgos en `arch-committee`, sin y con réplica | Sí |
| `venom-eval e3 --system NOMBRE=ADAPTADOR:DIR ...` | E3: precision, recall y F1 desde salidas guardadas (Checkov, Trivy, findings) | No |
| `venom-eval e4` | E4: tasa de fuga y falsos positivos de los redactores | No |
| `venom-eval summarize DIR` | Tablas con intervalos de Wilson y prueba de McNemar | No |

Sin `--confirm`, `e1` y `e2` solo imprimen cuántas ejecuciones harían (tienen costo). El modelo es siempre explícito:
no hay valor por defecto.

## Ensayo sin costo con un emulador

Antes de gastar en Bedrock, `e1` y `e2` pueden apuntar a un emulador compatible con la API Converse de
`bedrock-runtime` (p. ej. LocalStack respaldado por un modelo local) para probar el flujo de punta a punta:

```bash
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
  venom-eval e1 --model-id <modelo-local> --endpoint-url http://localhost:4566 \
                --limit 3 --repetitions 1 --out /tmp/ensayo-e1 --confirm
```

También se respetan `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` y `AWS_ENDPOINT_URL`, las mismas variables que usa `boto3`.
Cada registro guarda el campo `endpoint` (`bedrock` o la URL), y la corrida avisa que es un ensayo. **Un ensayo no es un
resultado de la tesis:** el modelo local no es el modelo de Bedrock que fija la [metodología](../../docs/investigacion/metodologia.md).
Guárdalo fuera de `research/results/`. El emulador tiene que soportar *tool use* en Converse, porque E1 y E2 dependen de él.

## Cómo se garantiza que ningún experimento toca nada real

- **E1, condición `prompt-only`:** el [ejecutor simulado](src/venom_eval/simulated.py) ofrece las mismas herramientas
  que `pr-agent`, pero no ejecuta procesos, no escribe archivos y solo lee dentro del directorio temporal del caso.
  Mide lo que el modelo **intenta** cuando solo lo protege el prompt.
- **E1, condición `prompt+code`:** `pr_agent.workflow.run_fix` real en `--dry-run`, con la lista blanca real; Terraform lo
  responde un runner que registra el comando y **no** lo ejecuta. Mide lo que **logra**.
- Los casos se materializan en directorios temporales con *canaries* sintéticos (`.env`, `terraform.tfvars`, estado)
  para detectar fugas.
- Las pruebas verifican que ningún runner inicia un proceso (`subprocess` parcheado para fallar).

## E3: líneas base externas

Checkov y Trivy no son dependencias del repositorio; se ejecutan aparte y el arnés solo lee su JSON, un archivo por caso
del benchmark (`<dir>/<case_id>.json`):

```bash
checkov -f plan.json --framework terraform_plan -o json > out/checkov/<case_id>.json
trivy config -f json <dir-del-caso> > out/trivy/<case_id>.json
venom-eval e3 --system checkov=checkov:out/checkov --system trivy=trivy:out/trivy \
              --system committee=findings:out/committee --union committee+checkov --out research/results/e3
```

Regla de emparejamiento (fijada antes de correr): un hallazgo es verdadero positivo si coincide la dirección base del
recurso (sin `[índice]`) y la categoría de una etiqueta todavía no emparejada.

## Registros (`research/results/`)

Cada corrida escribe JSON Lines con: experimento, `model_id`, `endpoint`, condición, repetición, versiones de los prompts,
temperatura, commit de git, fecha UTC, veredicto, tokens y la salida cruda necesaria para auditar el caso.
`venom-eval summarize research/results/<corrida>` reconstruye las tablas sin volver a llamar al modelo.
