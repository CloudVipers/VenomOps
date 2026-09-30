// Command findings-validate validates finding JSON files against the shared schema.
//
// It prints one JSON object per file ({"file","valid","issues":[{path,keyword,message}]}) and exits
// 0 if every file is valid, 1 if at least one is invalid, 2 on internal errors. The cross-language
// parity test compares this output with the Python validator.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
)

type result struct {
	File   string           `json:"file"`
	Valid  bool             `json:"valid"`
	Issues []findings.Issue `json:"issues"`
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: findings-validate <file.json>...")
		os.Exit(2)
	}
	enc := json.NewEncoder(os.Stdout)
	anyInvalid := false
	for _, path := range os.Args[1:] {
		data, err := os.ReadFile(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		issues, err := findings.Validate(data)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		if len(issues) > 0 {
			anyInvalid = true
		}
		if err := enc.Encode(result{File: path, Valid: len(issues) == 0, Issues: issues}); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
	}
	if anyInvalid {
		os.Exit(1)
	}
}
