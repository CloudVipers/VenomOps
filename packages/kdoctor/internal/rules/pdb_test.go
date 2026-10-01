package rules

import (
	"context"
	"errors"
	"strings"
	"testing"

	policyv1 "k8s.io/api/policy/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"

	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

// pdbWith builds a reconciled PDB (observedGeneration set) with the given status numbers.
func pdbWith(ns, name string, allowed, healthy, desired, expected int32) *policyv1.PodDisruptionBudget {
	min := intstr.FromInt32(desired)
	return &policyv1.PodDisruptionBudget{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: name},
		Spec:       policyv1.PodDisruptionBudgetSpec{MinAvailable: &min},
		Status: policyv1.PodDisruptionBudgetStatus{
			ObservedGeneration: 1, DisruptionsAllowed: allowed, CurrentHealthy: healthy, DesiredHealthy: desired, ExpectedPods: expected,
		},
	}
}

func TestPDBBlocksDrainWhenHealthyButNoBudget(t *testing.T) {
	fs := check(t, PDBBlocksDrain{}, newCluster(pdbWith("web", "front", 0, 1, 1, 1)))
	if len(fs) != 1 {
		t.Fatalf("want 1 finding, got %d", len(fs))
	}
	f := fs[0]
	if f.Resource.Type != "PodDisruptionBudget" || f.Resource.Namespace != "web" || f.Severity != "medium" {
		t.Fatalf("resource/severity: %+v %s", f.Resource, f.Severity)
	}
	if !hasTag(f.Tags, "pdb-blocks-drain") || !strings.Contains(f.RootCause, "sanos") || f.RiskOfFix != "medium" {
		t.Fatalf("tags=%v root=%q risk=%s", f.Tags, f.RootCause, f.RiskOfFix)
	}
	ev := evidenceKinds(f)
	if !strings.Contains(ev["pdb-status"], "disruptionsAllowed=0") || !strings.Contains(ev["pdb-spec"], "minAvailable=1") || !strings.Contains(ev["pdb-spec"], "maxUnavailable=-") {
		t.Fatalf("evidence: %v", ev)
	}
}

func TestPDBBlocksDrainWhenPodsAreUnhealthy(t *testing.T) {
	fs := check(t, PDBBlocksDrain{}, newCluster(pdbWith("web", "front", 0, 1, 2, 3)))
	if len(fs) != 1 || !hasTag(fs[0].Tags, "pdb-unhealthy-pods") || !strings.Contains(fs[0].RootCause, "menos Pods sanos") {
		t.Fatalf("unhealthy case: %+v", fs)
	}
	if strings.Contains(strings.Join(fs[0].SuggestedFix.Steps, " "), "Subir las réplicas") {
		t.Fatal("the fix for degraded pods must not tell the user to loosen the budget")
	}
}

func TestPDBDemandingMoreThanExistsIsAMisconfigurationNotADegradedWorkload(t *testing.T) {
	// minAvailable=3 on a 1-pod workload: the single pod IS healthy, so "fix the pods" would be the wrong advice.
	fs := check(t, PDBBlocksDrain{}, newCluster(pdbWith("web", "greedy", 0, 1, 3, 1)))
	if len(fs) != 1 || !hasTag(fs[0].Tags, "pdb-impossible") || hasTag(fs[0].Tags, "pdb-unhealthy-pods") {
		t.Fatalf("impossible budget: %+v", fs)
	}
	if !strings.Contains(fs[0].RootCause, "más Pods") || !strings.Contains(strings.Join(fs[0].SuggestedFix.Steps, " "), "Bajar minAvailable") {
		t.Fatalf("advice: %q %v", fs[0].RootCause, fs[0].SuggestedFix.Steps)
	}
}

func TestPDBWithNoMatchingPodsIsLowSeverity(t *testing.T) {
	fs := check(t, PDBBlocksDrain{}, newCluster(pdbWith("web", "orphan", 0, 0, 0, 0)))
	if len(fs) != 1 || fs[0].Severity != "low" || !hasTag(fs[0].Tags, "pdb-selector-matches-nothing") {
		t.Fatalf("orphan PDB: %+v", fs)
	}
}

func TestPDBIgnoresBudgetsThatAllowDisruptions(t *testing.T) {
	if fs := check(t, PDBBlocksDrain{}, newCluster(pdbWith("web", "ok", 1, 3, 2, 3))); len(fs) != 0 {
		t.Fatalf("a PDB with room must not be reported: %+v", fs)
	}
}

func TestPDBIgnoresBudgetsTheControllerHasNotProcessedYet(t *testing.T) {
	// Right after creation every status number is zero: that means "unknown", not "blocked" or "matches nothing".
	fresh := pdbWith("web", "fresh", 0, 0, 0, 0)
	fresh.Status.ObservedGeneration = 0
	if fs := check(t, PDBBlocksDrain{}, newCluster(fresh)); len(fs) != 0 {
		t.Fatalf("an unreconciled PDB must not be reported: %+v", fs)
	}
}

func TestPDBRespectsNamespaceAndShowsMaxUnavailable(t *testing.T) {
	a := pdbWith("a", "x", 0, 1, 1, 1)
	zero := intstr.FromInt32(0)
	a.Spec.MinAvailable = nil
	a.Spec.MaxUnavailable = &zero
	b := pdbWith("b", "y", 0, 1, 1, 1)
	fs := check(t, PDBBlocksDrain{Namespace: "a"}, newCluster(a, b))
	if len(fs) != 1 || fs[0].Resource.Namespace != "a" || !strings.Contains(evidenceKinds(fs[0])["pdb-spec"], "maxUnavailable=0") {
		t.Fatalf("namespace filter / spec: %+v", fs)
	}
	if all := check(t, PDBBlocksDrain{}, newCluster(a, b)); len(all) != 2 {
		t.Fatalf("all namespaces: %d", len(all))
	}
}

type failingPDBs struct{ engine.ClusterReader }

func (failingPDBs) ListPDBs(context.Context, string) ([]policyv1.PodDisruptionBudget, error) {
	return nil, errors.New("poddisruptionbudgets is forbidden")
}

func TestPDBReturnsTheListError(t *testing.T) {
	if _, err := (PDBBlocksDrain{}).Check(context.Background(), failingPDBs{newCluster()}); err == nil {
		t.Fatal("a forbidden list must surface as a rule error")
	}
}
