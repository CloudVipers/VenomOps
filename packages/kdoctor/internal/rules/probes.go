package rules

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/redact"
)

// ProbeFailures reports liveness, readiness and startup probes that keep failing, and says why.
type ProbeFailures struct{ Namespace string }

// ID implements engine.Rule.
func (ProbeFailures) ID() string { return "KD-K8S-005" }

// Description implements engine.Rule.
func (ProbeFailures) Description() string {
	return "Liveness/readiness/startup probes failing: wrong port or path, timeouts, or an app that is not ready"
}

var probeEventRe = regexp.MustCompile(`(?i)^(liveness|readiness|startup) probe failed: ?(.*)$`)

// restartsToBeHigh is the restart count from which a failing liveness/startup probe is treated as HIGH.
const restartsToBeHigh = 3

type probeCause int

const (
	probeUnknown probeCause = iota
	probeRefused
	probeTimeout
	probeNotFound
	probeServerError
	probeExec
)

// classifyProbe tells the usual probe failures apart from the kubelet's message.
func classifyProbe(msg string) probeCause {
	t := strings.ToLower(msg)
	has := func(subs ...string) bool {
		for _, s := range subs {
			if strings.Contains(t, s) {
				return true
			}
		}
		return false
	}
	switch {
	case has("connection refused", "connect: connection refused"):
		return probeRefused
	case has("deadline exceeded", "timeout", "timed out"):
		return probeTimeout
	case has("statuscode: 404", "status code 404"):
		return probeNotFound
	case has("statuscode: 5", "status code 5"):
		return probeServerError
	case has("command", "exec", "exited with", "unable to upgrade"):
		return probeExec
	default:
		return probeUnknown
	}
}

func describeProbe(kind string, c probeCause) (rootCause string, steps []string, tag string) {
	switch c {
	case probeRefused:
		return "La probe no logra conectar: la aplicación todavía no escucha en ese puerto (arranque lento) o el puerto de la probe es incorrecto.",
			[]string{"Comprobar que el puerto de la probe coincide con el que realmente abre la aplicación (containerPort / configuración de la app).", "Si la app tarda en arrancar, añadir una startupProbe o subir initialDelaySeconds / failureThreshold.", "Revisar los logs del contenedor para ver si la aplicación llegó a iniciar."},
			"probe-connection-refused"
	case probeTimeout:
		return "La aplicación responde, pero más lento que el timeoutSeconds de la probe (carga alta, arranque costoso o dependencias lentas).",
			[]string{"Subir timeoutSeconds (el valor por defecto es 1 segundo) y, si hace falta, periodSeconds.", "Usar un endpoint de salud ligero que no dependa de bases de datos u otros servicios externos.", "Revisar si el contenedor está limitado de CPU (limits.cpu) y se está throttleando."},
			"probe-timeout"
	case probeNotFound:
		return "El endpoint de la probe devuelve 404: la ruta configurada no existe en la aplicación.",
			[]string{"Corregir el path de la probe para que coincida con el endpoint de salud real de la aplicación.", "Verificar si la ruta cambió entre versiones de la imagen."},
			"probe-http-404"
	case probeServerError:
		return "El endpoint de la probe responde con un error 5xx: la aplicación está arriba pero se reporta no saludable (a menudo una dependencia caída).",
			[]string{"Revisar los logs de la aplicación en el momento del fallo.", "Si el endpoint comprueba dependencias externas, considerar separar la liveness (¿está vivo el proceso?) de la readiness (¿puede atender tráfico?).", "Evitar que la liveness dependa de servicios externos: un fallo ajeno reiniciaría los Pods en cascada."},
			"probe-http-5xx"
	case probeExec:
		return "El comando de la probe termina con error: el comando no existe en la imagen o devuelve un código distinto de cero.",
			[]string{"Ejecutar el mismo comando dentro del contenedor (kubectl exec en un entorno de prueba) para ver su salida.", "Confirmar que el binario del comando existe en la imagen y que el usuario del contenedor puede ejecutarlo."},
			"probe-exec"
	default:
		return "La probe falla y el mensaje del kubelet no permite distinguir la causa exacta.",
			[]string{"Revisar el mensaje completo con kubectl describe pod y los logs del contenedor.", "Comprobar puerto, path y tiempos (initialDelaySeconds, timeoutSeconds, failureThreshold) de la probe."},
			"probe-unknown"
	}
}

// probeFor returns the probe of the given kind configured on a container.
func probeFor(c *corev1.Container, kind string) *corev1.Probe {
	switch kind {
	case "liveness":
		return c.LivenessProbe
	case "readiness":
		return c.ReadinessProbe
	default:
		return c.StartupProbe
	}
}

func orDefault(v, def int32) int32 {
	if v == 0 {
		return def
	}
	return v
}

