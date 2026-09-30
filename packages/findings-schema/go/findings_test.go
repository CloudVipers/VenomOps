package findings_test

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
)

const examplesDir = "../examples"

type expectation struct {
	Path    string `json:"path"`
	Keyword string `json:"keyword"`
}

func files(t *testing.T, kind string) []string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(examplesDir, kind, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(paths)
	return paths
}

func read(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestEnoughExamples(t *testing.T) {
	if n := len(files(t, "valid")); n < 5 {
		t.Fatalf("need >=5 valid examples, have %d", n)
	}
	if n := len(files(t, "invalid")); n < 5 {
		t.Fatalf("need >=5 invalid examples, have %d", n)
	}
}

func TestValidExamplesPassAndRoundTrip(t *testing.T) {
	for _, path := range files(t, "valid") {
		t.Run(filepath.Base(path), func(t *testing.T) {
			data := read(t, path)
			issues, err := findings.Validate(data)
			if err != nil {
				t.Fatal(err)
			}
			if len(issues) != 0 {
				t.Fatalf("expected valid, got %v", issues)
			}
			f, err := findings.Parse(data)
			if err != nil {
				t.Fatal(err)
			}
			out, err := json.Marshal(f)
			if err != nil {
				t.Fatal(err)
			}
			again, err := findings.Validate(out)
			if err != nil || len(again) != 0 {
				t.Fatalf("round trip produced an invalid document: %v %v\n%s", err, again, out)
			}
		})
	}
}

func TestInvalidExamplesFailWithExpectedError(t *testing.T) {
	var expected map[string]expectation
	if err := json.Unmarshal(read(t, filepath.Join(examplesDir, "expected-errors.json")), &expected); err != nil {
		t.Fatal(err)
	}
	invalid := files(t, "invalid")
	if len(invalid) != len(expected) {
		t.Fatalf("%d invalid examples but %d expectations", len(invalid), len(expected))
	}
	for _, path := range invalid {
		name := filepath.Base(path)
		t.Run(name, func(t *testing.T) {
			issues, err := findings.Validate(read(t, path))
			if err != nil {
				t.Fatal(err)
			}
			if len(issues) == 0 {
				t.Fatal("expected the document to be invalid")
			}
			want := expected[name]
			for _, i := range issues {
				if i.Path == want.Path && i.Keyword == want.Keyword {
					return
				}
			}
			t.Fatalf("missing expected issue %+v in %v", want, issues)
		})
	}
}

func TestErrorMessagesPointToTheField(t *testing.T) {
	_, err := findings.Parse([]byte(`{"id":"kd-1"}`))
	var verr *findings.ValidationError
	if !errors.As(err, &verr) {
		t.Fatalf("expected *ValidationError, got %T %v", err, err)
	}
	msg := verr.Error()
	if !strings.Contains(msg, "/id") || !strings.Contains(msg, "<root>") {
		t.Fatalf("message should name the field and the root: %s", msg)
	}
}

func TestMalformedJSONIsAnIssueNotAPanic(t *testing.T) {
	issues, err := findings.Validate([]byte(`{not json`))
	if err != nil {
		t.Fatal(err)
	}
	if len(issues) != 1 || issues[0].Keyword != "json" {
		t.Fatalf("unexpected issues: %v", issues)
	}
}

func TestParseRejectsInvalidBeforeDecoding(t *testing.T) {
	if _, err := findings.Parse([]byte(`{"id":"x"}`)); err == nil {
		t.Fatal("expected an error")
	}
}
