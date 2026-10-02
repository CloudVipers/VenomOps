package rules

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/redact"
)

// KarpenterCapacity reports Karpenter objects that keep the cluster from getting capacity: NodePools that are not Ready
// or have reached their limits, and NodeClaims stuck before the node became usable. It reads the documented
// karpenter.sh/v1 API and shows the reason and message each object reports verbatim, without interpreting them.
// It is cluster-scoped (only runs for a cluster-wide diagnosis) and silent when Karpenter is not installed.
type KarpenterCapacity struct{}

var (
	nodePoolGVR  = schema.GroupVersionResource{Group: "karpenter.sh", Version: "v1", Resource: "nodepools"}
	nodeClaimGVR = schema.GroupVersionResource{Group: "karpenter.sh", Version: "v1", Resource: "nodeclaims"}
)

// ID implements engine.Rule.
func (KarpenterCapacity) ID() string { return "KD-K8S-008" }

// Description implements engine.Rule.
func (KarpenterCapacity) Description() string {
	return "Karpenter without capacity: NodePools not Ready or at their limits, and NodeClaims stuck before the node is usable"
}

// Heuristics: how long a NodeClaim may sit in a stage before it is worth a finding. Creating an instance takes
// seconds, so a failed launch shows up early; registration is documented to be abandoned by Karpenter after 15 minutes.
const (
	launchStuckAfter = 3 * time.Minute
	stageStuckAfter  = 10 * time.Minute
)

type condition struct{ Type, Status, Reason, Message string }

func conditionsOf(obj *unstructured.Unstructured) []condition {
	raw, _, _ := unstructured.NestedSlice(obj.Object, "status", "conditions")
	var out []condition
	for _, r := range raw {
		m, ok := r.(map[string]any)
		if !ok {
			continue
		}
		str := func(k string) string { v, _ := m[k].(string); return v }
		out = append(out, condition{str("type"), str("status"), str("reason"), str("message")})
	}
	return out
}

func findCondition(cs []condition, t string) (condition, bool) {
	for _, c := range cs {
		if c.Type == t {
			return c, true
		}
	}
	return condition{}, false
}

func describeCondition(c condition) string {
	d := fmt.Sprintf("%s=%s", c.Type, c.Status)
	if c.Reason != "" {
		d += ", reason=" + c.Reason
	}
	if c.Message != "" {
		d += ": " + redact.String(truncate(c.Message, 300))
	}
	return d
}

func karpenterRefs(extra ...string) []string {
	return append([]string{"https://karpenter.sh/docs/"}, extra...)
}

// Check implements engine.Rule.
func (r KarpenterCapacity) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pools, installed, err := cluster.ListCustomResources(ctx, nodePoolGVR)
	if err != nil {
		return nil, err
	}
	if !installed {
		return nil, nil // Karpenter is not in this cluster
	}
	claims, _, err := cluster.ListCustomResources(ctx, nodeClaimGVR)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pools {
		out = append(out, r.checkNodePool(&pools[i])...)
	}
	for i := range claims {
		out = append(out, r.checkNodeClaim(&claims[i])...)
	}
	return out, nil
}

