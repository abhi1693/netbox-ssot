package secrets

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEnvironmentResolverReadsOnlyExplicitEnvironmentReferences(t *testing.T) {
	t.Setenv("NETBOX_TEST_TOKEN", "secret-value")
	value, err := (EnvironmentResolver{}).Resolve(context.Background(), "env://NETBOX_TEST_TOKEN")
	if err != nil || value != "secret-value" {
		t.Fatalf("Resolve() = %q, %v", value, err)
	}
}

func TestLocalResolverReadsPrivateFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "signing-key")
	if err := os.WriteFile(path, []byte("secret-value\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	value, err := (LocalResolver{}).Resolve(context.Background(), "file://"+path)
	if err != nil || value != "secret-value" {
		t.Fatalf("Resolve() = %q, %v", value, err)
	}
}

func TestLocalResolverRejectsUnsafeFilesWithoutLeakingPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sensitive-name")
	if err := os.WriteFile(path, []byte("secret-value"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, reference := range []string{
		"file://" + path,
		"file://remotehost/tmp/key",
		"file://relative-key",
	} {
		_, err := (LocalResolver{}).Resolve(context.Background(), reference)
		if err == nil {
			t.Fatalf("Resolve() accepted unsafe reference %q", reference)
		}
		if strings.Contains(err.Error(), "sensitive-name") || strings.Contains(err.Error(), "secret-value") {
			t.Fatalf("Resolve() leaked secret details: %v", err)
		}
	}
}

func TestLocalResolverRejectsSymbolicLinks(t *testing.T) {
	directory := t.TempDir()
	target := filepath.Join(directory, "target")
	link := filepath.Join(directory, "link")
	if err := os.WriteFile(target, []byte("secret-value"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if _, err := (LocalResolver{}).Resolve(context.Background(), "file://"+link); err == nil {
		t.Fatal("Resolve() accepted a symbolic link")
	}
}

func TestLocalResolverRejectsDirectoriesAndOversizedFiles(t *testing.T) {
	directory := t.TempDir()
	oversized := filepath.Join(directory, "oversized")
	if err := os.WriteFile(oversized, make([]byte, (64<<10)+1), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, reference := range []string{"file://" + directory, "file://" + oversized, "file:///tmp/key?query=yes"} {
		if _, err := (LocalResolver{}).Resolve(context.Background(), reference); err == nil {
			t.Fatalf("Resolve() accepted unsafe reference %q", reference)
		}
	}
}

func TestEnvironmentResolverErrorsDoNotExposeReferenceOrValue(t *testing.T) {
	t.Setenv("NETBOX_TEST_TOKEN", "secret-value")
	_, err := (EnvironmentResolver{}).Resolve(context.Background(), "file:///tmp/secret-value")
	if err == nil {
		t.Fatal("Resolve() accepted an unsupported scheme")
	}
	if strings.Contains(err.Error(), "secret-value") || strings.Contains(err.Error(), "/tmp") {
		t.Fatalf("Resolve() leaked secret reference details: %v", err)
	}
}
