package rules

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"

	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
)

// crdCluster serves custom resources from memory; a nil map entry means "CRD not installed".
type crdCluster struct {
	engine.ClusterReader
	items map[schema.GroupVersionResource][]unstructured.Unstructured
	err   error
}

func (c crdCluster) ListCustomResources(_ context.Context, gvr schema.GroupVersionResource) ([]unstructured.Unstructured, bool, error) {
	if c.err != nil {
		return nil, false, c.err
	}
	items, ok := c.items[gvr]
	return items, ok, nil
}

func conds(cs ...[3]string) []any {
	var out []any
	for _, c := range cs {
		out = append(out, map[string]any{"type": c[0], "status": c[1], "reason": c[2], "message": ""})
	}
	return out
}

func condsMsg(typ, status, reason, msg string) []any {
	return []any{map[string]any{"type": typ, "status": status, "reason": reason, "message": msg}}
}

func nodePool(name string, spec, status map[string]any) unstructured.Unstructured {
	return unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "karpenter.sh/v1", "kind": "NodePool",
		"metadata": map[string]any{"name": name}, "spec": spec, "status": status,
	}}
}

func nodeClaim(name string, age time.Duration, conditions []any, extra map[string]any) unstructured.Unstructured {
	u := unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "karpenter.sh/v1", "kind": "NodeClaim",
		"metadata": map[string]any{"name": name, "labels": map[string]any{"karpenter.sh/nodepool": "default"}},
		"status":   map[string]any{"conditions": conditions},
	}}
	for k, v := range extra {
		_ = unstructured.SetNestedField(u.Object, v, "status", k)
	}
	u.SetCreationTimestamp(metav1.NewTime(now().Add(-age)))
	return u
}

func karpenter(pools, claims []unstructured.Unstructured) engine.ClusterReader {
	return crdCluster{ClusterReader: newCluster(), items: map[schema.GroupVersionResource][]unstructured.Unstructured{
		nodePoolGVR: pools, nodeClaimGVR: claims,
	}}
}

func TestKarpenterSilentWhenNotInstalled(t *testing.T) {
	c := crdCluster{ClusterReader: newCluster()} // no CRDs at all
	if fs := check(t, KarpenterCapacity{}, c); len(fs) != 0 {
		t.Fatalf("a cluster without Karpenter must produce nothing: %+v", fs)
	}
}

func TestKarpenterNodePoolNotReadyShowsTheReportedCondition(t *testing.T) {
	pool := nodePool("default", nil, map[string]any{"conditions": []any{
		map[string]any{"type": "Ready", "status": "False", "reason": "NodeClassNotReady", "message": "NodeClass not ready"},
		map[string]any{"type": "NodeClassReady", "status": "False", "reason": "SubnetsNotFound", "message": "no subnets token=abc123secret"},
		map[string]any{"type": "ValidationSucceeded", "status": "True", "reason": "ValidationSucceeded"},
	}})
	fs := check(t, KarpenterCapacity{}, karpenter([]unstructured.Unstructured{pool}, nil))
	if len(fs) != 1 || fs[0].Resource.Type != "NodePool" || fs[0].Severity != "high" || !hasTag(fs[0].Tags, "nodepool-not-ready") {
		t.Fatalf("not ready: %+v", fs)
	}
	var details []string
	for _, e := range fs[0].Evidence {
		details = append(details, e.Detail)
	}
	all := strings.Join(details, "|")
	if !strings.Contains(all, "reason=NodeClassNotReady") || !strings.Contains(all, "reason=SubnetsNotFound") {
		t.Fatalf("reported reasons must be shown verbatim: %v", details)
	}
	if strings.Contains(all, "ValidationSucceeded=") || strings.Contains(all, "abc123secret") {
		t.Fatalf("healthy sub-conditions must be omitted and messages redacted: %v", details)
	}
}

func TestKarpenterNodePoolWithUnknownReadyIsNotReported(t *testing.T) {
	pool := nodePool("new", nil, map[string]any{"conditions": conds([3]string{"Ready", "Unknown", "AwaitingReconciliation"})})
	if fs := check(t, KarpenterCapacity{}, karpenter([]unstructured.Unstructured{pool}, nil)); len(fs) != 0 {
		t.Fatalf("Unknown means not evaluated yet: %+v", fs)
	}
}

