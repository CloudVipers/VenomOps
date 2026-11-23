# ADR 0012 — Firma opcional de findings (HMAC-SHA256 separada)

- **Estado:** aceptado
- **Fecha:** 2026-11-23
- **Alcance:** `findings-schema` (utilidad de firma en Go y Python), `venom-doctor`, `arch-committee` y `pr-agent`.
  No modifica el schema del finding.

## Contexto

El [modelo de amenazas](../investigacion/modelo-de-amenazas.md) marca un riesgo residual medio: **los findings no
autentican su origen**. La validación del schema comprueba la forma, no quién escribió el documento. Por eso, un
finding falso o modificado (p. ej. con `source: venom-doctor`) podría guiar a `pr-agent` hacia un cambio que nadie
pidió.

## Decisión

1. **Firma separada** (`<archivo>.sig`) en lugar de un campo nuevo en el finding. El contrato no cambia, los findings
   sin firmar siguen siendo válidos y no hace falta una nueva versión del schema.
2. **HMAC-SHA256 sobre los bytes exactos del archivo.** No hay forma canónica que mantener entre Go y Python: cada
   productor firma lo que escribe. Una prueba de paridad comparte el mismo vector en Go y Python.
3. **Clave compartida en `VENOMOPS_FINDINGS_KEY`**, de al menos 32 bytes. La firma incluye un `key_id` (los primeros
   12 caracteres hexadecimales del SHA-256 de la clave) para saber con qué clave se firmó sin revelarla.
4. **Productores:** `venom-doctor -o json --sign-to <archivo.sig>` firma exactamente lo que escribe en stdout.
   `arch-committee` firma su `findings.json` cuando la clave está definida.
5. **Consumidor:** `pr-agent --require-signature` rechaza un finding sin firma válida. Para leer desde stdin se pasa
   `--signature`. Sin el flag, si existe una firma y la clave está disponible, también se verifica: un documento
   alterado nunca se acepta en silencio.

## Alternativas descartadas

- **Firma asimétrica (Ed25519, Sigstore):** da no repudio y permite verificar sin compartir un secreto, pero exige
  gestionar pares de claves o una identidad OIDC. Es el siguiente paso natural si los findings cruzan límites de
  confianza entre equipos.
- **Campo `signature` dentro del finding:** obliga a una forma canónica del JSON y a cambiar el contrato (ADR 0001).

## Consecuencias

- HMAC prueba integridad y que el documento lo produjo **un poseedor de la clave**; no distingue entre poseedores.
- Quien necesite exigir la firma debe distribuir la clave a productores y consumidores, como cualquier secreto de CI.
- La firma es opcional y está desactivada por defecto: no cambia el comportamiento de quien no la usa.
