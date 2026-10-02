# Manifiestos rotos para probar venom-doctor

Cada manifiesto provoca a propósito uno de los escenarios del MVP. **Aplícalos solo en un clúster local
(kind)**, nunca en uno real, y con un kubeconfig dedicado para no tocar tus contextos habituales:

```bash
export KUBECONFIG=/tmp/venom-doctor-kind.kubeconfig
kind create cluster --name venom-doctor-test --kubeconfig "$KUBECONFIG"
kubectl apply -f examples/k8s/
kubectl venom-doctor -n venom-demo          # o: go run ./cmd/kubectl-venom_doctor -n venom-demo
kind delete cluster --name venom-doctor-test --kubeconfig "$KUBECONFIG"
```

| Archivo | Regla | Qué debe detectar |
|---|---|---|
| `10-crashloopbackoff.yaml` | KD-K8S-001 | CrashLoopBackOff con exit code 1 y la línea del log |
| `20-oomkilled.yaml` | KD-K8S-002 | OOMKilled con `limits.memory=32Mi` y un nuevo valor sugerido |
| `30-imagepullbackoff.yaml` | KD-K8S-003 | Imagen o tag inexistente |
| `40-pending.yaml` | KD-K8S-004 | Sin CPU suficiente y PVC inexistente |
| `50-probes.yaml` | KD-K8S-005 | Readiness con `connection refused` y liveness con 404 que reinicia el contenedor |
| `60-pdb.yaml` | KD-K8S-007 | Cuatro PDB que estorban a un drain: réplica única, Pods no sanos, `minAvailable` mayor que las réplicas y selector huérfano |

## Nodo NotReady (KD-K8S-006)

No hay manifiesto: un nodo no se rompe con YAML. Con un clúster kind de dos nodos (`role: worker`) se provoca parando
el contenedor del worker y esperando ~40 s a que el control plane lo marque:

```bash
docker stop <cluster>-worker
kubectl venom-doctor -A    # la regla de nodos solo corre con -A
```
