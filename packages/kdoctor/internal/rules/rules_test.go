package rules

import (
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
)

// ---- CrashLoopBackOff ----

func crashingPod(restarts int32, exit int32) *corev1.Pod {
	return pod("payments", "api-1", func(p *corev1.Pod) {
		p.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: "app", RestartCount: restarts,
			State:                corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
			LastTerminationState: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: exit, Reason: "Error"}},
		}}
	})
}

func TestCrashLoopBackOffDetects(t *testing.T) {
	fs := check(t, CrashLoopBackOff{}, newCluster(crashingPod(14, 1)))
	if len(fs) != 1 {
		t.Fatalf("want 1 finding, got %d", len(fs))
	}
	f := fs[0]
	ev := evidenceKinds(f)
	if f.ID != "KD-K8S-001" || f.Severity != findings.SeverityHigh || f.Resource.Namespace != "payments" {
		t.Fatalf("unexpected finding: %+v", f)
	}
	if !strings.Contains(ev["exit-code"], "exitCode=1") || ev["log-tail"] == "" {
		t.Fatalf("exit code and log tail are required evidence: %v", ev)
	}
	if !strings.Contains(f.RootCause, "código 1") {
		t.Fatalf("root cause should explain the exit code: %s", f.RootCause)
	}
}

func TestCrashLoopBackOffSeverityAndExitCodes(t *testing.T) {
	if fs := check(t, CrashLoopBackOff{}, newCluster(crashingPod(1, 127))); fs[0].Severity != findings.SeverityMedium ||
		!strings.Contains(fs[0].RootCause, "127") {
		t.Fatalf("few restarts should be medium and explain 127: %+v", fs[0])
	}
	if !strings.Contains(exitCodeMeaning(137), "SIGKILL") || !strings.Contains(exitCodeMeaning(99), "99") {
		t.Fatal("exit code meanings are off")
	}
}

func TestCrashLoopBackOffIgnoresHealthyAndInitCrash(t *testing.T) {
	if fs := check(t, CrashLoopBackOff{}, newCluster(pod("ns", "ok", nil))); len(fs) != 0 {
		t.Fatalf("healthy pod must not be reported: %v", fs)
	}
	init := pod("ns", "init", func(p *corev1.Pod) {
		p.Status.InitContainerStatuses = []corev1.ContainerStatus{{
			Name: "setup", RestartCount: 5,
			State: corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
		}}
	})
	fs := check(t, CrashLoopBackOff{}, newCluster(init))
	if len(fs) != 1 || !strings.Contains(fs[0].Title, "init container") {
		t.Fatalf("init container crash should be reported as such: %v", fs)
	}
}

func TestCrashLoopBackOffRespectsNamespace(t *testing.T) {
	c := newCluster(crashingPod(5, 1), pod("other", "x", nil))
	if fs := check(t, CrashLoopBackOff{Namespace: "other"}, c); len(fs) != 0 {
		t.Fatalf("namespace filter ignored: %v", fs)
	}
}

// ---- OOMKilled ----

func oomPod(limitMi int64, restarts int32) *corev1.Pod {
	return pod("batch", "worker", func(p *corev1.Pod) {
		if limitMi > 0 {
			memLimit(p, limitMi)
		}
		p.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: "app", RestartCount: restarts,
			LastTerminationState: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 137, Reason: "OOMKilled"}},
		}}
	})
}

func TestOOMKilledSuggestsDoubleWithoutMetrics(t *testing.T) {
	fs := check(t, OOMKilled{}, newCluster(oomPod(256, 1)))
	if len(fs) != 1 || fs[0].Severity != findings.SeverityMedium {
		t.Fatalf("unexpected: %+v", fs)
	}
	if !strings.Contains(fs[0].SuggestedFix.Summary, "256Mi a 512Mi") || !strings.Contains(fs[0].SuggestedFix.IaCHint, "512Mi") {
		t.Fatalf("should suggest 512Mi: %+v", fs[0].SuggestedFix)
	}
	if _, has := evidenceKinds(fs[0])["usage"]; has {
		t.Fatal("no metrics: usage evidence must be absent")
	}
}

