package rules

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
)

func node(name string, conds ...corev1.NodeCondition) *corev1.Node {
	return &corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: name}, Status: corev1.NodeStatus{Conditions: conds}}
}

func cond(t corev1.NodeConditionType, s corev1.ConditionStatus, reason, msg string) corev1.NodeCondition {
	return corev1.NodeCondition{Type: t, Status: s, Reason: reason, Message: msg,
		LastTransitionTime: metav1.NewTime(now().Add(-47 * time.Minute))}
}

func readyNode(name string) *corev1.Node {
	return node(name, cond(corev1.NodeReady, corev1.ConditionTrue, "KubeletReady", "kubelet is posting ready status"))
}

func TestNodeNotReadyClassifiesCauses(t *testing.T) {
	cases := []struct {
		name         string
		status       corev1.ConditionStatus
		reason, msg  string
		wantTag, txt string
	}{
		{"kubelet stopped", corev1.ConditionUnknown, "NodeStatusUnknown", "Kubelet stopped posting node status.", "kubelet-stopped", "dejó de reportar"},
		{"cni", corev1.ConditionFalse, "KubeletNotReady", "container runtime network not ready: NetworkReady=false reason:NetworkPluginNotReady message:cni plugin not initialized", "cni-not-ready", "CNI"},
		{"pleg", corev1.ConditionFalse, "KubeletNotReady", "PLEG is not healthy: pleg was last seen active 5m ago", "pleg-unhealthy", "PLEG"},
		{"runtime", corev1.ConditionFalse, "KubeletNotReady", "container runtime is down", "container-runtime-down", "runtime de contenedores"},
		{"unknown", corev1.ConditionFalse, "KubeletNotReady", "something odd", "node-not-ready", "no permite distinguir"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			n := node("ip-10-0-1-5.ec2.internal", cond(corev1.NodeReady, c.status, c.reason, c.msg))
			fs := check(t, NodeNotReady{}, newCluster(n))
			if len(fs) != 1 {
				t.Fatalf("want 1 finding, got %d", len(fs))
			}
			f := fs[0]
			if f.Resource.Type != "Node" || f.Resource.Namespace != "" || f.Severity != "high" {
				t.Fatalf("resource/severity: %+v %s", f.Resource, f.Severity)
			}
			if !hasTag(f.Tags, c.wantTag) || !strings.Contains(f.RootCause, c.txt) {
				t.Fatalf("tags=%v rootCause=%q", f.Tags, f.RootCause)
			}
			if !strings.Contains(evidenceKinds(f)["node-condition"], "47m") {
				t.Fatalf("duration missing: %q", evidenceKinds(f)["node-condition"])
			}
		})
	}
}

func TestNodeNotReadyCountsAffectedPodsAndNotesCordon(t *testing.T) {
	n := node("n1", cond(corev1.NodeReady, corev1.ConditionUnknown, "NodeStatusUnknown", "Kubelet stopped posting node status."))
	n.Spec.Unschedulable = true
	onNode := func(name string) *corev1.Pod { return pod("a", name, func(p *corev1.Pod) { p.Spec.NodeName = "n1" }) }
	elsewhere := pod("a", "other", func(p *corev1.Pod) { p.Spec.NodeName = "n2" })
	fs := check(t, NodeNotReady{}, newCluster(n, onNode("p1"), onNode("p2"), elsewhere))
	ev := evidenceKinds(fs[0])
	if !strings.Contains(ev["pods-on-node"], "2 Pods") || !strings.Contains(ev["node-spec"], "acordonado") {
		t.Fatalf("evidence: %v", ev)
	}
}

func TestNodeNotReadyReportsPressureOnReadyNodesAndRedacts(t *testing.T) {
	n := node("n1",
		cond(corev1.NodeReady, corev1.ConditionTrue, "KubeletReady", "ok"),
		cond(corev1.NodeDiskPressure, corev1.ConditionTrue, "KubeletHasDiskPressure", "kubelet has disk pressure token=abc123secret"),
		cond(corev1.NodeMemoryPressure, corev1.ConditionFalse, "KubeletHasSufficientMemory", "ok"),
	)
	fs := check(t, NodeNotReady{}, newCluster(n))
	if len(fs) != 1 || fs[0].Severity != "medium" || !hasTag(fs[0].Tags, "disk-pressure") {
		t.Fatalf("want one medium disk-pressure finding: %+v", fs)
	}
	if strings.Contains(evidenceKinds(fs[0])["kubelet-message"], "abc123secret") {
		t.Fatal("kubelet message must be redacted")
	}
}

func TestNodeNotReadyPressureTagsAreStableAndIncludedWhenNotReady(t *testing.T) {
	n := node("n1",
		cond(corev1.NodeReady, corev1.ConditionFalse, "KubeletNotReady", "x"),
		cond(corev1.NodePIDPressure, corev1.ConditionTrue, "p", "p"),
		cond(corev1.NodeMemoryPressure, corev1.ConditionTrue, "m", "m"),
	)
	var first []string
	for i := 0; i < 20; i++ {
		fs := check(t, NodeNotReady{}, newCluster(n))
		if i == 0 {
			first = fs[0].Tags
			if !hasTag(first, "memory-pressure") || !hasTag(first, "pid-pressure") {
				t.Fatalf("tags=%v", first)
			}
			continue
		}
		if strings.Join(fs[0].Tags, ",") != strings.Join(first, ",") {
			t.Fatalf("unstable tag order: %v vs %v", fs[0].Tags, first)
		}
	}
}

func TestNodeNotReadyIgnoresHealthyNodes(t *testing.T) {
	healthy := node("ok",
		cond(corev1.NodeReady, corev1.ConditionTrue, "KubeletReady", "ok"),
		cond(corev1.NodeMemoryPressure, corev1.ConditionFalse, "x", "x"),
		cond(corev1.NodeNetworkUnavailable, corev1.ConditionTrue, "x", "x"), // not an eviction signal while Ready
	)
	if fs := check(t, NodeNotReady{}, newCluster(healthy, readyNode("ok2"))); len(fs) != 0 {
		t.Fatalf("unexpected findings: %+v", fs)
	}
}

type failingNodes struct{ engine.ClusterReader }

func (failingNodes) ListNodes(context.Context) ([]corev1.Node, error) {
	return nil, errors.New("nodes is forbidden")
}

func TestNodeNotReadyReturnsTheListError(t *testing.T) {
	if _, err := (NodeNotReady{}).Check(context.Background(), failingNodes{newCluster()}); err == nil {
		t.Fatal("a forbidden node list must surface as a rule error, not be swallowed")
	}
}

func TestNodeRuleOnlyRunsClusterWide(t *testing.T) {
	has := func(rs []engine.Rule) bool {
		for _, r := range rs {
			if r.ID() == "KD-K8S-006" {
				return true
			}
		}
		return false
	}
	if !has(Default("")) || has(Default("payments")) {
		t.Fatal("node rule must run for all namespaces only")
	}
}
