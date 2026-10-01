# Manifiestos rotos para probar kdoctor

Cada manifiesto provoca a propósito uno de los escenarios del MVP. **Aplícalos solo en un clúster local
(kind)**, nunca en uno real, y con un kubeconfig dedicado para no tocar tus contextos habituales:

```bash
export KUBECONFIG=/tmp/kdoctor-kind.kubeconfig
kind create cluster --name kdoctor-test --kubeconfig "$KUBECONFIG"
kubectl apply -f examples/k8s/
kubectl venom-doctor -n kdoctor-demo          # o: go run ./cmd/kubectl-venom_doctor -n kdoctor-demo
kind delete cluster --name kdoctor-test --kubeconfig "$KUBECONFIG"
```

| Archivo | Regla | Qué debe detectar |
|---|---|---|
| `10-crashloopbackoff.yaml` | KD-K8S-001 | CrashLoopBackOff con exit code 1 y la línea del log |
| `20-oomkilled.yaml` | KD-K8S-002 | OOMKilled con `limits.memory=32Mi` y un nuevo valor sugerido |
| `30-imagepullbackoff.yaml` | KD-K8S-003 | Imagen o tag inexistente |
| `40-pending.yaml` | KD-K8S-004 | Sin CPU suficiente y PVC inexistente |
