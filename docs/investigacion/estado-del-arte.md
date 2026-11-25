# Estado del arte (borrador)

> Borrador para la postulación. Antes de citar, verificar cada referencia en la fuente original y completar los datos
> bibliográficos según la norma que exija el programa (APA o IEEE).

## 1. Seguridad de la infraestructura como código

- **Code smells de seguridad en IaC.** Rahman, Parnin y Williams, *The Seven Sins: Security Smells in Infrastructure as
  Code Scripts* (ICSE 2019): catálogo empírico de malas prácticas (secretos en el código, permisos excesivos, etc.).
- **Análisis estático de IaC:** Checkov, Trivy (que absorbió tfsec), KICS y Terrascan. Aplican reglas por recurso
  (políticas) sobre HCL o sobre el plan. Son deterministas y baratos, pero no razonan sobre el contexto (entorno,
  costo frente a confiabilidad, relaciones entre recursos).
- **Benchmarks de referencia:** CIS AWS Foundations Benchmark y AWS Well-Architected (pilares de seguridad, confiabilidad
  y costo), usados para etiquetar las fallas del benchmark.

**Brecha:** las herramientas estáticas detectan bien las fallas de un solo recurso; hay poca evaluación comparativa con
revisores basados en LLM sobre un mismo benchmark etiquetado.

## 2. Inyección de prompt y agentes con herramientas

- **Inyección de prompt indirecta.** Greshake et al., *Not what you've signed up for: Compromising Real-World
  LLM-Integrated Applications with Indirect Prompt Injection* (2023): las instrucciones llegan por los datos que la
  aplicación recupera, no por el usuario.
- **Benchmarks de agentes.** AgentDojo (Debenedetti et al., 2024): entorno dinámico para medir ataques y defensas de
  inyección en agentes con herramientas, con métricas de utilidad y de tasa de éxito del ataque.
- **Marcos de riesgo.** OWASP Top 10 para aplicaciones LLM (2025), MITRE ATLAS y NIST AI RMF.
- **Defensas propuestas en la literatura:** delimitar datos (*spotlighting*), detectores de inyección, separar
  privilegios entre un modelo planificador y uno que lee datos no confiables, y controles fuera del modelo
  (permisos mínimos, confirmación humana).

**Brecha:** la mayoría de los benchmarks usan dominios genéricos (correo, banca, viajes). Hay poca evidencia sobre
agentes que **ejecutan herramientas de infraestructura**, donde el daño es destructivo, y casi nada sobre la
**supresión de hallazgos** en revisores automáticos.

## 3. Sistemas multiagente para revisión

- Debate y réplica entre agentes como técnica para mejorar la factualidad y reducir errores de un único modelo.
- Comités especialistas con moderador: el riesgo es que una inyección afecte a todos los agentes que leen el mismo dato.

**Brecha:** no se ha medido si la réplica entre especialistas actúa como defensa frente a la inyección.

## 4. Posicionamiento de este trabajo

| Aspecto | Herramientas estáticas | Benchmarks de inyección genéricos | Este trabajo |
|---|---|---|---|
| Dominio | IaC | Correo, banca, viajes | IaC y Kubernetes |
| Agente con herramientas destructivas | No aplica | Simuladas | Reales, con controles en código |
| Mide supresión de hallazgos | No | No | Sí (PI-2) |
| Compara LLM vs. reglas | No | No | Sí (PI-3) |
| Prototipo abierto | Sí | Sí | Sí (Apache 2.0) |