func TestOOMKilledUsesObservedUsage(t *testing.T) {
	c := metricsCluster{ClusterReader: newCluster(oomPod(256, 5)), usage: map[string]int64{"app": 300 * 1024 * 1024}}
	fs := check(t, OOMKilled{}, c)
	if len(fs) != 1 || fs[0].Severity != findings.SeverityHigh {
		t.Fatalf("5 restarts should be high: %+v", fs)
	}
	if !strings.Contains(evidenceKinds(fs[0])["usage"], "300Mi") || !strings.Contains(fs[0].SuggestedFix.Summary, "450Mi") {
		t.Fatalf("expected 1.5x observed usage (450Mi): %+v %v", fs[0].SuggestedFix, evidenceKinds(fs[0]))
	}
}

func TestOOMKilledWithoutLimit(t *testing.T) {
	fs := check(t, OOMKilled{}, newCluster(oomPod(0, 1)))
	if len(fs) != 1 || !strings.Contains(fs[0].RootCause, "no define limits.memory") {
		t.Fatalf("missing limit should be called out: %+v", fs)
	}
}

func TestOOMKilledIgnoresOtherTerminations(t *testing.T) {
	p := oomPod(256, 1)
	p.Status.ContainerStatuses[0].LastTerminationState.Terminated.Reason = "Error"
	if fs := check(t, OOMKilled{}, newCluster(p)); len(fs) != 0 {
		t.Fatalf("non-OOM termination reported: %v", fs)
	}
}

func TestSuggestLimit(t *testing.T) {
	const mib = int64(1024 * 1024)
	if got := suggestLimit(256*mib, 100*mib, true); got != 512*mib { // usage far below limit: double
		t.Fatalf("got %d", got/mib)
	}
	if got := suggestLimit(256*mib, 250*mib, true); got != 375*mib { // near limit: 1.5x usage
		t.Fatalf("got %d", got/mib)
	}
}

// ---- ImagePullBackOff ----

func pullPod(reason, msg string) *corev1.Pod {
	return pod("web", "front", func(p *corev1.Pod) {
		p.Status.Phase = corev1.PodPending
		p.Status.ContainerStatuses = []corev1.ContainerStatus{{
			Name: "app", Image: "example.com/app:9",
			State: corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: reason, Message: msg}},
		}}
	})
}

func TestImagePullBackOffClassifiesCauses(t *testing.T) {
	cases := []struct {
		name, msg, wantTag, wantText string
	}{
		{"not found", `Back-off pulling image "example.com/app:9": manifest unknown`, "image-not-found", "no existe"},
		{"credentials", "pull access denied... unauthorized: authentication required", "registry-credentials", "autenticación"},
		{"rate limit", "toomanyrequests: You have reached your pull rate limit", "registry-rate-limit", "limitó"},
		{"ambiguous", "pull access denied for app, repository does not exist or may require 'docker login'", "image-not-found-or-credentials", "ambas causas"},
		{"network", "dial tcp: lookup registry.example.com: no such host", "registry-network", "contactar"},
		{"unknown", "something odd happened", "image-pull-unknown", "no permite distinguir"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			fs := check(t, ImagePullBackOff{}, newCluster(pullPod("ImagePullBackOff", c.msg)))
			if len(fs) != 1 {
				t.Fatalf("want 1 finding, got %d", len(fs))
			}
			has := false
			for _, tag := range fs[0].Tags {
				has = has || tag == c.wantTag
			}
			if !has || !strings.Contains(fs[0].RootCause, c.wantText) {
				t.Fatalf("tags=%v rootCause=%q", fs[0].Tags, fs[0].RootCause)
			}
		})
	}
}

func TestImagePullBackOffUsesEventsAndRedacts(t *testing.T) {
	ev := event("web", "front", "Failed", "Failed to pull image: pull access denied password=hunter2 unauthorized")
	fs := check(t, ImagePullBackOff{}, newCluster(pullPod("ErrImagePull", ""), ev))
	if len(fs) != 1 || !strings.Contains(fs[0].RootCause, "autenticación") {
		t.Fatalf("event message should drive classification: %+v", fs)
	}
	if strings.Contains(evidenceKinds(fs[0])["event"], "hunter2") {
		t.Fatal("event evidence must be redacted")
	}
}