func TestKarpenterNodePoolLimits(t *testing.T) {
	cases := []struct {
		name         string
		limits, used map[string]any
		want         bool
	}{
		{"cpu reached", map[string]any{"cpu": "100"}, map[string]any{"cpu": "100"}, true},
		{"memory exceeded (eventually consistent overrun)", map[string]any{"memory": "100Gi"}, map[string]any{"memory": "104Gi"}, true},
		{"units are compared as quantities", map[string]any{"cpu": "10"}, map[string]any{"cpu": "10000m"}, true},
		{"below the limit", map[string]any{"cpu": "100"}, map[string]any{"cpu": "40"}, false},
		{"resource without usage data", map[string]any{"nvidia.com/gpu": "4"}, map[string]any{"cpu": "40"}, false},
		{"no limits means unlimited", nil, map[string]any{"cpu": "999"}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			spec := map[string]any{}
			if c.limits != nil {
				spec["limits"] = c.limits
			}
			pool := nodePool("default", spec, map[string]any{"resources": c.used})
			fs := check(t, KarpenterCapacity{}, karpenter([]unstructured.Unstructured{pool}, nil))
			if got := len(fs) == 1 && hasTag(fs[0].Tags, "nodepool-limits"); got != c.want || (!c.want && len(fs) != 0) {
				t.Fatalf("want reported=%v, got %+v", c.want, fs)
			}
		})
	}
}

func TestKarpenterNodeClaimStuckStages(t *testing.T) {
	cases := []struct {
		name    string
		age     time.Duration
		conds   []any
		want    string
		wantTag string
	}{
		{"launch failing", 5 * time.Minute, condsMsg("Launched", "False", "LaunchFailed", "insufficient capacity"), "Launched", "nodeclaim-launch-stuck"},
		{"never registered", 12 * time.Minute, conds([3]string{"Launched", "True", "Launched"}, [3]string{"Registered", "False", "NodeNotFound"}), "Registered", "nodeclaim-registration-stuck"},
		{"registered but not initialized", 20 * time.Minute, conds([3]string{"Launched", "True", ""}, [3]string{"Registered", "True", ""}, [3]string{"Initialized", "False", "NodeNotReady"}), "Initialized", "nodeclaim-initialization-stuck"},
		{"no conditions yet but old", 15 * time.Minute, nil, "Launched", "nodeclaim-launch-stuck"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			claim := nodeClaim("default-abc", c.age, c.conds, map[string]any{"nodeName": "ip-10-0-1-5"})
			fs := check(t, KarpenterCapacity{}, karpenter(nil, []unstructured.Unstructured{claim}))
			if len(fs) != 1 || fs[0].Resource.Type != "NodeClaim" || !hasTag(fs[0].Tags, c.wantTag) || !strings.Contains(fs[0].Title, c.want) {
				t.Fatalf("stuck claim: %+v", fs)
			}
			if kinds := evidenceKinds(fs[0]); kinds["nodepool"] != "default" || kinds["node"] != "ip-10-0-1-5" {
				t.Fatalf("evidence: %v", kinds)
			}
		})
	}
}

func TestKarpenterNodeClaimRespectsNormalStartupTimes(t *testing.T) {
	young := nodeClaim("young", 90*time.Second, condsMsg("Launched", "False", "", ""), nil)
	registering := nodeClaim("registering", 4*time.Minute, conds([3]string{"Launched", "True", ""}, [3]string{"Registered", "False", ""}), nil)
	ready := nodeClaim("ready", time.Hour, conds([3]string{"Launched", "True", ""}, [3]string{"Registered", "True", ""}, [3]string{"Initialized", "True", ""}, [3]string{"Ready", "True", ""}), nil)
	deleting := nodeClaim("deleting", time.Hour, condsMsg("Launched", "False", "", ""), nil)
	deleting.SetDeletionTimestamp(&metav1.Time{Time: now()})
	if fs := check(t, KarpenterCapacity{}, karpenter(nil, []unstructured.Unstructured{young, registering, ready, deleting})); len(fs) != 0 {
		t.Fatalf("normal startup, ready and deleting claims must not be reported: %+v", fs)
	}
}

func TestKarpenterReturnsListErrors(t *testing.T) {
	c := crdCluster{ClusterReader: newCluster(), err: errors.New("nodepools.karpenter.sh is forbidden")}
	if _, err := (KarpenterCapacity{}).Check(context.Background(), c); err == nil {
		t.Fatal("RBAC errors must surface as rule errors, not be mistaken for 'not installed'")
	}
}

func TestKarpenterRuleOnlyRunsClusterWide(t *testing.T) {
	has := func(rs []engine.Rule) bool {
		for _, r := range rs {
			if r.ID() == "VD-K8S-008" {
				return true
			}
		}
		return false
	}
	if !has(Default("")) || has(Default("payments")) {
		t.Fatal("the Karpenter rule must run for all namespaces only")
	}
}
