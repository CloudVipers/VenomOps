// Package output renders findings as a human-readable table or as JSON.
package output

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"text/tabwriter"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

// Format is an output format.
type Format string

// Supported formats.
const (
	FormatTable Format = "table"
	FormatJSON  Format = "json"
)

// ParseFormat validates the --output flag value.
func ParseFormat(s string) (Format, error) {
	switch Format(s) {
	case FormatTable, FormatJSON:
		return Format(s), nil
	}
	return "", fmt.Errorf("unsupported output format %q (use table or json)", s)
}

// JSON writes the findings as a JSON array. Every finding is validated against findings-schema
// first, so consumers (pr-agent, arch-committee) can rely on the contract.
func JSON(w io.Writer, fs []findings.Finding) error {
	if fs == nil {
		fs = []findings.Finding{}
	}
	for _, f := range fs {
		raw, err := json.Marshal(f)
		if err != nil {
			return fmt.Errorf("marshal finding %s: %w", f.ID, err)
		}
		issues, err := findings.Validate(raw)
		if err != nil {
			return err
		}
		if len(issues) > 0 {
			return fmt.Errorf("finding %s violates findings-schema: %v", f.ID, issues)
		}
	}
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(fs)
}

// Table writes a summary table followed by a plain-language detail block for each finding.
func Table(w io.Writer, fs []findings.Finding, ruleErrors []engine.RuleError) error {
	if len(fs) == 0 {
		fmt.Fprintln(w, "✔ No se encontraron problemas con las reglas activas.")
	} else {
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		fmt.Fprintln(tw, "SEVERIDAD\tID\tRECURSO\tPROBLEMA")
		for _, f := range fs {
			fmt.Fprintf(tw, "%s\t%s\t%s\t%s\n", strings.ToUpper(string(f.Severity)), f.ID, resourceLabel(f.Resource), f.Title)
		}
		if err := tw.Flush(); err != nil {
			return err
		}
		for _, f := range fs {
			writeDetail(w, f)
		}
	}
	for _, re := range ruleErrors {
		fmt.Fprintf(w, "\n⚠ La regla %s no pudo ejecutarse: %v\n", re.RuleID, re.Err)
	}
	return nil
}

func resourceLabel(r findings.Resource) string {
	if r.Namespace != "" {
		return fmt.Sprintf("%s %s/%s", r.Type, r.Namespace, r.Name)
	}
	return fmt.Sprintf("%s %s", r.Type, r.Name)
}

func indent(s, prefix string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	for i, l := range lines {
		lines[i] = prefix + l
	}
	return strings.Join(lines, "\n")
}

func writeDetail(w io.Writer, f findings.Finding) {
	fmt.Fprintf(w, "\n── [%s] %s · %s\n", strings.ToUpper(string(f.Severity)), f.ID, f.Title)
	fmt.Fprintf(w, "Recurso: %s\n", resourceLabel(f.Resource))
	fmt.Fprintf(w, "Causa probable: %s\n", f.RootCause)
	fmt.Fprintln(w, "Evidencia:")
	for _, e := range f.Evidence {
		if strings.Contains(e.Detail, "\n") {
			fmt.Fprintf(w, "  • %s:\n%s\n", e.Kind, indent(e.Detail, "      "))
		} else {
			fmt.Fprintf(w, "  • %s: %s\n", e.Kind, e.Detail)
		}
	}
	fmt.Fprintf(w, "Cómo arreglarlo (riesgo %s): %s\n", f.RiskOfFix, f.SuggestedFix.Summary)
	for i, s := range f.SuggestedFix.Steps {
		fmt.Fprintf(w, "  %d. %s\n", i+1, s)
	}
	if f.SuggestedFix.IaCHint != "" {
		fmt.Fprintf(w, "  IaC: %s\n", f.SuggestedFix.IaCHint)
	}
	for _, ref := range f.References {
		fmt.Fprintf(w, "  Ref: %s\n", ref)
	}
}
