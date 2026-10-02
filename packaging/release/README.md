# Publicar un release

`publish-release.sh <versión>` publica los dos releases de GitHub de una versión (`venom-doctor-vX.Y.Z` y `venom-vX.Y.Z`) a partir de
los artefactos ya construidos en local (`packages/venom-doctor/dist` con GoReleaser y `packaging/dist` con `packaging/build.sh`; los
paquetes arm64 del workflow `release-venom.yml` se añaden solos si están en `packaging/dist`). Lo ejecuta una persona: los
releases son públicos.

```bash
./packaging/release/publish-release.sh 0.1.4 --dry-run   # solo comprobaciones, no publica nada
./packaging/release/publish-release.sh 0.1.4             # comprueba, pide confirmación y publica
```

**Antes de publicar comprueba:** que están todos los archivos, que los `checksums.txt` coinciden con ellos, que el manifiesto de
krew (`venom-doctor.yaml`) apunta a esta versión y que sus hashes son los de **estos** archivos (o siguen en ceros, pendientes de
rellenar), que `gh` tiene acceso al repositorio y que los releases no existen ya. Un hash distinto en el manifiesto significa que
se reconstruyó después de rellenarlo: los binarios de Go no salen idénticos al reconstruir.

**Después:** verifica las descargas públicas contra el manifiesto e imprime los pasos siguientes (rellenar los hashes del manifiesto
si estaban en ceros, actualizar el PR a krew-index y lanzar `publish-repo.yml` para el repositorio firmado).

No borres releases que referencie el manifiesto de krew o un PR abierto: no se pueden recrear con los mismos hashes.
