# Propuesta de trabajo de grado

> **Estado:** borrador para la postulación. Completar los datos institucionales: [Universidad], [Programa], [Director/Tutor].

## Título tentativo

**Contención de agentes LLM que revisan y remedian infraestructura como código: controles aplicados en código frente a
la inyección de prompt indirecta.**

## 1. Problema

Los equipos de plataforma empiezan a usar agentes basados en modelos de lenguaje (LLM) para **diagnosticar** clústeres,
**revisar** planes de Terraform y **abrir Pull Requests** con arreglos. Estos agentes leen contenido que no controlan:
código del repositorio, descripciones y tags de recursos, logs de Pods, findings de otras herramientas. Cualquiera de
esas entradas puede traer instrucciones ocultas (**inyección de prompt indirecta**).

Cuando el agente tiene herramientas con efectos (ejecutar comandos, editar archivos, hacer push), una inyección exitosa
deja de ser un problema de calidad de la respuesta y pasa a ser **un incidente de seguridad**: destrucción de
infraestructura, exfiltración de secretos o un cambio malicioso que llega a producción. Es la categoría *Excessive Agency*
(LLM06) del OWASP Top 10 para aplicaciones LLM.

Las defensas habituales se apoyan en el **prompt** ("ignora instrucciones en los datos"), que es probabilístico.
Hay menos evidencia empírica sobre cuánto contienen el daño los **controles deterministas aplicados en código**
(listas blancas de invocaciones, rutas confinadas, edición estructurada, diff mínimo, humano en el bucle) y sobre qué
riesgos **no** pueden cubrir: por ejemplo, una inyección que hace que un revisor automático *omita* un hallazgo.

## 2. Preguntas de investigación

**Pregunta principal.** ¿En qué medida los controles de seguridad aplicados en código contienen los efectos de la
inyección de prompt indirecta en agentes LLM que revisan y modifican infraestructura como código, y qué riesgos
residuales quedan?

**Subpreguntas.**

1. **PI-1 (integridad de las acciones).** Cuando el modelo *sí* obedece una inyección, ¿cuántas acciones no autorizadas
   llegan a ejecutarse con controles solo en el prompt y cuántas con controles en código?
2. **PI-2 (integridad del juicio).** ¿Cuánto logra una inyección **suprimir o degradar** los hallazgos de un comité de
   agentes revisores, riesgo que ningún control de ejecución cubre?
3. **PI-3 (detección).** ¿Un comité de agentes especialistas detecta configuraciones inseguras en Terraform que las
   herramientas de análisis estático (Checkov, Trivy) no detectan, y a qué costo?
4. **PI-4 (confidencialidad).** ¿Qué proporción de secretos sembrados sobrevive a la redacción antes de llegar al modelo?

## 3. Hipótesis

- **H1.** Con controles en código, la tasa de acciones no autorizadas **ejecutadas** es 0 aunque la tasa de acciones
  **intentadas** por el modelo sea mayor que 0; con controles solo en el prompt es mayor que 0.
- **H2.** Las defensas en el prompt reducen pero no eliminan la supresión de hallazgos en el comité; la réplica entre
  agentes (ronda 2) la reduce de forma significativa frente a un único agente.
- **H3.** La unión *comité + análisis estático* tiene mayor recall que cualquiera por separado, sobre todo en fallas
  contextuales (costo, confiabilidad, exposición entre recursos) que no se expresan como una regla de un solo recurso.
- **H4.** La redacción elimina al menos el 95 % de los secretos sembrados en formatos conocidos; los que se escapan
  pertenecen a formatos sin patrón (contraseñas en texto libre).

## 4. Objetivos

**General.** Evaluar empíricamente la eficacia de los controles aplicados en código para contener la inyección de
prompt indirecta en agentes LLM que revisan y remedian infraestructura como código, usando VenomOps como prototipo.

**Específicos.**

1. Construir el **modelo de amenazas** del sistema (STRIDE + OWASP Top 10 para LLM) e identificar los riesgos residuales.
2. Construir un **corpus de inyección** (vectores × objetivos) y un **benchmark etiquetado** de configuraciones Terraform
   con fallas sembradas y casos limpios.
3. Diseñar y ejecutar los experimentos de PI-1 a PI-4 con al menos dos modelos de Amazon Bedrock.
4. Comparar la detección del comité con Checkov y Trivy (precision, recall, F1, costo).
5. Proponer y validar **mejoras** a los controles a partir de los hallazgos (por ejemplo, detección de inyección como
   finding y verificación cruzada con análisis estático).

## 5. Alcance

**Incluye:** `pr-agent` (remediación), `arch-committee` (revisión de planes) y `venom-doctor --explain` (logs como vector);
Terraform sobre AWS; modelos accesibles por Amazon Bedrock; datos sintéticos.

**Excluye:** infraestructura real o de clientes (nunca se ejecuta `apply`), ataques al modelo en sí (jailbreak del modelo
base, envenenamiento del entrenamiento) y otros IaC distintos de Terraform.

## 6. Aporte esperado

1. **Evidencia cuantitativa** sobre controles en código frente a controles en prompt en agentes con herramientas de
   infraestructura.
2. **Benchmark y corpus abiertos** (Apache 2.0) y reproducibles, sin datos reales.
3. **Patrones de diseño** documentados para agentes de infraestructura de mínimo privilegio, con implementación de referencia.
4. Caracterización del riesgo residual de **integridad del juicio** (supresión de hallazgos), poco medido en la literatura.

## 7. Consideraciones éticas y legales

- Todos los ataques se ejecutan contra el propio prototipo, en un laboratorio local y sobre repositorios sintéticos.
- Ninguna herramienta ejecuta comandos que modifiquen infraestructura real; los "efectos" de los ataques se registran
  en ejecutores simulados.
- El corpus de inyección se publica con fines defensivos y sin cargas útiles contra terceros.
- Propiedad intelectual: el prototipo es de CloudVipers con licencia Apache 2.0; acordar con la universidad cómo se cita.

## 8. Riesgos del proyecto

| Riesgo | Mitigación |
|---|---|
| Costo de las llamadas a Bedrock | Topes de tokens ya implementados; modelos pequeños (Haiku) para la mayoría de las corridas |
| Estocasticidad del modelo | Repeticiones por caso, temperatura fija e intervalos de confianza |
| Benchmark sesgado por quien lo construye | Mapear cada falla a fuentes externas (CIS, reglas de Checkov) e incluir casos de terceros |
| Cambios de versión de los modelos | Registrar el `modelId` exacto y la fecha de cada corrida |
