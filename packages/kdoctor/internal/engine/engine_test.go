package engine_test

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

type fakeRule struct {
	id    string
	out   []findings.Finding
	err   error
	panic bool
	delay time.Duration
	ran   *atomic.Int32
}

func (f fakeRule) ID() string          { return f.id }
func (f fakeRule) Description() string { return "fake " + f.id }
func (f fakeRule) Check(context.Context, engine.ClusterReader) ([]findings.Finding, error) {
	if f.ran != nil {
		f.ran.Add(1)
	}
	time.Sleep(f.delay)
	if f.panic {
		panic("boom")
	}
	return f.out, f.err
}

func finding(id string, sev findings.Severity, ns, name string) findings.Finding {
	return findings.Finding{ID: id, Severity: sev, Resource: findings.Resource{Type: "Pod", Namespace: ns, Name: name}}
}

func TestRunSortsBySeverityThenIDThenResource(t *testing.T) {
	e, err := engine.New(
		fakeRule{id: "A", out: []findings.Finding{finding("A", findings.SeverityLow, "ns", "z"), finding("A", findings.SeverityCritical, "ns", "b")}},
		fakeRule{id: "B", out: []findings.Finding{finding("B", findings.SeverityCritical, "ns", "a"), finding("B", findings.SeverityInfo, "ns", "a")}},
	)
	if err != nil {
		t.Fatal(err)
	}
	got := e.Run(context.Background(), nil).Findings
	want := []string{"A/critical/b", "B/critical/a", "A/low/z", "B/info/a"}
	if len(got) != len(want) {
		t.Fatalf("got %d findings", len(got))
	}
	for i, f := range got {
		if s := f.ID + "/" + string(f.Severity) + "/" + f.Resource.Name; s != want[i] {
			t.Fatalf("position %d: got %s want %s", i, s, want[i])
		}
	}
}

func TestFailingAndPanickingRulesDoNotStopTheOthers(t *testing.T) {
	e, _ := engine.New(
		fakeRule{id: "OK", out: []findings.Finding{finding("OK", findings.SeverityHigh, "ns", "a")}},
		fakeRule{id: "ERR", err: errors.New("api down")},
		fakeRule{id: "PANIC", panic: true},
	)
	rep := e.Run(context.Background(), nil)
	if len(rep.Findings) != 1 || len(rep.Errors) != 2 {
		t.Fatalf("findings=%d errors=%v", len(rep.Findings), rep.Errors)
	}
	ids := map[string]bool{}
	for _, re := range rep.Errors {
		ids[re.RuleID] = true
		if re.Error() == "" {
			t.Fatal("empty rule error")
		}
	}
	if !ids["ERR"] || !ids["PANIC"] {
		t.Fatalf("errors not attributed to rules: %v", rep.Errors)
	}
}

func TestRulesRunConcurrently(t *testing.T) {
	var ran atomic.Int32
	var rules []engine.Rule
	for _, id := range []string{"1", "2", "3", "4"} {
		rules = append(rules, fakeRule{id: id, delay: 100 * time.Millisecond, ran: &ran})
	}
	e, _ := engine.New(rules...)
	start := time.Now()
	e.Run(context.Background(), nil)
	if elapsed := time.Since(start); elapsed > 300*time.Millisecond {
		t.Fatalf("4 rules x100ms took %v: they are not running concurrently", elapsed)
	}
	if ran.Load() != 4 {
		t.Fatalf("ran %d rules", ran.Load())
	}
}

func TestDuplicateRuleIDIsRejected(t *testing.T) {
	if _, err := engine.New(fakeRule{id: "X"}, fakeRule{id: "X"}); err == nil {
		t.Fatal("expected duplicate registration error")
	}
	e, _ := engine.New(fakeRule{id: "X"})
	if got := e.Rules(); len(got) != 1 || got[0].ID() != "X" {
		t.Fatalf("rules: %v", got)
	}
}
