# Comprobación del manifiesto de krew

`check_manifest.py` descarga cada archivo que referencia el manifiesto de krew (`packages/venom-doctor/venom-doctor.yaml`), compara su
`sha256` y comprueba que el binario del plugin está en la raíz del archivo. Cubre lo que rompe una instalación con
`kubectl krew install` y la validación de los mantenedores de krew-index: un release borrado (404), un archivo reconstruido
con otro hash, un manifiesto con el `sha256` todavía en ceros o un archivo sin el binario.

```bash
python3 packaging/krew/check_manifest.py                 # el manifiesto del repo
python3 packaging/krew/check_manifest.py otro.yaml       # otro manifiesto (por ejemplo el de un PR a krew-index)
python3 -m pytest -q packaging/krew                      # tests (servidor HTTP local, sin red)
```

El workflow [`check-krew-manifest.yml`](../../.github/workflows/check-krew-manifest.yml) lo ejecuta cada lunes, al publicarse
un release y a demanda, y los tests en los PR que tocan este directorio. Si falla, el manifiesto no es instalable tal como está:
normalmente falta publicar el release de esa versión o hay que regenerar los hashes.

**Por qué existe:** los binarios de Go **no se reconstruyen con el mismo hash** (al volver a compilar el mismo tag salieron
hashes distintos), así que un release borrado no se puede recrear con los mismos archivos y hay que actualizar el manifiesto a una
versión nueva. No borres releases que referencie el manifiesto o un PR abierto a krew-index.
