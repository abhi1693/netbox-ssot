package provider

import (
	"context"
	"fmt"
	"sort"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

type SecretResolver interface {
	Resolve(context.Context, string) (string, error)
}

type Collector interface {
	Manifest() (contracts.ProviderManifest, error)
	TestConnection(context.Context, contracts.ConnectionTestRequest, SecretResolver) contracts.ConnectionTestResult
	Collect(context.Context, contracts.CollectionRequest, SecretResolver) contracts.ObservationBatch
}

type Registry struct {
	collectors map[string]Collector
}

func NewRegistry(collectors ...Collector) (*Registry, error) {
	registry := &Registry{collectors: make(map[string]Collector, len(collectors))}
	for _, collector := range collectors {
		manifest, err := collector.Manifest()
		if err != nil {
			return nil, fmt.Errorf("load collector manifest: %w", err)
		}
		if manifest.ProviderID == "" || manifest.ContractVersion != contracts.ContractVersion {
			return nil, fmt.Errorf("collector manifest is missing a compatible identity")
		}
		if manifest.AgentCompatibility.CollectorID != manifest.ProviderID {
			return nil, fmt.Errorf("collector identity does not match provider identity")
		}
		if _, exists := registry.collectors[manifest.ProviderID]; exists {
			return nil, fmt.Errorf("duplicate collector ID %q", manifest.ProviderID)
		}
		registry.collectors[manifest.ProviderID] = collector
	}
	return registry, nil
}

func (r *Registry) Get(providerID string) (Collector, bool) {
	collector, found := r.collectors[providerID]
	return collector, found
}

func (r *Registry) Manifests() ([]contracts.ProviderManifest, error) {
	ids := make([]string, 0, len(r.collectors))
	for id := range r.collectors {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	manifests := make([]contracts.ProviderManifest, 0, len(ids))
	for _, id := range ids {
		manifest, err := r.collectors[id].Manifest()
		if err != nil {
			return nil, fmt.Errorf("load collector manifest: %w", err)
		}
		manifests = append(manifests, manifest)
	}
	return manifests, nil
}

func ResolveDatasets(manifest contracts.ProviderManifest, requested []string) ([]string, error) {
	known := make(map[string]contracts.DatasetDefinition, len(manifest.Datasets))
	selected := make(map[string]bool, len(requested))
	for _, dataset := range manifest.Datasets {
		known[dataset.ID] = dataset
	}

	pending := append([]string(nil), requested...)
	for len(pending) > 0 {
		id := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		dataset, exists := known[id]
		if !exists {
			return nil, fmt.Errorf("unknown dataset %q", id)
		}
		if selected[id] {
			continue
		}
		selected[id] = true
		pending = append(pending, dataset.DependsOn...)
	}

	resolved := make([]string, 0, len(selected))
	for _, dataset := range manifest.Datasets {
		if selected[dataset.ID] {
			resolved = append(resolved, dataset.ID)
		}
	}
	return resolved, nil
}
