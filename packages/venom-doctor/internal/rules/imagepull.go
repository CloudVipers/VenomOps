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

// ImagePullBackOff reports containers whose image cannot be pulled and says why.
type ImagePullBackOff struct{ Namespace string }

// ID implements engine.Rule.
func (ImagePullBackOff) ID() string { return "VD-K8S-003" }

// Description implements engine.Rule.
func (ImagePullBackOff) Description() string {
	return "Containers in ImagePullBackOff/ErrImagePull: missing image, credentials or rate limit"
}

type pullCause int

const (
	causeUnknown pullCause = iota
	causeNotFound
	causeAuth
	causeNotFoundOrAuth
	causeRateLimit
	causeNetwork
)

// classifyPull inspects the kubelet/runtime message (and related events) to tell apart the usual causes.
func classifyPull(text string) pullCause {
	t := strings.ToLower(text)
	has := func(subs ...string) bool {
		for _, s := range subs {
			if strings.Contains(t, s) {
				return true
			}
		}
		return false
	}
	switch {
	case has("toomanyrequests", "too many requests", "rate limit", "429"):
		return causeRateLimit
	// Docker Hub says "repository does not exist or may require 'docker login'": ambiguous by design.
	case has("repository does not exist or may require", "does not exist or may require"):
		return causeNotFoundOrAuth
	case has("unauthorized", "authentication required", "no basic auth credentials", "pull access denied", "forbidden", "403"):
		return causeAuth
	case has("not found", "manifest unknown", "name unknown", "no such image", "repository does not exist"):
		return causeNotFound
	case has("no such host", "i/o timeout", "connection refused", "dial tcp", "tls handshake", "network is unreachable"):
		return causeNetwork
	default:
		return causeUnknown
	}
}

func describePull(c pullCause) (rootCause string, steps []string, tags string) {
	switch c {
	case causeNotFound:
		return "La imagen o el tag no existe en el registro (nombre o tag mal escrito, o la imagen nunca se publicó).",
			[]string{"Verificar el nombre completo y el tag de la imagen en el manifiesto.", "Confirmar en el registro que ese tag existe (por ejemplo con docker manifest inspect o la consola del registro).", "Corregir la referencia de la imagen y volver a desplegar."},
			"image-not-found"
	case causeAuth:
		return "El registro rechazó la autenticación: faltan credenciales (imagePullSecrets) o no tienen permiso para esa imagen.",
			[]string{"Crear un Secret de tipo docker-registry con credenciales válidas en el namespace del Pod.", "Referenciarlo en spec.imagePullSecrets (o en el ServiceAccount).", "En ECR, comprobar que el rol/credencial del nodo tiene ecr:GetAuthorizationToken y permisos de lectura sobre el repositorio."},
			"registry-credentials"
	case causeNotFoundOrAuth:
		return "El registro responde que la imagen no existe o que requiere autorización; con registros públicos como Docker Hub ambas causas se ven igual.",
			[]string{"Verificar que el nombre de la imagen y el tag son correctos.", "Si el repositorio es privado, configurar imagePullSecrets con credenciales válidas."},
			"image-not-found-or-credentials"
	case causeRateLimit:
		return "El registro limitó las descargas (rate limit, por ejemplo Docker Hub para cuentas anónimas).",
			[]string{"Autenticarse en el registro con imagePullSecrets para subir el límite.", "Usar un registro espejo o un pull-through cache (por ejemplo ECR) para imágenes públicas.", "Reintentar más tarde si el límite es temporal."},
			"registry-rate-limit"
	case causeNetwork:
		return "El nodo no pudo contactar al registro (DNS, red, proxy o firewall).",
			[]string{"Comprobar desde el nodo la resolución DNS y la salida hacia el registro (NAT Gateway, security groups, proxy).", "Si usas un registro privado por VPC endpoint, revisar rutas y políticas."},
			"registry-network"
	default:
		return "No se pudo descargar la imagen; el mensaje del runtime no permite distinguir la causa exacta.",
			[]string{"Revisar el mensaje completo con kubectl describe pod y los eventos del Pod.", "Probar el pull manualmente en un nodo para ver el error del registro."},
			"image-pull-unknown"
	}
}

// Check implements engine.Rule.
func (r ImagePullBackOff) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pods {
		pod := &pods[i]
		var events []corev1.Event
		var eventsLoaded bool
		for _, cs := range containerStatuses(pod) {
			w := cs.State.Waiting
			if w == nil || (w.Reason != "ImagePullBackOff" && w.Reason != "ErrImagePull") {
				continue
			}
			if !eventsLoaded {
				events, _ = cluster.PodEvents(ctx, pod.Namespace, pod.Name) // events are best-effort context
				eventsLoaded = true
			}
			text := w.Message
			var evMsgs []string
			for _, e := range events {
				if e.Reason == "Failed" || e.Reason == "BackOff" || e.Reason == "ErrImagePull" {
					text += " " + e.Message
					evMsgs = append(evMsgs, e.Message)
				}
			}
			cause := classifyPull(text)
			rootCause, steps, tag := describePull(cause)

			ev := []findings.Evidence{
				{Kind: "pod-status", Detail: fmt.Sprintf("%s %q en %s, image=%s", kindLabel(cs.Init), cs.Name, w.Reason, cs.Image)},
			}
			if w.Message != "" {
				ev = append(ev, findings.Evidence{Kind: "waiting-message", Detail: redact.String(truncate(w.Message, 300))})
			}
			if len(evMsgs) > 0 {
				ev = append(ev, findings.Evidence{Kind: "event", Detail: redact.String(truncate(evMsgs[len(evMsgs)-1], 300))})
			}
			out = append(out, newFinding(r.ID(), findings.SeverityHigh,
				fmt.Sprintf("No se puede descargar la imagen del %s %s", kindLabel(cs.Init), cs.Name),
				pod, ev, rootCause,
				findings.SuggestedFix{Summary: "Resolver la causa del pull fallido y volver a desplegar.", Steps: steps},
				findings.RiskLow,
				[]string{
					"https://kubernetes.io/docs/concepts/containers/images/",
					"https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/",
				},
				[]string{"kubernetes", "image", tag},
			))
		}
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}
