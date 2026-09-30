package redact

import (
	"strings"
	"testing"
)

func TestStringRedactsSecrets(t *testing.T) {
	cases := []struct {
		name, in string
		gone     string // substring that must not survive
		kept     string // substring that must survive ("" = skip)
	}{
		{"aws key id", "key AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE", "leaked"},
		{"password pair", "db password=hunter2 failed", "hunter2", "failed"},
		{"quoted secret", `API_TOKEN: "abc123secret"`, "abc123secret", "API_TOKEN"},
		{"env style", "DB_PASSWORD=s3cr3t!", "s3cr3t!", "DB_PASSWORD"},
		{"bearer", "Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop", "Bearer"},
		{"jwt", "token eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4 end", "eyJzdWIiOiIx", "end"},
		{"url creds", "pull https://user:pa55@registry.example.com/img", "pa55", "registry.example.com"},
		{"account id", "arn:aws:iam::123456789012:role/app", "123456789012", "role/app"},
		{"private key", "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----", "MIIBOgIBAAJB", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := String(c.in)
			if strings.Contains(got, c.gone) {
				t.Fatalf("%q still contains %q", got, c.gone)
			}
			if c.kept != "" && !strings.Contains(got, c.kept) {
				t.Fatalf("%q lost useful text %q", got, c.kept)
			}
		})
	}
}

func TestStringKeepsHarmlessText(t *testing.T) {
	in := "container exited with code 137; image sha256:" + strings.Repeat("ab", 32) + " restart 14"
	if got := String(in); got != in {
		t.Fatalf("harmless text changed: %q", got)
	}
}
