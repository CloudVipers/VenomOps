// Package engine runs venom-doctor rules against a read-only view of a cluster and aggregates findings.
package engine

import (
	"context"
	"fmt"
	"sort"
	"sync"

	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
)

// ClusterReader is a READ-ONLY view of a cluster. It deliberately exposes no write operations so
// that rules cannot modify anything, and so they can be tested against client-go's fake clientset.
type ClusterReader interface {
	// ListPods lists pods in a namespace; an empty namespace means all namespaces.
	ListPods(ctx context.Context, namespace string) ([]corev1.Pod, error)
	// PodLogs returns the last tailLines lines of a container's log (previous = last terminated instance).
	PodLogs(ctx context.Context, namespace, pod, container string, previous bool, tailLines int64) (string, error)
	// PodEvents returns the events whose involved object is the given pod.
	PodEvents(ctx context.Context, namespace, pod string) ([]corev1.Event, error)
	// GetPVC returns a PersistentVolumeClaim.
	GetPVC(ctx context.Context, namespace, name string) (*corev1.PersistentVolumeClaim, error)
	// ListServiceAccounts lists ServiceAccounts in a namespace; an empty namespace means all namespaces.
	ListServiceAccounts(ctx context.Context, namespace string) ([]corev1.ServiceAccount, error)
	// ListPDBs lists PodDisruptionBudgets in a namespace; an empty namespace means all namespaces.
	ListPDBs(ctx context.Context, namespace string) ([]policyv1.PodDisruptionBudget, error)
	// ListCustomResources lists a cluster-scoped custom resource. installed is false when the CRD does not exist
	// (or no dynamic client is configured), which is not an error: the product simply is not in this cluster.
	ListCustomResources(ctx context.Context, gvr schema.GroupVersionResource) (items []unstructured.Unstructured, installed bool, err error)
	// ListNodes lists the cluster's nodes (cluster-scoped).
	ListNodes(ctx context.Context) ([]corev1.Node, error)
	// PodMemoryUsage returns the current memory usage in bytes per container from the metrics API.
	// ok is false when metrics-server is not available (not an error).
	PodMemoryUsage(ctx context.Context, namespace, pod string) (usage map[string]int64, ok bool, err error)
}

// Rule is a single diagnostic check.
type Rule interface {
	ID() string
	Description() string
	Check(ctx context.Context, cluster ClusterReader) ([]findings.Finding, error)
}

// RuleError records a rule that failed; other rules keep running.
type RuleError struct {
	RuleID string
	Err    error
}

func (e RuleError) Error() string { return fmt.Sprintf("rule %s: %v", e.RuleID, e.Err) }

// Report is the aggregated result of a run.
type Report struct {
	Findings []findings.Finding
	Errors   []RuleError
}

// Engine holds a registry of rules.
type Engine struct {
	rules []Rule
}

// New creates an engine with the given rules.
func New(rules ...Rule) (*Engine, error) {
	e := &Engine{}
	for _, r := range rules {
		if err := e.Register(r); err != nil {
			return nil, err
		}
	}
	return e, nil
}

// Register adds a rule; rule IDs must be unique.
func (e *Engine) Register(r Rule) error {
	for _, existing := range e.rules {
		if existing.ID() == r.ID() {
			return fmt.Errorf("rule %q is already registered", r.ID())
		}
	}
	e.rules = append(e.rules, r)
	return nil
}

// Rules returns the registered rules in registration order.
func (e *Engine) Rules() []Rule { return append([]Rule(nil), e.rules...) }

// Run executes every rule concurrently. A failing rule is reported in Report.Errors and never
// prevents the others from running. Findings are ordered by severity (most severe first).
func (e *Engine) Run(ctx context.Context, cluster ClusterReader) Report {
	type result struct {
		findings []findings.Finding
		err      error
	}
	results := make([]result, len(e.rules))

	var wg sync.WaitGroup
	for i, r := range e.rules {
		wg.Add(1)
		go func(i int, r Rule) {
			defer wg.Done()
			defer func() {
				if p := recover(); p != nil {
					results[i] = result{err: fmt.Errorf("panic: %v", p)}
				}
			}()
			f, err := r.Check(ctx, cluster)
			results[i] = result{findings: f, err: err}
		}(i, r)
	}
	wg.Wait()

	var report Report
	for i, res := range results {
		if res.err != nil {
			report.Errors = append(report.Errors, RuleError{RuleID: e.rules[i].ID(), Err: res.err})
			continue
		}
		report.Findings = append(report.Findings, res.findings...)
	}
	SortFindings(report.Findings)
	return report
}

var severityRank = map[findings.Severity]int{
	findings.SeverityCritical: 0,
	findings.SeverityHigh:     1,
	findings.SeverityMedium:   2,
	findings.SeverityLow:      3,
	findings.SeverityInfo:     4,
}

// SortFindings orders findings by severity, then rule ID, then resource, deterministically.
func SortFindings(fs []findings.Finding) {
	sort.SliceStable(fs, func(a, b int) bool {
		x, y := fs[a], fs[b]
		if rx, ry := severityRank[x.Severity], severityRank[y.Severity]; rx != ry {
			return rx < ry
		}
		if x.ID != y.ID {
			return x.ID < y.ID
		}
		if x.Resource.Namespace != y.Resource.Namespace {
			return x.Resource.Namespace < y.Resource.Namespace
		}
		return x.Resource.Name < y.Resource.Name
	})
}
