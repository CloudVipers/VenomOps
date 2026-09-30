package rules

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

// OOMKilled reports containers killed for exceeding their memory limit (or the node's memory).
type OOMKilled struct{ Namespace string }

// ID implements engine.Rule.
func (OOMKilled) ID() string { return "KD-K8S-002" }

// Description implements engine.Rule.
func (OOMKilled) Description() string {
	return "Containers terminated by OOMKilled, comparing limits.memory with observed usage"
}

const mi = int64(1024 * 1024)

func roundUpMi(bytes int64) int64 {
	return (bytes + mi - 1) / mi * mi
}

// suggestLimit proposes a new memory limit in bytes: 1.5x the observed usage when usage is close to
// the limit, otherwise double the current limit. The value is a starting point, not a measurement.
func suggestLimit(limit, usage int64, haveUsage bool) int64 {
	if haveUsage && limit > 0 && usage*100 >= limit*80 {
		return roundUpMi(usage + usage/2)
	}
	return roundUpMi(limit * 2)
}

func oomTerminated(cs corev1.ContainerStatus) *corev1.ContainerStateTerminated {
	if t := cs.LastTerminationState.Terminated; t != nil && t.Reason == "OOMKilled" {
		return t
	}
	if t := cs.State.Terminated; t != nil && t.Reason == "OOMKilled" {
		return t
	}
	return nil
}

// Check implements engine.Rule.
func (r OOMKilled) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pods {
		pod := &pods[i]
		var usage map[string]int64
		var haveMetrics, metricsLoaded bool
		for _, cs := range containerStatuses(pod) {
			term := oomTerminated(cs.ContainerStatus)
			if term == nil {
				continue
			}
			if !metricsLoaded {
				// Metrics are optional: a failure here must not hide the OOM finding itself.
				usage, haveMetrics, _ = cluster.PodMemoryUsage(ctx, pod.Namespace, pod.Name)
				metricsLoaded = true
			}

			ev := []findings.Evidence{
				{Kind: "last-state", Detail: fmt.Sprintf("%s %q Terminated reason=OOMKilled, exitCode=%d, restartCount=%d", kindLabel(cs.Init), cs.Name, term.ExitCode, cs.RestartCount)},
			}
			var limit int64
			if spec := containerSpec(pod, cs.Name); spec != nil {
				if q, ok := spec.Resources.Limits[corev1.ResourceMemory]; ok {
					limit = q.Value()
				}
			}
			u, haveUsage := usage[cs.Name]
			haveUsage = haveUsage && haveMetrics

			var rootCause string
			var fix findings.SuggestedFix
			if limit == 0 {
				rootCause = "El contenedor no define limits.memory: fue terminado porque el nodo se quedó sin memoria. Sin requests/limits el Pod compite sin control con el resto."
				fix = findings.SuggestedFix{
					Summary: "Definir requests y limits de memoria acordes al consumo real.",
					Steps: []string{
						"Medir el consumo real (kubectl top pod o tu herramienta de métricas).",
						"Definir resources.requests.memory y resources.limits.memory con margen sobre el pico.",
					},
				}
			} else {
				ev = append(ev, findings.Evidence{Kind: "limits", Detail: fmt.Sprintf("limits.memory=%dMi", limit/mi)})
				if haveUsage {
					ev = append(ev, findings.Evidence{Kind: "usage", Detail: fmt.Sprintf("uso actual de memoria=%dMi (metrics-server)", u/mi)})
				}
				suggested := suggestLimit(limit, u, haveUsage)
				rootCause = fmt.Sprintf("El contenedor superó su límite de memoria de %dMi y el kernel lo terminó (OOMKilled).", limit/mi)
				fix = findings.SuggestedFix{
					Summary: fmt.Sprintf("Subir limits.memory de %dMi a %dMi (punto de partida).", limit/mi, suggested/mi),
					Steps: []string{
						fmt.Sprintf("Actualizar resources.limits.memory a %dMi en el manifiesto del workload.", suggested/mi),
						"Verificar que no vuelva a ocurrir y ajustar con el consumo observado; si crece sin parar, buscar una fuga de memoria.",
					},
					IaCHint: fmt.Sprintf("resources { limits = { memory = %q } }", fmt.Sprintf("%dMi", suggested/mi)),
				}
			}

			sev := findings.SeverityMedium
			if cs.RestartCount >= 3 {
				sev = findings.SeverityHigh
			}
			out = append(out, newFinding(r.ID(), sev,
				fmt.Sprintf("OOMKilled en el %s %s", kindLabel(cs.Init), cs.Name),
				pod, ev, rootCause, fix, findings.RiskLow,
				[]string{"https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/"},
				[]string{"kubernetes", "memory"},
			))
		}
	}
	return out, nil
}
