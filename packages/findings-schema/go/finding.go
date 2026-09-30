// Package findings holds the Go types and validator for the VenomOps common finding format.
// It is equivalent to the pydantic models in python/findings_schema/models.py.
package findings

import "time"

// Source is the module that produced a finding.
type Source string

// Valid sources.
const (
	SourceKDoctor       Source = "kdoctor"
	SourcePRAgent       Source = "pr-agent"
	SourceArchCommittee Source = "arch-committee"
	SourceManual        Source = "manual"
)

// Severity of a finding.
type Severity string

// Valid severities.
const (
	SeverityCritical Severity = "critical"
	SeverityHigh     Severity = "high"
	SeverityMedium   Severity = "medium"
	SeverityLow      Severity = "low"
	SeverityInfo     Severity = "info"
)

// Risk of applying the suggested fix.
type Risk string

// Valid risk levels.
const (
	RiskLow    Risk = "low"
	RiskMedium Risk = "medium"
	RiskHigh   Risk = "high"
)

// Resource identifies the affected resource.
type Resource struct {
	Type         string `json:"type"`
	Name         string `json:"name"`
	Namespace    string `json:"namespace,omitempty"`
	Region       string `json:"region,omitempty"`
	AccountAlias string `json:"account_alias,omitempty"`
	Path         string `json:"path,omitempty"`
}

// Evidence is an observable proof of the problem.
type Evidence struct {
	Kind   string `json:"kind"`
	Detail string `json:"detail"`
}

// SuggestedFix describes how to fix the finding.
type SuggestedFix struct {
	Summary string   `json:"summary"`
	Steps   []string `json:"steps"`
	IaCHint string   `json:"iac_hint,omitempty"`
}

// Finding is the common contract shared by kdoctor, pr-agent and arch-committee.
type Finding struct {
	ID            string       `json:"id"`
	SchemaVersion string       `json:"schema_version"`
	Source        Source       `json:"source"`
	Severity      Severity     `json:"severity"`
	Title         string       `json:"title"`
	Resource      Resource     `json:"resource"`
	Evidence      []Evidence   `json:"evidence"`
	RootCause     string       `json:"root_cause"`
	SuggestedFix  SuggestedFix `json:"suggested_fix"`
	RiskOfFix     Risk         `json:"risk_of_fix"`
	References    []string     `json:"references,omitempty"`
	Tags          []string     `json:"tags,omitempty"`
	DetectedAt    time.Time    `json:"detected_at"`
}
