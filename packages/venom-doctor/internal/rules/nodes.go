package rules

import (
	"context"
	"fmt"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/redact"
)

// NodeNotReady reports nodes that are not Ready, and Ready nodes that report resource pressure.
// It is cluster-scoped: Default only enables it when no namespace was requested.
type NodeNotReady struct{}

// ID implements engine.Rule.
func (NodeNotReady) ID() string { return "KD-K8S-006" }

// Description implements engine.Rule.
func (NodeNotReady) Description() string {
	return "Nodes NotReady (kubelet stopped, CNI, container runtime) or under memory/disk/PID pressure"
}

type nodeCause int

const (
	nodeUnknown nodeCause = iota
	nodeKubeletStopped
	nodeCNI
	nodePLEG
	nodeRuntime
	nodePressure
)

func classifyNode(ready corev1.NodeCondition) nodeCause {
	text := strings.ToLower(ready.Reason + " " + ready.Message)
	has := func(subs ...string) bool {
		for _, s := range subs {
			if strings.Contains(text, s) {
				return true
			}
		}
		return false
	}
	switch {
	// Status Unknown comes from the node lifecycle controller when the kubelet stops posting heartbeats.
	case ready.Status == corev1.ConditionUnknown || has("nodestatusunknown", "kubelet stopped posting"):
		return nodeKubeletStopped
	case has("cni plugin not initialized", "networkplugin", "network plugin", "networkready=false"):
		return nodeCNI
	case has("pleg is not healthy"):
		return nodePLEG
	case has("container runtime is down", "container runtime not ready", "runtime is not ready", "containerruntime"):
		return nodeRuntime
	case has("pressure"):
		return nodePressure
	default:
		return nodeUnknown
	}
}

func describeNode(c nodeCause) (rootCause string, steps []string, tag string) {
	switch c {
	case nodeKubeletStopped:
		return "El nodo dejó de reportar su estado: el kubelet se detuvo o el nodo perdió conectividad con el control plane (instancia caída, kernel panic, red o seguridad).",
			[]string{"Comprobar el estado de la instancia en EC2 (status checks) o en tu proveedor, y la salida de consola si está disponible.", "Si el nodo es de un grupo gestionado (node group o ASG), terminar la instancia para que se reemplace: las cargas se reprogramarán en otros nodos.", "Si es recurrente, revisar security groups y rutas entre el nodo y el endpoint del API server, y el agotamiento de memoria o disco del nodo."},
			"kubelet-stopped"
	case nodeCNI:
		return "El plugin de red (CNI) no está inicializado en el nodo: sin red de Pods el kubelet marca el nodo como no listo (en EKS suele ser aws-node / VPC CNI).",
			[]string{"Revisar el DaemonSet del CNI (en EKS: kubectl -n kube-system get pods -l k8s-app=aws-node) y sus logs en ese nodo.", "En EKS comprobar el rol IAM del CNI y que la subred no se haya quedado sin direcciones IP libres.", "Si el nodo es nuevo, esperar unos minutos tras el arranque; si no se recupera, reemplazar el nodo."},
			"cni-not-ready"
	case nodePLEG:
		return "El PLEG (Pod Lifecycle Event Generator) del kubelet no está sano: el runtime de contenedores va lento o el nodo está sobrecargado (demasiados Pods o contenedores, disco o CPU saturados).",
			[]string{"Revisar la carga del nodo (CPU, disco, número de Pods) y los logs del kubelet (journalctl -u kubelet).", "Reducir la densidad de Pods en ese nodo o usar un tipo de instancia mayor.", "Reiniciar el runtime o reemplazar el nodo si el problema persiste."},
			"pleg-unhealthy"
	case nodeRuntime:
		return "El runtime de contenedores del nodo no responde o está caído, por lo que el kubelet no puede gestionar Pods.",
			[]string{"Revisar el estado y los logs del runtime en el nodo (containerd: systemctl status containerd, journalctl -u containerd).", "Comprobar espacio en disco y memoria del nodo.", "Reemplazar el nodo si el runtime no se recupera."},
			"container-runtime-down"
	case nodePressure:
		return "El nodo reporta presión de recursos (memoria, disco o PIDs) y el kubelet empezó a rechazar o desalojar Pods.",
			[]string{"Identificar qué recurso está bajo presión (ver las condiciones del nodo) y qué Pods lo consumen.", "Liberar o ampliar el recurso (disco mayor, limpiar imágenes y logs, ajustar requests y limits).", "Añadir capacidad al clúster para repartir la carga."},
			"node-resource-pressure"
	default:
		return "El nodo no está Ready y el mensaje del kubelet no permite distinguir la causa exacta.",
			[]string{"Revisar el detalle con kubectl describe node (condiciones y eventos).", "Revisar los logs del kubelet en el nodo (journalctl -u kubelet)."},
			"node-not-ready"
	}
}