func (r KarpenterCapacity) checkNodePool(pool *unstructured.Unstructured) []findings.Finding {
	var out []findings.Finding
	res := findings.Resource{Type: "NodePool", Name: pool.GetName()}
	conds := conditionsOf(pool)

	// A NodePool that is not Ready "will not be considered for scheduling" (Karpenter docs): no new nodes from it.
	if ready, ok := findCondition(conds, "Ready"); ok && ready.Status == "False" {
		ev := []findings.Evidence{{Kind: "nodepool-condition", Detail: describeCondition(ready)}}
		for _, t := range []string{"NodeClassReady", "ValidationSucceeded", "NodeRegistrationHealthy"} {
			if c, ok := findCondition(conds, t); ok && c.Status == "False" {
				ev = append(ev, findings.Evidence{Kind: "nodepool-condition", Detail: describeCondition(c)})
			}
		}
		out = append(out, newFindingFor(r.ID(), findings.SeverityHigh,
			"El NodePool "+pool.GetName()+" no está Ready", res, ev,
			"Karpenter no tiene en cuenta un NodePool que no está Ready: los Pods que dependían de él no recibirán nodos nuevos. Las condiciones de arriba indican qué parte falla.",
			findings.SuggestedFix{Summary: "Resolver la condición que deja el NodePool sin Ready.", Steps: []string{
				"Revisar las condiciones fallidas con kubectl describe nodepool " + pool.GetName() + ".",
				"Si falla NodeClassReady, revisar el EC2NodeClass que referencia (subnets, security groups, rol IAM, AMI).",
				"Si falla ValidationSucceeded, corregir la especificación del NodePool que indica el mensaje.",
				"Si falla NodeRegistrationHealthy, los nodos que lanza no llegan a registrarse: revisar rol IAM del nodo, security groups y bootstrap.",
				"Revisar los logs del controlador de Karpenter para ver el detalle."}},
			findings.RiskLow,
			karpenterRefs("https://karpenter.sh/docs/concepts/nodepools/"),
			[]string{"karpenter", "nodepool", "nodepool-not-ready"}))
	}

	// Limits reached: "nodes provisioning is prevented until some nodes have been terminated" (Karpenter docs).
	limits, _, _ := unstructured.NestedStringMap(pool.Object, "spec", "limits")
	used, _, _ := unstructured.NestedStringMap(pool.Object, "status", "resources")
	var names []string
	for name := range limits {
		names = append(names, name)
	}
	sort.Strings(names)
	var reached []string
	for _, name := range names {
		u, ok := used[name]
		if !ok {
			continue
		}
		lq, err1 := resource.ParseQuantity(limits[name])
		uq, err2 := resource.ParseQuantity(u)
		if err1 != nil || err2 != nil || lq.IsZero() {
			continue
		}
		if uq.Cmp(lq) >= 0 {
			reached = append(reached, fmt.Sprintf("%s: en uso %s de un límite de %s", name, u, limits[name]))
		}
	}
	if len(reached) > 0 {
		ev := []findings.Evidence{{Kind: "nodepool-limits", Detail: strings.Join(reached, "; ")}}
		out = append(out, newFindingFor(r.ID(), findings.SeverityMedium,
			"El NodePool "+pool.GetName()+" alcanzó su límite", res, ev,
			"El NodePool llegó a spec.limits, así que Karpenter no aprovisiona más nodos con él hasta que se liberen recursos: los Pods nuevos quedarán Pending.",
			findings.SuggestedFix{Summary: "Subir el límite del NodePool o liberar capacidad.", Steps: []string{
				"Decidir si el límite es un tope de coste deliberado; si lo es, reducir la demanda (requests de los Pods) o consolidar cargas.",
				"Si no, subir spec.limits del NodePool (cada nodo extra aumenta el coste).",
				"Tener en cuenta que el control de límites es eventualmente consistente y puede sobrepasarse ligeramente en escaladas rápidas."}},
			findings.RiskMedium,
			karpenterRefs("https://karpenter.sh/docs/concepts/nodepools/"),
			[]string{"karpenter", "nodepool", "nodepool-limits"}))
	}
	return out
}

// stuckStage returns the first lifecycle stage that is not True, with how long the claim has been waiting for it.
func stuckStage(conds []condition) (condition, string, bool) {
	for _, stage := range []string{"Launched", "Registered", "Initialized"} {
		c, ok := findCondition(conds, stage)
		if !ok || c.Status != "True" {
			if !ok {
				c = condition{Type: stage, Status: "Unknown"}
			}
			return c, stage, true
		}
	}
	return condition{}, "", false
}

