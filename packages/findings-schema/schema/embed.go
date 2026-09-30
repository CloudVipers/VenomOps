// Package schema embeds the canonical finding JSON Schema so Go consumers validate against the
// exact same file as the Python package.
package schema

import _ "embed"

// JSON is the content of finding.schema.json.
//
//go:embed finding.schema.json
var JSON []byte