// pressureConditions lists the node conditions that signal trouble, in a fixed order (the output must be stable).
var pressureConditions = []struct {
	Type corev1.NodeConditionType
	Tag  string
}{
	{corev1.NodeMemoryPressure, "memory-pressure"},
	{corev1.NodeDiskPressure, "disk-pressure"},
	{corev1.NodePIDPressure, "pid-pressure"},
	{corev1.NodeNetworkUnavailable, "network-unavailable"},
}

func nodeCondition(n *corev1.Node, t corev1.NodeConditionType) *corev1.NodeCondition {
	for i := range n.Status.Conditions {
		if n.Status.Conditions[i].Type == t {
			return &n.Status.Conditions[i]
		}
	}
	return nil
}

func sinceLabel(t time.Time) string {
	if t.IsZero() {
		return "desconocido"
	}
	d := now().Sub(t).Round(time.Minute)
	if d < 0 {
		d = 0
	}
	return d.String()
}

// podsOnNode counts the pods scheduled on each node, best effort (a failure only means less context).
func podsOnNode(ctx context.Context, cluster engine.ClusterReader) map[string]int {
	pods, err := cluster.ListPods(ctx, "")
	if err != nil {
		return nil
	}
	counts := map[string]int{}
	for i := range pods {
		if n := pods[i].Spec.NodeName; n != "" {
			counts[n]++
		}
	}
	return counts
}

// Check implements engine.Rule.
func (r NodeNotReady) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	nodes, err := cluster.ListNodes(ctx)
	if err != nil {
		return nil, err
	}
	var counts map[string]int
	var countsLoaded bool
	var out []findings.Finding
	for i := range nodes {
		node := &nodes[i]
		res := findings.Resource{Type: "Node", Name: node.Name}
		ready := nodeCondition(node, corev1.NodeReady)

		if ready != nil && ready.Status != corev1.ConditionTrue {
			if !countsLoaded {
				counts, countsLoaded = podsOnNode(ctx, cluster), true
			}
			cause := classifyNode(*ready)
			rootCause, steps, tag := describeNode(cause)
			ev := []findings.Evidence{
				{Kind: "node-condition", Detail: fmt.Sprintf("Ready=%s, reason=%s, desde hace %s", ready.Status, ready.Reason, sinceLabel(ready.LastTransitionTime.Time))},
			}
			if ready.Message != "" {
				ev = append(ev, findings.Evidence{Kind: "kubelet-message", Detail: redact.String(truncate(ready.Message, 300))})
			}
			if counts != nil {
				ev = append(ev, findings.Evidence{Kind: "pods-on-node", Detail: podCount(counts[node.Name]) + " programado(s) en este nodo"})
			}
			tags := []string{"node", "not-ready", tag}
			for _, pc := range pressureConditions {
				if c := nodeCondition(node, pc.Type); c != nil && c.Status == corev1.ConditionTrue {
					tags = append(tags, pc.Tag)
				}
			}
			if node.Spec.Unschedulable {
				ev = append(ev, findings.Evidence{Kind: "node-spec", Detail: "El nodo está acordonado (unschedulable)"})
			}
			out = append(out, newFindingFor(r.ID(), findings.SeverityHigh,
				"El nodo "+node.Name+" no está Ready", res, ev, rootCause,
				findings.SuggestedFix{Summary: "Recuperar o reemplazar el nodo; sus Pods se reprograman en otros nodos.", Steps: steps},
				findings.RiskMedium,
				[]string{
					"https://kubernetes.io/docs/tasks/debug/debug-cluster/",
					"https://kubernetes.io/docs/concepts/architecture/nodes/#condition",
				}, tags))
			continue
		}

		// A Ready node can still be about to evict Pods: report resource pressure on its own.
		for _, pc := range pressureConditions[:3] { // memory, disk and PID: NetworkUnavailable is not an eviction signal
			c := nodeCondition(node, pc.Type)
			if c == nil || c.Status != corev1.ConditionTrue {
				continue
			}
			rootCause, steps, tag := describeNode(nodePressure)
			ev := []findings.Evidence{
				{Kind: "node-condition", Detail: fmt.Sprintf("%s=True, reason=%s, desde hace %s", c.Type, c.Reason, sinceLabel(c.LastTransitionTime.Time))},
			}
			if c.Message != "" {
				ev = append(ev, findings.Evidence{Kind: "kubelet-message", Detail: redact.String(truncate(c.Message, 300))})
			}
			out = append(out, newFindingFor(r.ID(), findings.SeverityMedium,
				fmt.Sprintf("El nodo %s tiene %s", node.Name, pc.Tag), res, ev, rootCause,
				findings.SuggestedFix{Summary: "Liberar o ampliar el recurso bajo presión antes de que el kubelet desaloje Pods.", Steps: steps},
				findings.RiskLow,
				[]string{"https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/"},
				[]string{"node", pc.Tag, tag}))
		}
	}
	return out, nil
}