// describeProbeConfig renders the probe as a short, secret-free line (never the exec command's arguments).
func describeProbeConfig(p *corev1.Probe) string {
	var target string
	switch {
	case p.HTTPGet != nil:
		target = fmt.Sprintf("httpGet %s:%s", p.HTTPGet.Path, p.HTTPGet.Port.String())
	case p.TCPSocket != nil:
		target = "tcpSocket :" + p.TCPSocket.Port.String()
	case p.Exec != nil:
		target = "exec"
	case p.GRPC != nil:
		target = fmt.Sprintf("grpc :%d", p.GRPC.Port)
	default:
		target = "handler desconocido"
	}
	return fmt.Sprintf("%s, initialDelaySeconds=%d, periodSeconds=%d, timeoutSeconds=%d, failureThreshold=%d",
		target, p.InitialDelaySeconds, orDefault(p.PeriodSeconds, 10), orDefault(p.TimeoutSeconds, 1), orDefault(p.FailureThreshold, 3))
}

func statusByName(pod *corev1.Pod, name string) (corev1.ContainerStatus, bool) {
	for _, cs := range pod.Status.ContainerStatuses {
		if cs.Name == name {
			return cs, true
		}
	}
	return corev1.ContainerStatus{}, false
}

// containerFromEvent extracts "app" from a fieldPath like "spec.containers{app}".
func containerFromEvent(e corev1.Event) string {
	fp := e.InvolvedObject.FieldPath
	if i := strings.Index(fp, "{"); i >= 0 && strings.HasSuffix(fp, "}") {
		return fp[i+1 : len(fp)-1]
	}
	return ""
}

// stillFailing confirms with the CURRENT container state that the probe is a live problem, not a stale event
// (events live about an hour, and a slow start would otherwise be flagged long after it recovered).
func stillFailing(kind string, cs corev1.ContainerStatus) bool {
	switch kind {
	case "readiness":
		return !cs.Ready
	case "liveness":
		return cs.RestartCount > 0
	default: // startup
		return (cs.Started != nil && !*cs.Started) || cs.RestartCount > 0
	}
}

type probeKey struct{ container, kind string }

// Check implements engine.Rule.
func (r ProbeFailures) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pods {
		pod := &pods[i]
		if pod.Status.Phase != corev1.PodRunning {
			continue
		}
		events, _ := cluster.PodEvents(ctx, pod.Namespace, pod.Name) // events are best-effort context
		type seen struct {
			msg   string
			count int32
		}
		latest := map[probeKey]*seen{}
		var order []probeKey
		for _, e := range events {
			if e.Reason != "Unhealthy" {
				continue
			}
			m := probeEventRe.FindStringSubmatch(strings.TrimSpace(e.Message))
			if m == nil {
				continue
			}
			kind := strings.ToLower(m[1])
			name := containerFromEvent(e)
			if name == "" && len(pod.Spec.Containers) == 1 {
				name = pod.Spec.Containers[0].Name
			}
			if name == "" {
				continue // cannot tell which container: better to say nothing than to blame the wrong one
			}
			k := probeKey{name, kind}
			s := latest[k]
			if s == nil {
				s = &seen{}
				latest[k] = s
				order = append(order, k)
			}
			s.msg = m[2]
			if e.Count > 0 {
				s.count += e.Count
			} else {
				s.count++
			}
		}
		for _, k := range order {
			cs, ok := statusByName(pod, k.container)
			spec := containerSpec(pod, k.container)
			if !ok || spec == nil || !stillFailing(k.kind, cs) {
				continue
			}
			s := latest[k]
			cause := classifyProbe(s.msg)
			rootCause, steps, tag := describeProbe(k.kind, cause)

			sev := findings.SeverityMedium
			if k.kind != "readiness" && cs.RestartCount >= restartsToBeHigh {
				sev = findings.SeverityHigh
			}
			ev := []findings.Evidence{
				{Kind: "pod-status", Detail: fmt.Sprintf("container %q: ready=%t, restartCount=%d", k.container, cs.Ready, cs.RestartCount)},
				{Kind: "event", Detail: redact.String(truncate(fmt.Sprintf("%s probe failed (x%d): %s", k.kind, s.count, s.msg), 300))},
			}
			if p := probeFor(spec, k.kind); p != nil {
				ev = append(ev, findings.Evidence{Kind: "probe-config", Detail: k.kind + ": " + describeProbeConfig(p)})
			}
			consequence := "El Pod queda fuera del Service hasta que la probe pase."
			if k.kind != "readiness" {
				consequence = "El kubelet reinicia el contenedor cada vez que la probe falla."
			}
			out = append(out, newFinding(r.ID(), sev,
				fmt.Sprintf("La probe de %s falla en el container %s", k.kind, k.container),
				pod, ev, rootCause+" "+consequence,
				findings.SuggestedFix{Summary: "Ajustar la probe o la aplicación para que el chequeo de salud pase.", Steps: steps},
				findings.RiskLow,
				[]string{
					"https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
				},
				[]string{"probes", k.kind + "-probe", tag}))
		}
	}
	return out, nil
}
