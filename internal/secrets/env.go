package secrets

import (
	"context"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var environmentName = regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)

type EnvironmentResolver struct{}

func (EnvironmentResolver) Resolve(_ context.Context, reference string) (string, error) {
	const prefix = "env://"
	if !strings.HasPrefix(reference, prefix) {
		return "", fmt.Errorf("unsupported secret reference scheme")
	}
	name := strings.TrimPrefix(reference, prefix)
	if !environmentName.MatchString(name) {
		return "", fmt.Errorf("invalid environment secret reference")
	}
	value, found := os.LookupEnv(name)
	if !found || value == "" {
		return "", fmt.Errorf("secret reference is not configured")
	}
	return value, nil
}

type LocalResolver struct{}

func (LocalResolver) Resolve(ctx context.Context, reference string) (string, error) {
	if strings.HasPrefix(reference, "env://") {
		return (EnvironmentResolver{}).Resolve(ctx, reference)
	}
	path, err := FileReferencePath(reference)
	if err != nil {
		return "", err
	}
	pathInfo, err := os.Lstat(path)
	if err != nil || !pathInfo.Mode().IsRegular() || pathInfo.Mode().Perm()&0o077 != 0 {
		return "", fmt.Errorf("secret file permissions or type are unsafe")
	}
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("secret file could not be opened")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 || !os.SameFile(pathInfo, info) {
		return "", fmt.Errorf("secret file permissions or type are unsafe")
	}
	value, err := io.ReadAll(io.LimitReader(file, (64<<10)+1))
	if err != nil || len(value) > 64<<10 {
		return "", fmt.Errorf("secret file could not be read")
	}
	resolved := strings.TrimSpace(string(value))
	if resolved == "" {
		return "", fmt.Errorf("secret reference is not configured")
	}
	return resolved, nil
}

func FileReferencePath(reference string) (string, error) {
	parsed, err := url.Parse(reference)
	if err != nil || parsed.Scheme != "file" || parsed.Host != "" || parsed.RawQuery != "" ||
		parsed.Fragment != "" || !filepath.IsAbs(parsed.Path) {
		return "", fmt.Errorf("invalid file secret reference")
	}
	return filepath.Clean(parsed.Path), nil
}
