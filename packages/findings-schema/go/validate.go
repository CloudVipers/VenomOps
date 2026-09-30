package findings

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"golang.org/x/text/language"
	"golang.org/x/text/message"

	"github.com/CloudVipers/VenomOps/packages/findings-schema/schema"
)

// Issue is one schema violation. Path is a JSON Pointer ("" is the document root) and Keyword the
// JSON Schema keyword that failed; both match the Python validator so the two can be compared.
type Issue struct {
	Path    string `json:"path"`
	Keyword string `json:"keyword"`
	Message string `json:"message"`
}

func (i Issue) String() string {
	path := i.Path
	if path == "" {
		path = "<root>"
	}
	return fmt.Sprintf("%s: %s [%s]", path, i.Message, i.Keyword)
}

// ValidationError is returned by Parse when the document violates the schema.
type ValidationError struct{ Issues []Issue }

func (e *ValidationError) Error() string {
	lines := make([]string, 0, len(e.Issues))
	for _, i := range e.Issues {
		lines = append(lines, "  - "+i.String())
	}
	return "invalid finding:\n" + strings.Join(lines, "\n")
}

var (
	compileOnce sync.Once
	compiled    *jsonschema.Schema
	compileErr  error
)

func compiledSchema() (*jsonschema.Schema, error) {
	compileOnce.Do(func() {
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(schema.JSON))
		if err != nil {
			compileErr = fmt.Errorf("parse embedded schema: %w", err)
			return
		}
		c := jsonschema.NewCompiler()
		if err := c.AddResource("finding.schema.json", doc); err != nil {
			compileErr = fmt.Errorf("add schema: %w", err)
			return
		}
		compiled, compileErr = c.Compile("finding.schema.json")
	})
	return compiled, compileErr
}

func pointer(parts []string) string {
	var b strings.Builder
	for _, p := range parts {
		b.WriteByte('/')
		b.WriteString(strings.NewReplacer("~", "~0", "/", "~1").Replace(p))
	}
	return b.String()
}

func collect(ve *jsonschema.ValidationError, out *[]Issue, printer *message.Printer) {
	if len(ve.Causes) == 0 {
		kw := ve.ErrorKind.KeywordPath()
		keyword := ""
		if len(kw) > 0 {
			keyword = kw[len(kw)-1]
		}
		*out = append(*out, Issue{
			Path:    pointer(ve.InstanceLocation),
			Keyword: keyword,
			Message: ve.ErrorKind.LocalizedString(printer),
		})
		return
	}
	for _, c := range ve.Causes {
		collect(c, out, printer)
	}
}

// Validate checks a raw JSON document against the schema and returns every violation (an empty
// slice means it is valid). Malformed JSON is reported as an Issue with keyword "json"; the error
// return is only for internal failures (e.g. the embedded schema cannot be compiled).
func Validate(data []byte) ([]Issue, error) {
	sch, err := compiledSchema()
	if err != nil {
		return nil, err
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err != nil {
		return []Issue{{Path: "", Keyword: "json", Message: "malformed JSON: " + err.Error()}}, nil
	}
	verr := sch.Validate(inst)
	if verr == nil {
		return []Issue{}, nil
	}
	var ve *jsonschema.ValidationError
	if !errors.As(verr, &ve) {
		return nil, verr
	}
	issues := []Issue{}
	collect(ve, &issues, message.NewPrinter(language.English))
	sort.SliceStable(issues, func(a, b int) bool {
		if issues[a].Path != issues[b].Path {
			return issues[a].Path < issues[b].Path
		}
		return issues[a].Keyword < issues[b].Keyword
	})
	return issues, nil
}

// Parse validates the document against the schema (the contract) and, if valid, decodes it into a
// typed Finding. It returns *ValidationError listing every issue when the document is invalid.
func Parse(data []byte) (*Finding, error) {
	issues, err := Validate(data)
	if err != nil {
		return nil, err
	}
	if len(issues) > 0 {
		return nil, &ValidationError{Issues: issues}
	}
	var f Finding
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("decode finding: %w", err)
	}
	return &f, nil
}
