package rules

import (
	"context"
	"fmt"
	"strings"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/redact"
)

// Pending reports pods the scheduler could not place: insufficient resources, taints, selectors or
// PersistentVolumeClaims that are not bound.
type Pending struct{ Namespace string }

// ID implements engine.Rule.
func (Pending) ID() string { return "VD-K8S-004" }

// Description implements engine.Rule.
func (Pending) Description() string {
	return "Pods stuck in Pending: scheduler events (resources, taints, selectors) and unbound PVCs"
}

func unschedulable(pod *corev1.Pod) (corev1.PodCondition, bool) {
	if pod.Status.Phase != corev1.PodPending {
		return corev1.PodCondition{}, false
	}
	for _, c := range pod.Status.Conditions {
		if c.Type == corev1.PodScheduled && c.Status == corev1.ConditionFalse {
			return c, true
		}
	}
	return corev1.PodCondition{}, false
}

// Check implements engine.Rule.
func (r Pending) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pods {
		pod := &pods[i]
		cond, ok := unschedulable(pod)
		if !ok {
			continue
		}

		message := cond.Message
		events, _ := cluster.PodEvents(ctx, pod.Namespace, pod.Name)
		for _, e := range events {
			if e.Reason == "FailedScheduling" {
				message += " " + e.Message
			}
		}
		lower := strings.ToLower(message)

		var causes, steps []string
		var tags = []string{"kubernetes", "scheduling"}
		add := func(cause string, st ...string) { causes = append(causes, cause); steps = append(steps, st...) }

		if strings.Contains(lower, "insufficient cpu") || strings.Contains(lower, "insufficient memory") {
			var res []string
			for _, name := range []string{"cpu", "memory"} {
				if strings.Contains(lower, "insufficient "+name) {
					res = append(res, name)
				}
			}
			add(fmt.Sprintf("ningún nodo tiene %s suficiente para los requests del Pod", strings.Join(res, " ni ")),
				"Reducir resources.requests del Pod o agregar capacidad (más nodos o nodos más grandes).",
				"Si usas un autoscaler (Cluster Autoscaler/Karpenter), revisar por qué no escala.")
			tags = append(tags, "insufficient-resources")
		}
		if strings.Contains(lower, "taint") {
			add("los nodos tienen taints que el Pod no tolera",
				"Agregar tolerations al Pod para esos taints, o usar nodos sin ese taint.")
			tags = append(tags, "taints")
		}
		if strings.Contains(lower, "node affinity") || strings.Contains(lower, "node selector") || strings.Contains(lower, "nodeselector") {
			add("ningún nodo cumple el nodeSelector o la afinidad del Pod",
				"Revisar nodeSelector/affinity del Pod y las labels de los nodos (kubectl get nodes --show-labels).")
			tags = append(tags, "affinity")
		}

		ev := []findings.Evidence{{Kind: "pod-condition", Detail: fmt.Sprintf("PodScheduled=False reason=%s", cond.Reason)}}
		if cond.Message != "" {
			ev = append(ev, findings.Evidence{Kind: "scheduler-message", Detail: redact.String(truncate(cond.Message, 300))})
		}

		// PVCs referenced by the Pod that are not bound.
		for _, v := range pod.Spec.Volumes {
			if v.PersistentVolumeClaim == nil {
				continue
			}
			pvc, err := cluster.GetPVC(ctx, pod.Namespace, v.PersistentVolumeClaim.ClaimName)
			if err != nil {
				ev = append(ev, findings.Evidence{Kind: "pvc", Detail: fmt.Sprintf("PVC %q no se pudo leer (¿no existe?)", v.PersistentVolumeClaim.ClaimName)})
				add(fmt.Sprintf("el PersistentVolumeClaim %q no existe", v.PersistentVolumeClaim.ClaimName),
					fmt.Sprintf("Crear el PVC %q en el namespace %s o corregir el nombre en el Pod.", v.PersistentVolumeClaim.ClaimName, pod.Namespace))
				tags = append(tags, "pvc")
				continue
			}
			if pvc.Status.Phase != corev1.ClaimBound {
				ev = append(ev, findings.Evidence{Kind: "pvc", Detail: fmt.Sprintf("PVC %q en fase %s (no está Bound)", pvc.Name, pvc.Status.Phase)})
				add(fmt.Sprintf("el PersistentVolumeClaim %q no está enlazado (Bound) a ningún volumen", pvc.Name),
					"Verificar que existe una StorageClass válida (o un PV que coincida) y revisar los eventos del PVC con kubectl describe pvc.",
					"Con WaitForFirstConsumer, confirmar que hay un nodo en la zona del volumen donde el Pod pueda ejecutarse.")
				tags = append(tags, "pvc")
			}
		}

		rootCause := "El scheduler no pudo asignar el Pod a ningún nodo."
		if len(causes) > 0 {
			rootCause = "El scheduler no pudo asignar el Pod: " + strings.Join(causes, "; ") + "."
		} else {
			steps = []string{"Revisar el mensaje del scheduler con kubectl describe pod y los eventos del namespace."}
		}
		out = append(out, newFinding(r.ID(), findings.SeverityHigh,
			"Pod en Pending: el scheduler no puede programarlo",
			pod, ev, rootCause,
			findings.SuggestedFix{Summary: "Resolver lo que impide que el scheduler coloque el Pod.", Steps: steps},
			findings.RiskLow,
			[]string{
				"https://kubernetes.io/docs/concepts/scheduling-eviction/",
				"https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/",
				"https://kubernetes.io/docs/concepts/storage/persistent-volumes/",
			},
			tags,
		))
	}
	return out, nil
}
