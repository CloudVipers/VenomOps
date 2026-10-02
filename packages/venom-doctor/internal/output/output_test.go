package output_test

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/output"
)

func sample() findings.Finding {
	return findings.Finding{
		ID: "KD-K8S-001", SchemaVersion: "1.1.0", Source: findings.SourceVenomDoctor, Severity: findings.SeverityHigh,
		Title:     "Pod payments/api en CrashLoopBackOff",
		Resource:  findings.Resource{Type: "Pod", Name: "api", Namespace: "payments"},
		Evidence:  []findings.Evidence{{Kind: "exit-code", Detail: "exitCode=1"}, {Kind: "log-tail", Detail: "line1\nline2"}},
		RootCause: "Falta una variable de entorno.",
		SuggestedFix: findings.SuggestedFix{Summary: "Definir DB_HOST.", Steps: []string{"Agregar DB_HOST", "Reiniciar"},
			IaCHint: "env { name = \"DB_HOST\" }"},
		RiskOfFix:  findings.RiskLow,
		References: []string{"https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/"},
		DetectedAt: time.Date(2026, 9, 30, 12, 0, 0, 0, time.UTC),
	}
}

func TestJSONIsAnArrayThatValidatesAgainstTheSchema(t *testing.T) {
	var buf bytes.Buffer
	if err := output.JSON(&buf, []findings.Finding{sample()}); err != nil {
		t.Fatal(err)
	}
	var arr []json.RawMessage
	if err := json.Unmarshal(buf.Bytes(), &arr); err != nil || len(arr) != 1 {
		t.Fatalf("expected a JSON array with one finding: %v\n%s", err, buf.String())
	}
	issues, err := findings.Validate(arr[0])
	if err != nil || len(issues) != 0 {
		t.Fatalf("output must satisfy findings-schema: %v %v", issues, err)
	}
}

func TestJSONEmptyIsAnEmptyArray(t *testing.T) {
	var buf bytes.Buffer
	if err := output.JSON(&buf, nil); err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(buf.String()) != "[]" {
		t.Fatalf("got %q", buf.String())
	}
}

func TestJSONRefusesAFindingThatBreaksTheContract(t *testing.T) {
	bad := sample()
	bad.ID = "not-an-id"
	var buf bytes.Buffer
	if err := output.JSON(&buf, []findings.Finding{bad}); err == nil || !strings.Contains(err.Error(), "findings-schema") {
		t.Fatalf("expected a schema violation error, got %v", err)
	}
	if buf.Len() != 0 {
		t.Fatal("nothing must be written when validation fails")
	}
}

func TestTableShowsSummaryAndDetail(t *testing.T) {
	var buf bytes.Buffer
	errs := []engine.RuleError{{RuleID: "KD-K8S-009", Err: errTest{}}}
	if err := output.Table(&buf, []findings.Finding{sample()}, errs); err != nil {
		t.Fatal(err)
	}
	out := buf.String()
	for _, want := range []string{"SEVERIDAD", "HIGH", "KD-K8S-001", "Pod payments/api", "Causa probable:", "Evidencia:",
		"exit-code: exitCode=1", "      line2", "1. Agregar DB_HOST", "IaC:", "Ref: https://kubernetes.io", "KD-K8S-009 no pudo ejecutarse"} {
		if !strings.Contains(out, want) {
			t.Fatalf("table output is missing %q:\n%s", want, out)
		}
	}
}

func TestTableWithoutFindings(t *testing.T) {
	var buf bytes.Buffer
	if err := output.Table(&buf, nil, nil); err != nil || !strings.Contains(buf.String(), "No se encontraron problemas") {
		t.Fatalf("%v %q", err, buf.String())
	}
}

func TestParseFormat(t *testing.T) {
	for _, ok := range []string{"table", "json"} {
		if _, err := output.ParseFormat(ok); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := output.ParseFormat("yaml"); err == nil {
		t.Fatal("yaml should be rejected")
	}
}

type errTest struct{}

func (errTest) Error() string { return "api down" }
