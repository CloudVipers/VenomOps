package rules

import (
	"context"
	"fmt"
	"strings"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

// CrashLoopBackOff reports containers that keep crashing and being restarted by the kubelet.
type CrashLoopBackOff struct{ Namespace string }

// ID implements engine.Rule.
func (CrashLoopBackOff) ID() string { return "KD-K8S-001" }

// Description implements engine.Rule.
func (CrashLoopBackOff) Description() string {
	return "Containers in CrashLoopBackOff, with the last exit code and log lines"
}

const logLines = 10

// exitCodeMeaning explains the most common container exit codes in plain language.
func exitCodeMeaning(code int32) string {
	switch code {
	case 0:
		return "El proceso terminó con código 0: el contenedor no mantiene un proceso en primer plano (o es un comando que termina y el Pod no es un Job)."
	case 1:
		return "La aplicación terminó con un error (código 1). Las últimas líneas del log suelen indicar el motivo."
	case 2:
		return "El proceso terminó por un uso incorrecto del comando o de sus argumentos (código 2)."
	case 126:
		return "El comando del contenedor existe pero no se puede ejecutar (código 126): permisos o formato de binario incorrectos."
	case 127:
		return "El comando del contenedor no se encontró (código 127): revisa command/args y que el binario exista en la imagen."
	case 137:
		return "El proceso fue terminado con SIGKILL (código 137), típicamente por falta de memoria (OOM) o por un kill forzado."
	case 139:
		return "El proceso falló con un segmentation fault (código 139, SIGSEGV)."
	case 143:
		return "El proceso recibió SIGTERM (código 143) y se detuvo; revisa si una probe o un despliegue lo está reiniciando."
	default:
		return fmt.Sprintf("El proceso terminó con el código de salida %d; consulta los logs del contenedor.", code)
	}
}

// crashLoopThreshold is how many restarts make a container that is momentarily in a "Terminated"
// (Error) state count as crash-looping: the kubelet only shows Waiting/CrashLoopBackOff during the
// back-off window, so a snapshot taken between restarts would otherwise be missed.
const crashLoopThreshold = 3

func isCrashLooping(cs corev1.ContainerStatus) bool {
	if cs.State.Waiting != nil && cs.State.Waiting.Reason == "CrashLoopBackOff" {
		return true
	}
	t := cs.State.Terminated
	return t != nil && t.ExitCode != 0 && cs.RestartCount >= crashLoopThreshold
}

// usableLogs reports whether the text is a real log. The kubelet answers "unable to retrieve container
// logs for containerd://..." with a 200 body when the previous container instance was garbage
// collected, so that message must not be presented as evidence.
func usableLogs(logs string) bool {
	logs = strings.TrimSpace(logs)
	return logs != "" && !strings.HasPrefix(logs, "unable to retrieve container logs")
}

// Check implements engine.Rule.
func (r CrashLoopBackOff) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pods {
		pod := &pods[i]
		for _, cs := range containerStatuses(pod) {
			if !isCrashLooping(cs.ContainerStatus) {
				continue
			}
			if oomTerminated(cs.ContainerStatus) != nil {
				continue // killed for memory: reported by KD-K8S-002, with the actionable fix
			}
			ev := []findings.Evidence{
				{Kind: "pod-status", Detail: fmt.Sprintf("%s %q crash-looping, restartCount=%d", kindLabel(cs.Init), cs.Name, cs.RestartCount)},
			}
			rootCause := "El contenedor se cae repetidamente y el kubelet lo reinicia con espera creciente."
			last := cs.LastTerminationState.Terminated
			if last == nil {
				last = cs.State.Terminated // snapshot taken right after a crash, before the back-off state
			}
			if last != nil {
				ev = append(ev, findings.Evidence{Kind: "exit-code", Detail: fmt.Sprintf("Last state: Terminated, exitCode=%d, reason=%q", last.ExitCode, last.Reason)})
				rootCause = exitCodeMeaning(last.ExitCode)
			}
			logs, lerr := cluster.PodLogs(ctx, pod.Namespace, pod.Name, cs.Name, true, logLines)
			if lerr != nil || !usableLogs(logs) {
				// The previous instance may be gone; fall back to the current one.
				logs, lerr = cluster.PodLogs(ctx, pod.Namespace, pod.Name, cs.Name, false, logLines)
			}
			if lerr == nil && usableLogs(logs) {
				ev = append(ev, findings.Evidence{Kind: "log-tail", Detail: logTail(logs, logLines)})
			} else {
				ev = append(ev, findings.Evidence{Kind: "log-tail", Detail: "logs no disponibles para este contenedor"})
			}

			sev := findings.SeverityHigh
			if cs.RestartCount < 3 {
				sev = findings.SeverityMedium
			}
			out = append(out, newFinding(r.ID(), sev,
				fmt.Sprintf("CrashLoopBackOff en el %s %s", kindLabel(cs.Init), cs.Name),
				pod, ev, rootCause,
				findings.SuggestedFix{
					Summary: "Corregir la causa de la caída que muestran el código de salida y los logs.",
					Steps: []string{
						fmt.Sprintf("kubectl logs %s -c %s --previous para ver el log de la última caída.", podLabel(pod), cs.Name),
						fmt.Sprintf("kubectl describe pod %s para revisar eventos, probes y variables de entorno.", podLabel(pod)),
						"Corregir configuración, imagen o comando según la causa y volver a desplegar.",
					},
				},
				findings.RiskLow,
				[]string{"https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/"},
				[]string{"kubernetes", "availability"},
			))
		}
	}
	return out, nil
}
