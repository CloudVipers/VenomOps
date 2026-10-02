package rules

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/util/intstr"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
)

// PDBBlocksDrain reports PodDisruptionBudgets that allow no disruptions (so `kubectl drain` and node upgrades hang),
// and budgets whose selector matches no pods.
type PDBBlocksDrain struct{ Namespace string }

// ID implements engine.Rule.
func (PDBBlocksDrain) ID() string { return "VD-K8S-007" }

// Description implements engine.Rule.
func (PDBBlocksDrain) Description() string {
	return "PodDisruptionBudgets that block drains: no disruptions allowed, unhealthy pods, or a selector matching nothing"
}

func intOrString(v *intstr.IntOrString) string {
	if v == nil {
		return "-"
	}
	return v.String()
}

type pdbProblem int

const (
	pdbAlwaysBlocks pdbProblem = iota // healthy workload, but the budget leaves no room to evict anything
	pdbUnhealthy                      // fewer healthy pods than the budget requires
	pdbImpossible                     // the budget demands more pods than exist: it can never be satisfied
	pdbNoPods                         // selector matches no pods
)

func describePDB(p pdbProblem) (title, rootCause string, steps []string, tag string, sev findings.Severity, risk findings.Risk) {
	switch p {
	case pdbUnhealthy:
		return "El PDB %s no permite desalojos porque faltan Pods sanos",
			"Hay menos Pods sanos que los que exige el presupuesto, así que no se puede desalojar ninguno: un drain o una actualización de nodos se quedará esperando hasta que el workload se recupere.",
			[]string{"Resolver primero por qué los Pods no están sanos (CrashLoopBackOff, probes, Pending: revisa los demás findings de este namespace).", "No relajes el PDB para forzar el drain: lo que lo bloquea es el workload degradado.", "Si hace falta mantenimiento urgente, valorar escalar el workload antes de drenar."},
			"pdb-unhealthy-pods", findings.SeverityMedium, findings.RiskLow
	case pdbImpossible:
		return "El PDB %s exige más Pods de los que existen",
			"El presupuesto pide más Pods disponibles (minAvailable) que los que tiene el workload, así que es imposible de cumplir y no se podrá desalojar ningún Pod mientras exista.",
			[]string{"Bajar minAvailable por debajo del número de réplicas, o subir las réplicas del workload por encima de minAvailable.", "Preferir maxUnavailable: 1 (o un porcentaje) a un minAvailable fijo, para que el presupuesto escale con las réplicas.", "Comprobar que el PDB apunta al workload correcto."},
			"pdb-impossible", findings.SeverityMedium, findings.RiskMedium
	case pdbNoPods:
		return "El selector del PDB %s no coincide con ningún Pod",
			"El PDB no protege nada: su selector no coincide con ningún Pod (etiquetas cambiadas, workload escalado a cero o PDB huérfano).",
			[]string{"Comparar spec.selector del PDB con las etiquetas de los Pods del workload que debería proteger.", "Si el workload ya no existe, eliminar el PDB para no dejar protecciones engañosas.", "Si está escalado a cero a propósito, ignorar este hallazgo."},
			"pdb-selector-matches-nothing", findings.SeverityLow, findings.RiskLow
	default:
		return "El PDB %s no deja desalojar ningún Pod aunque estén sanos",
			"Los Pods están sanos pero el presupuesto exige que todos sigan disponibles (por ejemplo minAvailable igual al número de réplicas, maxUnavailable: 0 o un workload de una sola réplica): ningún drain ni actualización de nodos podrá terminar.",
			[]string{"Subir las réplicas del workload por encima de lo que exige minAvailable, o bajar minAvailable.", "Preferir maxUnavailable: 1 (o un porcentaje) a minAvailable fijo, para que el presupuesto escale con las réplicas.", "Para un workload de una sola réplica, un PDB con minAvailable: 1 bloquea siempre el drain: elimínalo o sube a 2 réplicas."},
			"pdb-blocks-drain", findings.SeverityMedium, findings.RiskMedium
	}
}

// Check implements engine.Rule.
func (r PDBBlocksDrain) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	pdbs, err := cluster.ListPDBs(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	var out []findings.Finding
	for i := range pdbs {
		pdb := &pdbs[i]
		st := pdb.Status
		if st.ObservedGeneration == 0 {
			continue // the disruption controller has not processed it yet: its zeros mean "unknown", not "blocked"
		}
		var problem pdbProblem
		switch {
		case st.ExpectedPods == 0:
			problem = pdbNoPods
		case st.DisruptionsAllowed > 0:
			continue
		case st.DesiredHealthy > st.ExpectedPods:
			problem = pdbImpossible
		case st.CurrentHealthy < st.DesiredHealthy:
			problem = pdbUnhealthy
		default:
			problem = pdbAlwaysBlocks
		}
		title, rootCause, steps, tag, sev, risk := describePDB(problem)
		ev := []findings.Evidence{
			{Kind: "pdb-status", Detail: fmt.Sprintf("disruptionsAllowed=%d, currentHealthy=%d, desiredHealthy=%d, expectedPods=%d",
				st.DisruptionsAllowed, st.CurrentHealthy, st.DesiredHealthy, st.ExpectedPods)},
			{Kind: "pdb-spec", Detail: fmt.Sprintf("minAvailable=%s, maxUnavailable=%s", intOrString(pdb.Spec.MinAvailable), intOrString(pdb.Spec.MaxUnavailable))},
		}
		res := findings.Resource{Type: "PodDisruptionBudget", Name: pdb.Name, Namespace: pdb.Namespace}
		out = append(out, newFindingFor(r.ID(), sev, fmt.Sprintf(title, pdb.Name), res, ev, rootCause,
			findings.SuggestedFix{Summary: "Ajustar el PDB o el workload para que el mantenimiento de nodos pueda avanzar.", Steps: steps},
			risk,
			[]string{
				"https://kubernetes.io/docs/tasks/run-application/configure-pdb/",
				"https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/",
			},
			[]string{"pdb", "drain", tag}))
	}
	return out, nil
}