func (r KarpenterCapacity) checkNodeClaim(claim *unstructured.Unstructured) []findings.Finding {
	if claim.GetDeletionTimestamp() != nil {
		return nil // being removed: Karpenter is already handling it
	}
	conds := conditionsOf(claim)
	if ready, ok := findCondition(conds, "Ready"); ok && ready.Status == "True" {
		return nil
	}
	stage, name, ok := stuckStage(conds)
	if !ok {
		return nil
	}
	age := now().Sub(claim.GetCreationTimestamp().Time)
	limit := stageStuckAfter
	if name == "Launched" {
		limit = launchStuckAfter
	}
	if age < limit {
		return nil // still within the normal time to reach this stage
	}

	rootCause, steps, tag := describeClaimStage(name)
	ev := []findings.Evidence{
		{Kind: "nodeclaim-condition", Detail: describeCondition(stage)},
		{Kind: "nodeclaim-age", Detail: fmt.Sprintf("creado hace %s y todavía sin pasar la etapa %s", age.Round(time.Minute), name)},
	}
	if pool := claim.GetLabels()["karpenter.sh/nodepool"]; pool != "" {
		ev = append(ev, findings.Evidence{Kind: "nodepool", Detail: pool})
	}
	if node, _, _ := unstructured.NestedString(claim.Object, "status", "nodeName"); node != "" {
		ev = append(ev, findings.Evidence{Kind: "node", Detail: node})
	}
	return []findings.Finding{newFindingFor(r.ID(), findings.SeverityHigh,
		fmt.Sprintf("El NodeClaim %s lleva atascado en %s", claim.GetName(), name),
		findings.Resource{Type: "NodeClaim", Name: claim.GetName()}, ev, rootCause,
		findings.SuggestedFix{Summary: "Averiguar por qué la capacidad pedida no llega a estar disponible.", Steps: steps},
		findings.RiskLow,
		karpenterRefs("https://karpenter.sh/docs/concepts/nodeclaims/"),
		[]string{"karpenter", "nodeclaim", tag})}
}

// describeClaimStage explains each lifecycle stage using the definitions in the Karpenter docs.
func describeClaimStage(stage string) (rootCause string, steps []string, tag string) {
	switch stage {
	case "Launched":
		return "Karpenter todavía no logra crear la instancia en el proveedor (condición Launched sin cumplirse). El mensaje de la condición indica el motivo que reporta Karpenter.",
			[]string{"Leer el reason y el mensaje de la condición Launched: es lo que Karpenter reporta del proveedor.", "Si apunta a capacidad, probar con más tipos de instancia o zonas en el NodePool; si apunta a cuotas o permisos, revisar los service quotas y el rol IAM del controlador.", "Revisar el EC2NodeClass referenciado y los logs del controlador de Karpenter."},
			"nodeclaim-launch-stuck"
	case "Registered":
		return "La instancia se creó, pero no llegó a unirse al clúster como Node (condición Registered sin cumplirse). Karpenter abandona los NodeClaims que no se registran en 15 minutos.",
			[]string{"Comprobar que el rol IAM del nodo está autorizado en el clúster (access entries o aws-auth).", "Revisar security groups y rutas entre el nodo y el endpoint del API server, y el user data o bootstrap de la AMI.", "Mirar la consola de la instancia (EC2) para ver si el kubelet llegó a arrancar."},
			"nodeclaim-registration-stuck"
	default:
		return "El nodo se registró pero no terminó de inicializarse (condición Initialized sin cumplirse): sigue con taints de arranque o le faltan recursos por registrar (CNI, device plugins).",
			[]string{"Revisar los taints del nodo y qué DaemonSets deben retirarlos.", "Comprobar que el CNI y los plugins de dispositivos están corriendo en ese nodo.", "Revisar el detalle con kubectl describe node sobre el nodo asociado."},
			"nodeclaim-initialization-stuck"
	}
}
