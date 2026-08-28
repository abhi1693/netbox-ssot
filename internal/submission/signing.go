package submission

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
)

const signatureContext = "netbox-ssot-agent-v1"

type KeyPair struct {
	PublicKey  string `json:"public_key"`
	PrivateKey string `json:"private_key"`
}

func GenerateKeyPair() (KeyPair, error) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return KeyPair{}, errors.New("generate Ed25519 key pair")
	}
	seed := privateKey.Seed()
	return KeyPair{
		PublicKey:  base64.RawURLEncoding.EncodeToString(publicKey),
		PrivateKey: base64.RawURLEncoding.EncodeToString(seed),
	}, nil
}

func decodePrivateKey(value string) (ed25519.PrivateKey, error) {
	seed, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil || len(seed) != ed25519.SeedSize {
		return nil, errors.New("invalid Ed25519 private key")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

func signingPayload(agentID string, timestamp int64, body []byte) []byte {
	digest := sha256.Sum256(body)
	return fmt.Appendf(nil, "%s\n%s\n%d\n%s", signatureContext, agentID, timestamp, hex.EncodeToString(digest[:]))
}

func signBody(privateKeyValue string, agentID string, timestamp int64, body []byte) (string, error) {
	privateKey, err := decodePrivateKey(privateKeyValue)
	if err != nil {
		return "", err
	}
	signature := ed25519.Sign(privateKey, signingPayload(agentID, timestamp, body))
	return base64.RawURLEncoding.EncodeToString(signature), nil
}
