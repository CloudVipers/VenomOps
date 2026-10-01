package rules

import (
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

func httpProbe(path string, port int) *corev1.Probe {
	return &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: path, Port: intstr.FromInt32(int32(port))}},
	}
}

// probePod returns a Running pod whose single container has the given state; the mutate hook sets probes.
func probePod(ready bool, restarts int32, mutate func(*corev1.Container)) *corev1.Pod {
	return pod("web", "front", func(p *corev1.Pod) {
		if mutate != nil {
			mutate(&p.Spec.Containers[0])
		}
		p.Status.ContainerStatuses = []corev1.ContainerStatus{{Name: "app", Image: "example.com/app:1", Ready: ready, RestartCount: restarts}}
	})
}

func probeEvent(msg string) *corev1.Event {
	e := event("web", "front", "Unhealthy", msg)
	e.InvolvedObject.FieldPath = "spec.containers{app}"
	return e
}

func hasTag(tags []string, want string) bool {
	for _, t := range tags {
		if t == want {
			return true
		}
	}
	return false
}

func TestProbeFailuresClassifiesCauses(t *testing.T) {
	cases := []struct{ name, msg, wantTag, wantText string }{
		{"refused", "Readiness probe failed: Get \"http://10.0.0.5:8080/\": dial tcp 10.0.0.5:8080: connect: connection refused", "probe-connection-refused", "no logra conectar"},
		{"timeout", "Readiness probe failed: Get \"http://10.0.0.5:8080/\": context deadline exceeded (Client.Timeout exceeded while awaiting headers)", "probe-timeout", "timeoutSeconds"},
		{"404", "Readiness probe failed: HTTP probe failed with statuscode: 404", "probe-http-404", "404"},
		{"503", "Readiness probe failed: HTTP probe failed with statuscode: 503", "probe-http-5xx", "5xx"},
		{"exec", "Readiness probe failed: command \"/bin/check\" exited with 1", "probe-exec", "comando"},
		{"unknown", "Readiness probe failed: something odd", "probe-unknown", "no permite distinguir"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p := probePod(false, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/healthz", 8080) })
			fs := check(t, ProbeFailures{}, newCluster(p, probeEvent(c.msg)))
			if len(fs) != 1 {
				t.Fatalf("want 1 finding, got %d: %+v", len(fs), fs)
			}
			if !hasTag(fs[0].Tags, c.wantTag) || !strings.Contains(fs[0].RootCause, c.wantText) {
				t.Fatalf("tags=%v rootCause=%q", fs[0].Tags, fs[0].RootCause)
			}
		})
	}
}

func TestProbeFailuresReportsTheProbeConfigurationWithDefaults(t *testing.T) {
	p := probePod(false, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/healthz", 8080) })
	fs := check(t, ProbeFailures{}, newCluster(p, probeEvent("Readiness probe failed: HTTP probe failed with statuscode: 500")))
	got := evidenceKinds(fs[0])["probe-config"]
	for _, want := range []string{"httpGet /healthz:8080", "periodSeconds=10", "timeoutSeconds=1", "failureThreshold=3"} {
		if !strings.Contains(got, want) {
			t.Fatalf("probe-config %q lacks %q", got, want)
		}
	}
}

func TestProbeFailuresIgnoresStaleEventsOfRecoveredContainers(t *testing.T) {
	// Events live about an hour: a readiness failure at startup must not be flagged once the container is Ready.
	p := probePod(true, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/", 80) })
	if fs := check(t, ProbeFailures{}, newCluster(p, probeEvent("Readiness probe failed: connection refused"))); len(fs) != 0 {
		t.Fatalf("a Ready container must not be reported from an old event: %+v", fs)
	}
	// Same for liveness without restarts.
	p = probePod(true, 0, func(ct *corev1.Container) { ct.LivenessProbe = httpProbe("/", 80) })
	if fs := check(t, ProbeFailures{}, newCluster(p, probeEvent("Liveness probe failed: connection refused"))); len(fs) != 0 {
		t.Fatalf("liveness without restarts must not be reported: %+v", fs)
	}
}

func TestProbeFailuresLivenessRestartsAndSeverity(t *testing.T) {
	mk := func(restarts int32) *corev1.Pod {
		return probePod(true, restarts, func(ct *corev1.Container) { ct.LivenessProbe = httpProbe("/live", 80) })
	}
	low := check(t, ProbeFailures{}, newCluster(mk(1), probeEvent("Liveness probe failed: connection refused")))
	high := check(t, ProbeFailures{}, newCluster(mk(5), probeEvent("Liveness probe failed: connection refused")))
	if len(low) != 1 || len(high) != 1 || low[0].Severity != "medium" || high[0].Severity != "high" {
		t.Fatalf("severity: low=%+v high=%+v", low, high)
	}
	if !strings.Contains(high[0].RootCause, "reinicia") {
		t.Fatalf("liveness consequence missing: %q", high[0].RootCause)
	}
}

func TestProbeFailuresStartupProbe(t *testing.T) {
	started := false
	p := probePod(false, 0, func(ct *corev1.Container) { ct.StartupProbe = httpProbe("/", 80) })
	p.Status.ContainerStatuses[0].Started = &started
	fs := check(t, ProbeFailures{}, newCluster(p, probeEvent("Startup probe failed: connection refused")))
	if len(fs) != 1 || !hasTag(fs[0].Tags, "startup-probe") {
		t.Fatalf("startup probe not reported: %+v", fs)
	}
}

func TestProbeFailuresAggregatesRepeatedEvents(t *testing.T) {
	p := probePod(false, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/", 80) })
	e := probeEvent("Readiness probe failed: connection refused")
	e.Count = 42
	fs := check(t, ProbeFailures{}, newCluster(p, e))
	if len(fs) != 1 || !strings.Contains(evidenceKinds(fs[0])["event"], "x42") {
		t.Fatalf("event count lost: %+v", fs)
	}
}

func TestProbeFailuresRedactsAndIgnoresOtherPods(t *testing.T) {
	p := probePod(false, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/", 80) })
	fs := check(t, ProbeFailures{}, newCluster(p, probeEvent("Readiness probe failed: token=abc123secret refused")))
	if len(fs) != 1 || strings.Contains(evidenceKinds(fs[0])["event"], "abc123secret") {
		t.Fatalf("event evidence must be redacted: %+v", fs)
	}
	// Pending pods and pods in other namespaces are not this rule's business.
	pending := probePod(false, 0, nil)
	pending.Status.Phase = corev1.PodPending
	if fs := check(t, ProbeFailures{}, newCluster(pending, probeEvent("Readiness probe failed: refused"))); len(fs) != 0 {
		t.Fatalf("pending pod reported: %+v", fs)
	}
	if fs := check(t, ProbeFailures{Namespace: "other"}, newCluster(p, probeEvent("Readiness probe failed: refused"))); len(fs) != 0 {
		t.Fatalf("namespace filter ignored: %+v", fs)
	}
}

func TestProbeFailuresSkipsWhenTheContainerIsAmbiguous(t *testing.T) {
	// Two containers and an event without fieldPath: do not blame one of them at random.
	p := probePod(false, 0, func(ct *corev1.Container) { ct.ReadinessProbe = httpProbe("/", 80) })
	p.Spec.Containers = append(p.Spec.Containers, corev1.Container{Name: "sidecar", Image: "example.com/side:1"})
	ev := event("web", "front", "Unhealthy", "Readiness probe failed: refused")
	if fs := check(t, ProbeFailures{}, newCluster(p, ev)); len(fs) != 0 {
		t.Fatalf("ambiguous container must produce no finding: %+v", fs)
	}
}
