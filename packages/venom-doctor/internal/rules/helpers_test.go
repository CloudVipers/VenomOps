package rules

import (
	"context"
	"encoding/json"
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/kubernetes/fake"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/cluster"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
)

func newCluster(objs ...runtime.Object) engine.ClusterReader {
	return cluster.New(fake.NewSimpleClientset(objs...))
}

// metricsCluster overrides memory metrics (the fake clientset has no REST client).
type metricsCluster struct {
	engine.ClusterReader
	usage map[string]int64
}

func (m metricsCluster) PodMemoryUsage(context.Context, string, string) (map[string]int64, bool, error) {
	return m.usage, true, nil
}

func pod(ns, name string, mutate func(*corev1.Pod)) *corev1.Pod {
	p := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: name},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "app", Image: "example.com/app:1"}}},
		Status:     corev1.PodStatus{Phase: corev1.PodRunning},
	}
	if mutate != nil {
		mutate(p)
	}
	return p
}

func memLimit(p *corev1.Pod, mib int64) {
	p.Spec.Containers[0].Resources.Limits = corev1.ResourceList{
		corev1.ResourceMemory: *resource.NewQuantity(mib*1024*1024, resource.BinarySI),
	}
}

func event(ns, podName, reason, msg string) *corev1.Event {
	return &corev1.Event{
		ObjectMeta:     metav1.ObjectMeta{Namespace: ns, Name: podName + "-" + reason},
		InvolvedObject: corev1.ObjectReference{Kind: "Pod", Name: podName, Namespace: ns},
		Reason:         reason,
		Message:        msg,
	}
}

// assertValid checks that a finding serialises to a document that satisfies the shared schema.
func assertValid(t *testing.T, f findings.Finding) {
	t.Helper()
	data, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	issues, err := findings.Validate(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(issues) != 0 {
		t.Fatalf("finding %s is not schema-valid: %v\n%s", f.ID, issues, data)
	}
}

func evidenceKinds(f findings.Finding) map[string]string {
	m := map[string]string{}
	for _, e := range f.Evidence {
		m[e.Kind] = e.Detail
	}
	return m
}

func check(t *testing.T, r engine.Rule, c engine.ClusterReader) []findings.Finding {
	t.Helper()
	fs, err := r.Check(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range fs {
		assertValid(t, f)
	}
	return fs
}
