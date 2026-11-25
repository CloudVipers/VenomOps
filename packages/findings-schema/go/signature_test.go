package findings

import (
	"errors"
	"testing"
)

// Same vector as python/tests/test_signature.py: both implementations must agree byte for byte.
var (
	vectorKey  = []byte("test-key-0123456789abcdef0123456789abcdef")
	vectorData = []byte("[{\"id\": \"X\"}]\n")
)

const (
	vectorSig   = "e270cdfd6eef0d22b8660eb89f7fee637d1e65b6f2abbc24d8a754ed99801d5d"
	vectorKeyID = "6a41f8cfb764"
)

func TestSignMatchesTheSharedVector(t *testing.T) {
	s := Sign(vectorData, vectorKey)
	if s.Sig != vectorSig || s.KeyID != vectorKeyID || s.Alg != SignatureAlgorithm {
		t.Fatalf("unexpected signature %+v", s)
	}
	if err := Verify(vectorData, s, vectorKey); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyRejectsTamperingAndOtherKeys(t *testing.T) {
	s := Sign(vectorData, vectorKey)
	cases := map[string]func() error{
		"modified data": func() error { return Verify([]byte("[]\n"), s, vectorKey) },
		"other key":     func() error { return Verify(vectorData, s, []byte("another-key-0123456789abcdef0123456789")) },
		"other alg":     func() error { return Verify(vectorData, Signature{Alg: "none", KeyID: s.KeyID, Sig: s.Sig}, vectorKey) },
	}
	for name, fn := range cases {
		if err := fn(); !errors.Is(err, ErrSignature) {
			t.Errorf("%s: expected ErrSignature, got %v", name, err)
		}
	}
}

func TestSignatureKeyFromEnv(t *testing.T) {
	t.Setenv(SignatureEnvKey, "")
	if k, err := SignatureKeyFromEnv(); k != nil || err != nil {
		t.Fatalf("unset key: %v %v", k, err)
	}
	t.Setenv(SignatureEnvKey, "short")
	if _, err := SignatureKeyFromEnv(); !errors.Is(err, ErrSignature) {
		t.Fatalf("short key accepted: %v", err)
	}
	t.Setenv(SignatureEnvKey, string(vectorKey))
	if k, err := SignatureKeyFromEnv(); err != nil || string(k) != string(vectorKey) {
		t.Fatalf("valid key: %v", err)
	}
}
