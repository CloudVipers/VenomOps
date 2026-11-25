package findings

// Detached HMAC-SHA256 signatures for findings documents (ADR 0012). Equivalent to
// python/findings_schema/signature.py: the signature covers the exact bytes written and lives in "<file>.sig".

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

// Signature constants shared with the Python implementation.
const (
	SignatureEnvKey     = "VENOMOPS_FINDINGS_KEY"
	SignatureAlgorithm  = "HMAC-SHA256"
	SignatureMinKeySize = 32
)

// ErrSignature is wrapped by every signature failure.
var ErrSignature = errors.New("findings signature")

// Signature is the content of a "<file>.sig" sidecar.
type Signature struct {
	Alg   string `json:"alg"`
	KeyID string `json:"key_id"`
	Sig   string `json:"sig"`
}

// SignatureKeyFromEnv returns the shared key from VENOMOPS_FINDINGS_KEY (nil if unset); short keys are refused.
func SignatureKeyFromEnv() ([]byte, error) {
	value := os.Getenv(SignatureEnvKey)
	if value == "" {
		return nil, nil
	}
	if len(value) < SignatureMinKeySize {
		return nil, fmt.Errorf("%w: %s must be at least %d bytes long", ErrSignature, SignatureEnvKey, SignatureMinKeySize)
	}
	return []byte(value), nil
}

// KeyID is the public identifier of a key: the first 12 hex characters of its SHA-256.
func KeyID(key []byte) string {
	sum := sha256.Sum256(key)
	return hex.EncodeToString(sum[:])[:12]
}

// Sign returns the detached signature of data.
func Sign(data, key []byte) Signature {
	mac := hmac.New(sha256.New, key)
	mac.Write(data)
	return Signature{Alg: SignatureAlgorithm, KeyID: KeyID(key), Sig: hex.EncodeToString(mac.Sum(nil))}
}

// Verify checks a detached signature of data.
func Verify(data []byte, s Signature, key []byte) error {
	if s.Alg != SignatureAlgorithm {
		return fmt.Errorf("%w: unsupported algorithm %q", ErrSignature, s.Alg)
	}
	if s.KeyID != KeyID(key) {
		return fmt.Errorf("%w: signed with a different key", ErrSignature)
	}
	want := Sign(data, key).Sig
	if !hmac.Equal([]byte(want), []byte(s.Sig)) {
		return fmt.Errorf("%w: invalid signature (modified, or not produced with this key)", ErrSignature)
	}
	return nil
}

// MarshalSignature renders a sidecar file's content.
func MarshalSignature(s Signature) ([]byte, error) {
	b, err := json.Marshal(s)
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}
