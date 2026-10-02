// Package redact removes secrets and sensitive identifiers from free text. Findings can flow to other
// systems (pull requests, LLM prompts), so anything copied from a cluster (logs, events) goes
// through here first.
package redact

import "regexp"

type rule struct {
	re   *regexp.Regexp
	repl string
}

var rules = []rule{
	{regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----`), "[REDACTED-PRIVATE-KEY]"},
	{regexp.MustCompile(`\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b`), "[REDACTED-AWS-KEY-ID]"},
	{regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b`), "[REDACTED-JWT]"},
	{regexp.MustCompile(`(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}`), "${1}[REDACTED]"},
	{regexp.MustCompile(`(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@`), "${1}[REDACTED]@"},
	{regexp.MustCompile(`(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[A-Za-z0-9_.-]*)(\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)`), "${1}${2}[REDACTED]"},
	{regexp.MustCompile(`\b\d{12}\b`), "[ACCOUNT-ID]"},
}

// String returns s with secrets and 12-digit AWS account IDs replaced by placeholders.
func String(s string) string {
	for _, r := range rules {
		s = r.re.ReplaceAllString(s, r.repl)
	}
	return s
}
