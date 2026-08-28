package provider

import (
	"context"
	"testing"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

type stubCollector struct {
	manifest contracts.ProviderManifest
}

func (s stubCollector) Manifest() (contracts.ProviderManifest, error) {
	return s.manifest, nil
}

func (stubCollector) TestConnection(context.Context, contracts.ConnectionTestRequest, SecretResolver) contracts.ConnectionTestResult {
	return contracts.ConnectionTestResult{}
}

func (stubCollector) Collect(context.Context, contracts.CollectionRequest, SecretResolver) contracts.ObservationBatch {
	return contracts.ObservationBatch{}
}

func TestResolveDatasetsIncludesDependenciesInManifestOrder(t *testing.T) {
	manifest := contracts.ProviderManifest{Datasets: []contracts.DatasetDefinition{
		{ID: "sites", DependsOn: []string{}},
		{ID: "devices", DependsOn: []string{"sites"}},
		{ID: "interfaces", DependsOn: []string{"devices"}},
	}}

	resolved, err := ResolveDatasets(manifest, []string{"interfaces"})
	if err != nil {
		t.Fatalf("ResolveDatasets() error = %v", err)
	}
	want := []string{"sites", "devices", "interfaces"}
	for index := range want {
		if resolved[index] != want[index] {
			t.Fatalf("resolved = %v, want %v", resolved, want)
		}
	}
}

func TestRegistryRejectsDuplicateCollectorIdentity(t *testing.T) {
	manifest := contracts.ProviderManifest{
		ProviderID:      "netbox",
		ContractVersion: contracts.ContractVersion,
		AgentCompatibility: contracts.AgentCompatibility{
			CollectorID: "netbox",
		},
	}
	_, err := NewRegistry(stubCollector{manifest: manifest}, stubCollector{manifest: manifest})
	if err == nil {
		t.Fatal("NewRegistry() accepted duplicate collector identities")
	}
}
