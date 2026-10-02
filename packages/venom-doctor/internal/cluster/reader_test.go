package cluster_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	dynamicfake "k8s.io/client-go/dynamic/fake"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"

	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/cluster"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
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
		&policyv1.PodDisruptionBudget{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "pdb"}},
		&corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Namespace: "a", Name: "sa"}},
	)
	r := cluster.New(cs)
	ctx := context.Background()
	if nodes, err := r.ListNodes(ctx); err != nil || len(nodes) != 1 {
		t.Fatalf("nodes: %v %v", nodes, err)
	}
	if sas, err := r.ListServiceAccounts(ctx, "a"); err != nil || len(sas) != 1 {
		t.Fatalf("serviceaccounts: %v %v", sas, err)
	}
	if pdbs, err := r.ListPDBs(ctx, "a"); err != nil || len(pdbs) != 1 {
		t.Fatalf("pdbs: %v %v", pdbs, err)
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

func TestReaderCustomResources(t *testing.T) {
	gvr := schema.GroupVersionResource{Group: "karpenter.sh", Version: "v1", Resource: "nodepools"}
	pool := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "karpenter.sh/v1", "kind": "NodePool", "metadata": map[string]any{"name": "default"},
	}}
	scheme := runtime.NewScheme()
	dyn := dynamicfake.NewSimpleDynamicClientWithCustomListKinds(scheme, map[schema.GroupVersionResource]string{gvr: "NodePoolList"}, pool)
	r := cluster.New(fake.NewSimpleClientset()).WithDynamic(dyn)
	ctx := context.Background()

	items, installed, err := r.ListCustomResources(ctx, gvr)
	if err != nil || !installed || len(items) != 1 {
		t.Fatalf("installed CRD: %v %v %v", items, installed, err)
	}
	for _, a := range dyn.Actions() {
		if v := a.GetVerb(); v != "get" && v != "list" {
			t.Fatalf("forbidden verb %q reading custom resources", v)
		}
	}

	// A CRD the API server does not know (404) means "not installed", not an error.
	missing := dynamicfake.NewSimpleDynamicClient(scheme)
	missing.PrependReactor("list", "*", func(k8stesting.Action) (bool, runtime.Object, error) {
		return true, nil, apierrors.NewNotFound(gvr.GroupResource(), "")
	})
	if _, installed, err := cluster.New(fake.NewSimpleClientset()).WithDynamic(missing).ListCustomResources(ctx, gvr); err != nil || installed {
		t.Fatalf("missing CRD: installed=%v err=%v", installed, err)
	}

	// Any other failure (for example RBAC) must not be hidden as "not installed".
	forbidden := dynamicfake.NewSimpleDynamicClient(scheme)
	forbidden.PrependReactor("list", "*", func(k8stesting.Action) (bool, runtime.Object, error) {
		return true, nil, apierrors.NewForbidden(gvr.GroupResource(), "", errors.New("no access"))
	})
	if _, _, err := cluster.New(fake.NewSimpleClientset()).WithDynamic(forbidden).ListCustomResources(ctx, gvr); err == nil {
		t.Fatal("a forbidden list must be an error")
	}

	// Without a dynamic client the reader cannot know: treated as not installed.
	if _, installed, err := cluster.New(fake.NewSimpleClientset()).ListCustomResources(ctx, gvr); err != nil || installed {
		t.Fatalf("no dynamic client: installed=%v err=%v", installed, err)
	}
}
