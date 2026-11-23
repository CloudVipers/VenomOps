package redact

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// corpusItem mirrors examples/benchmark/secrets/corpus.json (experiment E4). The same corpus runs against the
// Python redactors of pr-agent and arch-committee, so the three implementations are held to the same expectations.
type corpusItem struct {
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	Expected string `json:"expected"`
	Text     string `json:"text"`
	Secret   string `json:"secret"`
}

func loadCorpus(t *testing.T) []corpusItem {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "examples", "benchmark", "secrets", "corpus.json"))
	if err != nil {
		t.Fatalf("read corpus: %v", err)
	}
	var doc struct {
		Items []corpusItem `json:"items"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse corpus: %v", err)
	}
	if len(doc.Items) == 0 {
		t.Fatal("empty corpus")
	}
	return doc.Items
}

func TestStringMatchesTheSecretsCorpus(t *testing.T) {
	for _, item := range loadCorpus(t) {
		t.Run(item.ID, func(t *testing.T) {
			got := String(item.Text)
			switch item.Expected {
			case "redacted":
				if strings.Contains(got, item.Secret) {
					t.Fatalf("leaked: %q", got)
				}
			case "known_gap":
				if !strings.Contains(got, item.Secret) {
					t.Fatalf("now redacted: mark it 'redacted' in the corpus")
				}
			case "unchanged":
				if got != item.Text {
					t.Fatalf("false positive: %q", got)
				}
			case "known_false_positive":
				if got == item.Text {
					t.Fatalf("no longer changes: mark it 'unchanged' in the corpus")
				}
			default:
				t.Fatalf("unknown expectation %q", item.Expected)
			}
		})
	}
}
