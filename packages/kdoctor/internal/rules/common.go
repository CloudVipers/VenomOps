// Package rules contains the kdoctor diagnostic rules, one per file.
package rules

import (
	"fmt"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/redact"
)

const schemaVersion = "1.0.0"

// now is replaceable in tests.
var now = func() time.Time { return time.Now().UTC() }

// Default returns the MVP rule set scoped to a namespace ("" = all namespaces).
func Default(namespace string) []engine.Rule {
	return []engine.Rule{
		CrashLoopBackOff{Namespace: namespace},
		OOMKilled{Namespace: namespace},
		ImagePullBackOff{Namespace: namespace},
		Pending{Namespace: namespace},
	}
}

// containerStatusEntry pairs a status with its kind so init containers are reported correctly.
type containerStatusEntry struct {
	corev1.ContainerStatus
	Init bool
}

func containerStatuses(pod *corev1.Pod) []containerStatusEntry {
	var out []containerStatusEntry
	for _, cs := range pod.Status.InitContainerStatuses {
		out = append(out, containerStatusEntry{cs, true})
	}
	for _, cs := range pod.Status.ContainerStatuses {
		out = append(out, containerStatusEntry{cs, false})
	}
	return out
}

func containerSpec(pod *corev1.Pod, name string) *corev1.Container {
	for i := range pod.Spec.Containers {
		if pod.Spec.Containers[i].Name == name {
			return &pod.Spec.Containers[i]
		}
	}
	for i := range pod.Spec.InitContainers {
		if pod.Spec.InitContainers[i].Name == name {
			return &pod.Spec.InitContainers[i]
		}
	}
	return nil
}

func kindLabel(init bool) string {
	if init {
		return "init container"
	}
	return "container"
}

// logTail keeps the last n non-empty lines, redacted and truncated so findings stay small and safe.
func logTail(logs string, n int) string {
	var lines []string
	for _, l := range strings.Split(strings.TrimSpace(logs), "\n") {
		if l = strings.TrimSpace(l); l != "" {
			lines = append(lines, l)
		}
	}
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	for i, l := range lines {
		l = redact.String(l)
		if len(l) > 200 {
			l = l[:200] + "…"
		}
		lines[i] = l
	}
	return strings.Join(lines, "\n")
}

func newFinding(id string, sev findings.Severity, title string, pod *corev1.Pod, ev []findings.Evidence,
	rootCause string, fix findings.SuggestedFix, risk findings.Risk, refs, tags []string) findings.Finding {
	return findings.Finding{
		ID:            id,
		SchemaVersion: schemaVersion,
		Source:        findings.SourceKDoctor,
		Severity:      sev,
		Title:         title,
		Resource:      findings.Resource{Type: "Pod", Name: pod.Name, Namespace: pod.Namespace},
		Evidence:      ev,
		RootCause:     rootCause,
		SuggestedFix:  fix,
		RiskOfFix:     risk,
		References:    refs,
		Tags:          uniq(tags),
		DetectedAt:    now(),
	}
}

func uniq(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func podLabel(pod *corev1.Pod) string { return fmt.Sprintf("%s/%s", pod.Namespace, pod.Name) }
