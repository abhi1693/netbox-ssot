package submission

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func TestGenerateKeyPairProducesMatchingEd25519Keys(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	privateKey, err := decodePrivateKey(keyPair.PrivateKey)
	if err != nil {
		t.Fatalf("decodePrivateKey() error = %v", err)
	}
	wantPublicKey := base64.RawURLEncoding.EncodeToString(privateKey.Public().(ed25519.PublicKey))
	if keyPair.PublicKey != wantPublicKey {
		t.Fatal("generated public key does not match private key")
	}
}

func TestDecodePrivateKeyRejectsMalformedValues(t *testing.T) {
	for _, value := range []string{"", "not+base64", base64.RawURLEncoding.EncodeToString([]byte("short"))} {
		if _, err := decodePrivateKey(value); err == nil {
			t.Fatalf("decodePrivateKey(%q) succeeded", value)
		}
	}
}
