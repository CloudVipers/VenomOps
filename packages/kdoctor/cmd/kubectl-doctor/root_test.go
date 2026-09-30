package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/cluster"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/explain"
)

func crashingPod(ns, name string) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: name},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "app"}}},
		Status: corev1.PodStatus{ContainerStatuses: []corev1.ContainerStatus{{
			Name: "app", RestartCount: 9,
			State:                corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
			LastTerminationState: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 1}},
		}}},
	}
}

func TestRunJSONOutputValidatesAgainstTheSchema(t *testing.T) {
	reader := cluster.New(fake.NewSimpleClientset(crashingPod("payments", "api"), crashingPod("other", "x")))
	var out, errOut bytes.Buffer
	if err := run(context.Background(), options{output: "json", namespace: "payments"}, reader, nil, &out, &errOut); err != nil {
		t.Fatal(err)
	}
	var arr []json.RawMessage
	if err := json.Unmarshal(out.Bytes(), &arr); err != nil || len(arr) != 1 {
		t.Fatalf("stdout must be a pure JSON array with the payments finding only: %v\n%s", err, out.String())
	}
	if issues, err := findings.Validate(arr[0]); err != nil || len(issues) != 0 {
		t.Fatalf("finding does not satisfy findings-schema: %v %v", issues, err)
	}
}

func TestRunTableAndAllNamespaces(t *testing.T) {
	reader := cluster.New(fake.NewSimpleClientset(crashingPod("a", "p1"), crashingPod("b", "p2")))
	var out, errOut bytes.Buffer
	if err := run(context.Background(), options{output: "table", namespace: ""}, reader, nil, &out, &errOut); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"KD-K8S-001", "a/p1", "b/p2", "Causa probable:"} {
		if !strings.Contains(out.String(), want) {
			t.Fatalf("missing %q in:\n%s", want, out.String())
		}
	}
}

func TestRunHealthyClusterSaysSo(t *testing.T) {
	var out, errOut bytes.Buffer
	if err := run(context.Background(), options{output: "table"}, cluster.New(fake.NewSimpleClientset()), nil, &out, &errOut); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "No se encontraron problemas") {
		t.Fatalf("got %q", out.String())
	}
}

func TestRunRejectsUnknownFormat(t *testing.T) {
	err := run(context.Background(), options{output: "yaml"}, cluster.New(fake.NewSimpleClientset()), nil, &bytes.Buffer{}, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "unsupported output format") {
		t.Fatalf("got %v", err)
	}
}

type stubExplainer struct {
	got string
	err error
}

func (s *stubExplainer) Explain(_ context.Context, payload string) (string, error) {
	s.got = payload
	return "explicación de prueba", s.err
}

var _ explain.Explainer = (*stubExplainer)(nil)

func TestRunExplainIsOptInAndAttachedAsEvidence(t *testing.T) {
	reader := cluster.New(fake.NewSimpleClientset(crashingPod("a", "p1")))

	// Without an explainer nothing AI-related appears.
	var plain, errOut bytes.Buffer
	if err := run(context.Background(), options{output: "json"}, reader, nil, &plain, &errOut); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(plain.String(), explain.EvidenceKind) {
		t.Fatal("no AI evidence expected without --explain")
	}

	st := &stubExplainer{}
	var out bytes.Buffer
	if err := run(context.Background(), options{output: "json"}, reader, st, &out, &errOut); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), explain.EvidenceKind) || !strings.Contains(out.String(), "explicación de prueba") {
		t.Fatalf("explanation should appear as evidence:\n%s", out.String())
	}
	if !strings.Contains(st.got, "CrashLoopBackOff") {
		t.Fatalf("explainer should receive the finding payload: %s", st.got)
	}
}

func TestRunExplainFailureOnlyWarns(t *testing.T) {
	reader := cluster.New(fake.NewSimpleClientset(crashingPod("a", "p1")))
	var out, errOut bytes.Buffer
	st := &stubExplainer{err: errors.New("throttled")}
	if err := run(context.Background(), options{output: "table"}, reader, st, &out, &errOut); err != nil {
		t.Fatalf("a failing explainer must not fail the run: %v", err)
	}
	if !strings.Contains(errOut.String(), "throttled") || !strings.Contains(out.String(), "KD-K8S-001") {
		t.Fatalf("stderr=%q stdout=%q", errOut.String(), out.String())
	}
}

func TestBuildExplainerWithoutModelWarnsAndReturnsNil(t *testing.T) {
	t.Setenv(modelEnv, "")
	var errOut bytes.Buffer
	if e := buildExplainer(context.Background(), options{explain: true}, &errOut); e != nil {
		t.Fatal("expected nil explainer")
	}
	if !strings.Contains(errOut.String(), "no se pudo activar --explain") {
		t.Fatalf("expected a warning, got %q", errOut.String())
	}
}

func TestRootCommandDefinesTheDocumentedFlags(t *testing.T) {
	cmd := newRootCmd(&bytes.Buffer{}, &bytes.Buffer{})
	for _, f := range []string{"namespace", "all-namespaces", "output", "explain", "explain-model", "kubeconfig", "context"} {
		if cmd.Flags().Lookup(f) == nil {
			t.Fatalf("missing flag --%s", f)
		}
	}
	if cmd.Flags().Lookup("explain").DefValue != "false" {
		t.Fatal("--explain must be off by default")
	}
}
