package cluster_test

import (
	"context"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/cluster"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

var _ engine.ClusterReader = (*cluster.Reader)(nil)

func TestReaderListsAndFiltersEvents(t *testing.T) {
	cs := fake.NewSimpleClientset(
		&corev1.Pod{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "p1"}},
		&corev1.Pod{ObjectMeta: metav1.ObjectMeta{Namespace: "b", Name: "p2"}},
		&corev1.Event{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "e1"}, InvolvedObject: corev1.ObjectReference{Kind: "Pod", Name: "p1"}},
		&corev1.Event{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "e2"}, InvolvedObject: corev1.ObjectReference{Kind: "Pod", Name: "other"}},
		&corev1.PersistentVolumeClaim{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "data"}},
	)
	r := cluster.New(cs)
	ctx := context.Background()

	if pods, err := r.ListPods(ctx, ""); err != nil || len(pods) != 2 {
		t.Fatalf("all namespaces: %v %v", pods, err)
	}
	if pods, err := r.ListPods(ctx, "a"); err != nil || len(pods) != 1 {
		t.Fatalf("namespace a: %v %v", pods, err)
	}
	if ev, err := r.PodEvents(ctx, "a", "p1"); err != nil || len(ev) != 1 || ev[0].Name != "e1" {
		t.Fatalf("events should be filtered to the pod: %v %v", ev, err)
	}
	if _, err := r.GetPVC(ctx, "a", "data"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.GetPVC(ctx, "a", "missing"); err == nil || !strings.Contains(err.Error(), "missing") {
		t.Fatalf("missing PVC should error with its name: %v", err)
	}
	if logs, err := r.PodLogs(ctx, "a", "p1", "app", true, 10); err != nil || logs == "" {
		t.Fatalf("logs: %q %v", logs, err)
	}
	// The fake clientset has no REST client: metrics are "not available", never an error.
	if usage, ok, err := r.PodMemoryUsage(ctx, "a", "p1"); err != nil || ok || usage != nil {
		t.Fatalf("metrics should be unavailable: %v %v %v", usage, ok, err)
	}
}

// The reader must never write to the cluster: only get/list (log reads are a get on pods/log).
func TestReaderIsReadOnly(t *testing.T) {
	cs := fake.NewSimpleClientset(
		&corev1.Pod{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "p1"}},
		&corev1.PersistentVolumeClaim{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "data"}},
		&corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: "n1"}},
	)
	r := cluster.New(cs)
	ctx := context.Background()
	if nodes, err := r.ListNodes(ctx); err != nil || len(nodes) != 1 {
		t.Fatalf("nodes: %v %v", nodes, err)
	}
	_, _ = r.ListPods(ctx, "a")
	_, _ = r.PodLogs(ctx, "a", "p1", "app", false, 5)
	_, _ = r.PodEvents(ctx, "a", "p1")
	_, _ = r.GetPVC(ctx, "a", "data")
	_, _, _ = r.PodMemoryUsage(ctx, "a", "p1")

	if len(cs.Actions()) == 0 {
		t.Fatal("expected some recorded actions")
	}
	for _, a := range cs.Actions() {
		if v := a.GetVerb(); v != "get" && v != "list" {
			t.Fatalf("forbidden verb %q on %s", v, a.GetResource().Resource)
		}
	}
}
