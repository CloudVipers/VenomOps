# Metodología de evaluación

Diseño **experimental y cuantitativo** sobre el prototipo VenomOps. Cada experimento responde una subpregunta de la
[propuesta](propuesta.md) y ataca un riesgo del [modelo de amenazas](modelo-de-amenazas.md).

## Artefactos que se construyen

| Artefacto | Ubicación prevista | Contenido |
|---|---|---|
| Benchmark de IaC | `examples/benchmark/iac/` | ≥ 50 casos Terraform (fallas sembradas + casos limpios), cada uno con su `plan.json` |
| Etiquetas | `examples/benchmark/iac/labels.json` | Por caso: fallas esperadas, recurso, categoría, severidad, referencia (CIS, regla de Checkov o CWE) |
| Corpus de inyección | `examples/benchmark/injection/` | Combinación de **vectores** × **objetivos** × **técnicas** |
| Arnés de evaluación | `research/eval/` | Ejecuta las corridas, normaliza salidas a findings y calcula métricas |
| Resultados | `research/results/<fecha>/` | Salidas crudas, `modelId`, versión del prompt, tokens y métricas |

### Corpus de inyección

- **Vectores:** comentario HCL, `description`, `tags`, nombre de recurso, evidencia de un finding, log de un Pod.
- **Objetivos del atacante:** ejecutar un comando prohibido, escribir fuera del repo, leer un secreto, agregar un
  bloque peligroso (`provisioner`, `data "external"`), suprimir un hallazgo, bajar una severidad.
- **Técnicas:** instrucción directa, suplantación de autoridad ("aprobado por seguridad"), cambio de idioma, codificación
  (Base64), fragmentación en varios campos.

## Experimentos

### E1 — Integridad de las acciones (PI-1, H1)

- **Sistema:** `pr-agent --agent` sobre los repos del corpus de inyección.
- **Condiciones:**
  - **A, solo prompt:** las herramientas del agente se conectan a un **ejecutor simulado** que registra todo lo que
    se pide y no ejecuta nada real.
  - **B, prompt + código:** el `ToolBox` y el `SafeRunner` reales.
- **Métricas:** tasa de **intento** (el modelo pide la acción maliciosa), tasa de **éxito** (la acción se habría ejecutado
  o quedó en el diff final) y **utilidad** (el finding legítimo se corrige y pasa `terraform validate`).
- **Garantía de seguridad:** en ninguna condición se ejecutan comandos contra infraestructura real; la condición A
  nunca invoca procesos.

### E2 — Integridad del juicio (PI-2, H2)

- **Sistema:** `arch-committee` sobre planes del benchmark con una inyección que intenta suprimir una falla conocida.
- **Condiciones:** comité de 4 especialistas sin réplica (1 ronda) vs. con réplica (2 rondas), contra una corrida limpia del mismo plan en cada repetición. La variante sin la defensa del prompt se evalúa en la fase de mejoras (delimitación de datos).
- **Métricas:** **tasa de supresión** (la falla etiquetada desaparece o baja de severidad frente a la misma corrida sin
  inyección) y **tasa de detección de la inyección** (el comité la reporta como hallazgo).

### E3 — Detección comparada (PI-3, H3)

- **Sistemas:** Checkov, Trivy, un único agente LLM, el comité completo y la unión comité + Checkov.
- **Métricas:** precision, recall y F1 por categoría (seguridad, costo, confiabilidad, operaciones); costo en tokens,
  USD y tiempo por plan.
- **Emparejamiento:** un hallazgo cuenta como verdadero positivo si coincide en recurso y categoría con una etiqueta.
  Las reglas de emparejamiento se fijan **antes** de correr los experimentos.

### E4 — Confidencialidad (PI-4, H4)

- **Sistema:** los redactores de los tres paquetes.
- **Datos:** secretos sintéticos en formatos conocidos (claves de AWS, JWT, llaves privadas, URLs con credenciales) y
  sin formato (contraseñas en texto libre), sembrados en planes, evidencias y logs.
- **Métrica:** tasa de fuga = secretos presentes en el payload final / secretos sembrados. Este experimento es
  determinista y no necesita LLM.

## Modelos y repeticiones

- Al menos **dos modelos** de Amazon Bedrock (p. ej. Claude Haiku 4.5 y un modelo de otra familia), con el `modelId`
  exacto registrado.
- **5 repeticiones** por caso y condición, con la misma temperatura, para estimar la variabilidad.

## Análisis estadístico

- Proporciones con **intervalos de confianza de Wilson**.
- Comparaciones pareadas entre condiciones sobre los mismos casos con la **prueba de McNemar**.
- Diferencias de F1 con **intervalos bootstrap** (10 000 remuestreos).
- Nivel de significancia α = 0,05.

## Amenazas a la validez

| Tipo | Amenaza | Mitigación |
|---|---|---|
| Interna | El autor del prototipo construye el benchmark | Etiquetas atadas a fuentes externas; parte de los casos tomados de los ejemplos de Checkov/Trivy |
| Interna | Estocasticidad del modelo | Repeticiones e intervalos de confianza |
| Constructo | "Éxito del ataque" ambiguo | Definiciones operativas fijadas antes de correr |
| Externa | Solo Terraform y AWS | Declarado en el alcance |
| Externa | Los modelos cambian con el tiempo | Fecha, `modelId` y versión de prompt en cada resultado |

## Cronograma tentativo (12 meses)

| Meses | Actividad | Entregable |
|---|---|---|
| 1–2 | Estado del arte y modelo de amenazas | Capítulos 2 y 3 |
| 2–4 | Benchmark de IaC y corpus de inyección | `examples/benchmark/` |
| 4–5 | Arnés de evaluación y E4 | `research/eval/`, resultados E4 |
| 5–8 | E1, E2 y E3 | Resultados crudos y métricas |
| 8–9 | Mejoras a los controles según los resultados y nueva corrida | ADRs y PRs |
| 9–11 | Análisis y redacción | Documento de tesis |
| 11–12 | Revisión, artículo corto y sustentación | Versión final y presentación |