func TestImagePullBackOffIgnoresHealthyPods(t *testing.T) {
	if fs := check(t, ImagePullBackOff{}, newCluster(pod("ns", "ok", nil), crashingPod(3, 1))); len(fs) != 0 {
		t.Fatalf("unexpected findings: %v", fs)
	}
}

// ---- Pending ----

func pendingPod(msg string, pvc string) *corev1.Pod {
	return pod("data", "db-0", func(p *corev1.Pod) {
		p.Status.Phase = corev1.PodPending
		p.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodScheduled, Status: corev1.ConditionFalse, Reason: "Unschedulable", Message: msg}}
		if pvc != "" {
			p.Spec.Volumes = []corev1.Volume{{Name: "data", VolumeSource: corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: pvc}}}}
		}
	})
}

func TestPendingInsufficientResourcesAndTaints(t *testing.T) {
	msg := "0/3 nodes are available: 2 Insufficient cpu, 1 node(s) had untolerated taint {dedicated: gpu}."
	fs := check(t, Pending{}, newCluster(pendingPod(msg, "")))
	if len(fs) != 1 {
		t.Fatalf("want 1 finding, got %d", len(fs))
	}
	rc := fs[0].RootCause
	if !strings.Contains(rc, "cpu suficiente") || !strings.Contains(rc, "taints") {
		t.Fatalf("should list both causes: %s", rc)
	}
}

func TestPendingSelectorAndSchedulerEvent(t *testing.T) {
	ev := event("data", "db-0", "FailedScheduling", "0/3 nodes: 3 node(s) didn't match Pod's node affinity/selector")
	fs := check(t, Pending{}, newCluster(pendingPod("0/3 nodes are available", ""), ev))
	if len(fs) != 1 || !strings.Contains(fs[0].RootCause, "nodeSelector o la afinidad") {
		t.Fatalf("event message should reveal the selector mismatch: %+v", fs)
	}
}

func TestPendingUnboundPVC(t *testing.T) {
	pvc := &corev1.PersistentVolumeClaim{}
	pvc.Namespace, pvc.Name = "data", "db-data"
	pvc.Status.Phase = corev1.ClaimPending
	fs := check(t, Pending{}, newCluster(pendingPod("pod has unbound immediate PersistentVolumeClaims", "db-data"), pvc))
	if len(fs) != 1 || !strings.Contains(fs[0].RootCause, `"db-data" no está enlazado`) {
		t.Fatalf("unbound PVC should be named: %+v", fs)
	}
	if !strings.Contains(evidenceKinds(fs[0])["pvc"], "Pending") {
		t.Fatalf("PVC phase should be evidence: %v", evidenceKinds(fs[0]))
	}
}

func TestPendingMissingPVC(t *testing.T) {
	fs := check(t, Pending{}, newCluster(pendingPod("pod has unbound immediate PersistentVolumeClaims", "ghost")))
	if len(fs) != 1 || !strings.Contains(fs[0].RootCause, `"ghost" no existe`) {
		t.Fatalf("missing PVC should be reported: %+v", fs)
	}
}

func TestPendingIgnoresScheduledAndRunning(t *testing.T) {
	scheduledPending := pod("ns", "pulling", func(p *corev1.Pod) { // Pending but already scheduled (pulling image)
		p.Status.Phase = corev1.PodPending
		p.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodScheduled, Status: corev1.ConditionTrue}}
	})
	if fs := check(t, Pending{}, newCluster(pod("ns", "ok", nil), scheduledPending)); len(fs) != 0 {
		t.Fatalf("unexpected findings: %v", fs)
	}
}

func TestDefaultRulesHaveUniqueIDs(t *testing.T) {
	seen := map[string]bool{}
	for _, r := range Default("") {
		if seen[r.ID()] || r.Description() == "" {
			t.Fatalf("bad rule %s", r.ID())
		}
		seen[r.ID()] = true
	}
	if len(seen) != 4 {
		t.Fatalf("want 4 MVP rules, got %d", len(seen))
	}
}
