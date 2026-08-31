package unifiprovider

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
)

const (
	defaultPageSize = 200
	maxPages        = 10_000
	maxResponseSize = 16 << 20
)

//go:embed manifest.json
var manifestJSON []byte

type Collector struct {
	client *http.Client
}

type configuration struct {
	APIURL           string  `json:"api_url"`
	APIKeyRef        string  `json:"api_key_ref"`
	SiteRef          string  `json:"site_ref"`
	SiteNameOverride string  `json:"site_name_override"`
	SiteSlugOverride string  `json:"site_slug_override"`
	VerifyTLS        *bool   `json:"verify_tls"`
	PageSize         int     `json:"page_size"`
	TimeoutSeconds   float64 `json:"timeout_seconds"`
}

type applicationInfo struct {
	ApplicationVersion string `json:"applicationVersion"`
}

type site struct {
	ID                string `json:"id"`
	InternalReference string `json:"internalReference"`
	Name              string `json:"name"`
}

type deviceOverview struct {
	ID                string   `json:"id"`
	MACAddress        string   `json:"macAddress"`
	IPAddress         string   `json:"ipAddress"`
	Name              string   `json:"name"`
	Model             string   `json:"model"`
	State             string   `json:"state"`
	Supported         bool     `json:"supported"`
	FirmwareVersion   string   `json:"firmwareVersion"`
	FirmwareUpdatable bool     `json:"firmwareUpdatable"`
	Features          []string `json:"features"`
	Interfaces        []string `json:"interfaces"`
}

type deviceDetails struct {
	ID         string           `json:"id"`
	Interfaces deviceInterfaces `json:"interfaces"`
}

type deviceInterfaces struct {
	Ports  []portOverview  `json:"ports"`
	Radios []radioOverview `json:"radios"`
}

type portOverview struct {
	Index        int          `json:"idx"`
	State        string       `json:"state"`
	Connector    string       `json:"connector"`
	MaxSpeedMbps int          `json:"maxSpeedMbps"`
	SpeedMbps    int          `json:"speedMbps"`
	PoE          *poeOverview `json:"poe"`
}

type poeOverview struct {
	Standard string `json:"standard"`
	Type     int    `json:"type"`
	Enabled  bool   `json:"enabled"`
	State    string `json:"state"`
}

type radioOverview struct {
	WLANStandard    string `json:"wlanStandard"`
	FrequencyGHz    scalar `json:"frequencyGHz"`
	ChannelWidthMHz int    `json:"channelWidthMHz"`
	Channel         int    `json:"channel"`
}

type scalar string

func (s *scalar) UnmarshalJSON(value []byte) error {
	var text string
	if err := json.Unmarshal(value, &text); err == nil {
		*s = scalar(text)
		return nil
	}
	var number json.Number
	if err := json.Unmarshal(value, &number); err != nil {
		return errors.New("value must be a string or number")
	}
	*s = scalar(number.String())
	return nil
}

type networkOverview struct {
	Management string `json:"management"`
	ID         string `json:"id"`
	Name       string `json:"name"`
	Enabled    bool   `json:"enabled"`
	VLANID     int    `json:"vlanId"`
}

type networkDetails struct {
	Management        string             `json:"management"`
	ID                string             `json:"id"`
	Name              string             `json:"name"`
	Enabled           bool               `json:"enabled"`
	VLANID            int                `json:"vlanId"`
	IPv4Configuration *ipv4Configuration `json:"ipv4Configuration"`
}

type ipv4Configuration struct {
	HostIPAddress string `json:"hostIpAddress"`
	PrefixLength  int    `json:"prefixLength"`
}

type wifiBroadcast struct {
	Type                  string                `json:"type"`
	ID                    string                `json:"id"`
	Name                  string                `json:"name"`
	Enabled               bool                  `json:"enabled"`
	Network               *wifiNetworkReference `json:"network"`
	SecurityConfiguration wifiSecurity          `json:"securityConfiguration"`
}

type wifiNetworkReference struct {
	Type      string `json:"type"`
	NetworkID string `json:"networkId"`
}

type wifiSecurity struct {
	Type string `json:"type"`
}

type page[T any] struct {
	Offset     int `json:"offset"`
	Limit      int `json:"limit"`
	Count      int `json:"count"`
	TotalCount int `json:"totalCount"`
	Data       []T `json:"data"`
}

type collectError struct {
	code      string
	summary   string
	retryable bool
}

func (e *collectError) Error() string {
	return e.code
}

type collectionState struct {
	request         contracts.CollectionRequest
	observedAt      time.Time
	sites           []site
	devicesBySite   map[string][]deviceOverview
	networksBySite  map[string]map[string]string
	observations    []contracts.Observation
	observationByID map[string]contracts.Observation
	counts          map[string]int
}

func New() *Collector {
	return &Collector{}
}

func NewWithClient(client *http.Client) *Collector {
	return &Collector{client: client}
}

func (c *Collector) Manifest() (contracts.ProviderManifest, error) {
	var manifest contracts.ProviderManifest
	if err := json.Unmarshal(manifestJSON, &manifest); err != nil {
		return contracts.ProviderManifest{}, fmt.Errorf("decode embedded UniFi manifest: %w", err)
	}
	return manifest, nil
}

func (c *Collector) TestConnection(
	ctx context.Context,
	request contracts.ConnectionTestRequest,
	secrets providercontract.SecretResolver,
) contracts.ConnectionTestResult {
	manifest, err := c.Manifest()
	if err != nil || request.ProviderID != manifest.ProviderID || request.ExecutionMode != "agent" {
		return failedConnection("provider_contract", "UniFi collector identity is incompatible.", false)
	}
	config, apiURL, err := parseConfiguration(request.Configuration)
	if err != nil {
		return failedConnection("invalid_configuration", "UniFi source configuration is invalid.", false)
	}
	apiKey, err := secrets.Resolve(ctx, config.APIKeyRef)
	if err != nil || !validAPIKey(apiKey) {
		return failedConnection("secret_unavailable", "UniFi API key reference could not be resolved.", false)
	}
	client := c.httpClient(config)
	var info applicationInfo
	if failure := c.getJSON(ctx, client, endpointURL(apiURL, "info"), apiKey, &info); failure != nil {
		return failedConnection(failure.code, failure.summary, failure.retryable)
	}
	sites, failure := collectPages[site](ctx, c, client, apiURL, apiKey, config.PageSize, "sites")
	if failure != nil {
		return failedConnection(failure.code, failure.summary, failure.retryable)
	}
	selected, err := selectSites(sites, config)
	if err != nil {
		return failedConnection("site_selection", err.Error(), false)
	}
	summary := fmt.Sprintf("Connected to UniFi Network and found %d selected site(s).", len(selected))
	if info.ApplicationVersion != "" {
		summary = fmt.Sprintf("Connected to UniFi Network %s and found %d selected site(s).", info.ApplicationVersion, len(selected))
	}
	return contracts.ConnectionTestResult{Succeeded: true, Summary: summary, Details: []contracts.CollectionMessage{}}
}

func (c *Collector) Collect(
	ctx context.Context,
	request contracts.CollectionRequest,
	secrets providercontract.SecretResolver,
) contracts.ObservationBatch {
	startedAt := time.Now().UTC()
	batch := contracts.ObservationBatch{
		RunID: request.RunID, SourceID: request.SourceID, ProviderID: request.ProviderID,
		ContractVersion: contracts.ContractVersion, State: "failed", StartedAt: startedAt,
		Datasets: append([]string(nil), request.Datasets...), Scope: request.Scope,
		Observations: []contracts.Observation{}, Messages: []contracts.CollectionMessage{},
	}
	manifest, err := c.Manifest()
	if err != nil || request.ProviderID != manifest.ProviderID || request.ExecutionMode != "agent" {
		return finishBatch(batch, manifest.ImplementationVersion, "provider_contract", "Collection request is incompatible with this collector.", false)
	}
	batch.ProviderVersion = manifest.ImplementationVersion
	if len(request.Scope) != 0 {
		return finishBatch(batch, manifest.ImplementationVersion, "unsupported_scope", "Scoped UniFi collection is not implemented; refusing to claim completeness.", false)
	}
	config, apiURL, err := parseConfiguration(request.Configuration)
	if err != nil {
		return finishBatch(batch, manifest.ImplementationVersion, "invalid_configuration", "UniFi source configuration is invalid.", false)
	}
	resolvedDatasets, err := providercontract.ResolveDatasets(manifest, request.Datasets)
	if err != nil || len(resolvedDatasets) == 0 {
		return finishBatch(batch, manifest.ImplementationVersion, "invalid_datasets", "Requested UniFi datasets are invalid.", false)
	}
	batch.Datasets = resolvedDatasets
	apiKey, err := secrets.Resolve(ctx, config.APIKeyRef)
	if err != nil || !validAPIKey(apiKey) {
		return finishBatch(batch, manifest.ImplementationVersion, "secret_unavailable", "UniFi API key reference could not be resolved.", false)
	}
	client := c.httpClient(config)
	sites, failure := collectPages[site](ctx, c, client, apiURL, apiKey, config.PageSize, "sites")
	if failure != nil {
		return finishBatch(batch, manifest.ImplementationVersion, failure.code, failure.summary, failure.retryable)
	}
	selectedSites, err := selectSites(sites, config)
	if err != nil {
		return finishBatch(batch, manifest.ImplementationVersion, "site_selection", err.Error(), false)
	}
	state := &collectionState{
		request: request, observedAt: startedAt, sites: selectedSites,
		devicesBySite: make(map[string][]deviceOverview), networksBySite: make(map[string]map[string]string),
		observations: []contracts.Observation{}, observationByID: make(map[string]contracts.Observation),
		counts: make(map[string]int),
	}
	for _, datasetID := range resolvedDatasets {
		failure = c.collectDataset(ctx, client, apiURL, apiKey, config, datasetID, state)
		if failure != nil {
			batch.Observations = state.observations
			batch.State = "partial"
			if len(batch.Observations) == 0 {
				batch.State = "failed"
			}
			batch.CompletedAt = time.Now().UTC()
			batch.Messages = append(batch.Messages, contracts.CollectionMessage{
				Code:      failure.code,
				Message:   fmt.Sprintf("Dataset %q could not be collected completely: %s", datasetID, failure.summary),
				Retryable: failure.retryable,
			})
			return batch
		}
	}
	batch.Observations = state.observations
	batch.State = "complete"
	batch.CompletedAt = time.Now().UTC()
	batch.CompletenessToken = completenessToken(apiURL, resolvedDatasets, selectedSites, state.counts)
	batch.Messages = append(batch.Messages, contracts.CollectionMessage{
		Code: "collection_complete", Message: "All pages and required details in the selected UniFi datasets were collected.",
	})
	return batch
}

func (c *Collector) collectDataset(
	ctx context.Context,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	config configuration,
	datasetID string,
	state *collectionState,
) *collectError {
	switch datasetID {
	case "unifi_sites":
		return collectSiteObservations(config, state)
	case "unifi_devices":
		return c.collectDeviceObservations(ctx, client, apiURL, apiKey, config, state)
	case "unifi_interfaces":
		return c.collectInterfaceObservations(ctx, client, apiURL, apiKey, state)
	case "unifi_networks":
		return c.collectNetworkObservations(ctx, client, apiURL, apiKey, config, state)
	case "unifi_wireless":
		return c.collectWirelessObservations(ctx, client, apiURL, apiKey, config, state)
	default:
		return &collectError{code: "invalid_datasets", summary: "The requested dataset is not implemented by this collector."}
	}
}

func collectSiteObservations(config configuration, state *collectionState) *collectError {
	for _, sourceSite := range state.sites {
		observation, err := siteObservation(state.request, sourceSite, config, state.observedAt)
		if err != nil {
			return mappingFailure(err)
		}
		if err := state.add(observation); err != nil {
			return mappingFailure(err)
		}
	}
	return nil
}

func (c *Collector) collectDeviceObservations(
	ctx context.Context,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	config configuration,
	state *collectionState,
) *collectError {
	for _, sourceSite := range state.sites {
		devices, failure := collectPages[deviceOverview](
			ctx, c, client, apiURL, apiKey, config.PageSize, "sites", sourceSite.ID, "devices",
		)
		if failure != nil {
			return failure
		}
		state.devicesBySite[sourceSite.ID] = devices
		for _, device := range devices {
			observations, err := deviceObservations(state.request, sourceSite, device, state.observedAt)
			if err != nil {
				return mappingFailure(err)
			}
			for _, observation := range observations {
				if err := state.add(observation); err != nil {
					return mappingFailure(err)
				}
			}
		}
	}
	return nil
}

func (c *Collector) collectInterfaceObservations(
	ctx context.Context,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	state *collectionState,
) *collectError {
	for _, sourceSite := range state.sites {
		for _, device := range state.devicesBySite[sourceSite.ID] {
			var details deviceDetails
			failure := c.getJSON(
				ctx, client, endpointURL(apiURL, "sites", sourceSite.ID, "devices", device.ID), apiKey, &details,
			)
			if failure != nil {
				return failure
			}
			if details.ID != device.ID {
				return &collectError{code: "invalid_response", summary: "A UniFi device detail response changed identity."}
			}
			observations, err := interfaceObservations(state.request, sourceSite, device, details, state.observedAt)
			if err != nil {
				return mappingFailure(err)
			}
			for _, observation := range observations {
				if err := state.add(observation); err != nil {
					return mappingFailure(err)
				}
			}
		}
	}
	return nil
}

func (c *Collector) collectNetworkObservations(
	ctx context.Context,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	config configuration,
	state *collectionState,
) *collectError {
	for _, sourceSite := range state.sites {
		networks, failure := collectPages[networkOverview](
			ctx, c, client, apiURL, apiKey, config.PageSize, "sites", sourceSite.ID, "networks",
		)
		if failure != nil {
			return failure
		}
		state.networksBySite[sourceSite.ID] = make(map[string]string, len(networks))
		for _, network := range networks {
			vlan, err := vlanObservation(state.request, sourceSite, network, state.observedAt)
			if err != nil {
				return mappingFailure(err)
			}
			if err := state.add(vlan); err != nil {
				return mappingFailure(err)
			}
			state.networksBySite[sourceSite.ID][network.ID] = vlan.ExternalID
			var details networkDetails
			failure = c.getJSON(
				ctx, client, endpointURL(apiURL, "sites", sourceSite.ID, "networks", network.ID), apiKey, &details,
			)
			if failure != nil {
				return failure
			}
			if details.ID != network.ID || details.VLANID != network.VLANID || details.Name != network.Name {
				return &collectError{code: "invalid_response", summary: "A UniFi network detail response changed identity."}
			}
			prefix, present, err := prefixObservation(state.request, sourceSite, details, vlan.ExternalID, state.observedAt)
			if err != nil {
				return mappingFailure(err)
			}
			if present {
				if err := state.add(prefix); err != nil {
					return mappingFailure(err)
				}
			}
		}
	}
	return nil
}

func (c *Collector) collectWirelessObservations(
	ctx context.Context,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	config configuration,
	state *collectionState,
) *collectError {
	for _, sourceSite := range state.sites {
		broadcasts, failure := collectPages[wifiBroadcast](
			ctx, c, client, apiURL, apiKey, config.PageSize, "sites", sourceSite.ID, "wifi", "broadcasts",
		)
		if failure != nil {
			return failure
		}
		for _, broadcast := range broadcasts {
			observation, err := wirelessObservation(
				state.request, sourceSite, broadcast, state.networksBySite[sourceSite.ID], state.observedAt,
			)
			if err != nil {
				return mappingFailure(err)
			}
			if err := state.add(observation); err != nil {
				return mappingFailure(err)
			}
		}
	}
	return nil
}

func (state *collectionState) add(observation contracts.Observation) error {
	if existing, found := state.observationByID[observation.ExternalID]; found {
		if existing.ResourceKind != observation.ResourceKind || !reflect.DeepEqual(existing, observation) {
			return fmt.Errorf("source objects resolved to conflicting observation identity %q", observation.ExternalID)
		}
		return nil
	}
	state.observationByID[observation.ExternalID] = observation
	state.observations = append(state.observations, observation)
	state.counts[observation.ResourceKind]++
	return nil
}

func collectPages[T any](
	ctx context.Context,
	collector *Collector,
	client *http.Client,
	apiURL *url.URL,
	apiKey string,
	pageSize int,
	segments ...string,
) ([]T, *collectError) {
	items := make([]T, 0)
	offset := 0
	expectedTotal := -1
	for pageNumber := 0; ; pageNumber++ {
		if pageNumber >= maxPages {
			return items, &collectError{code: "pagination_limit", summary: "UniFi pagination exceeded the safety limit."}
		}
		requestURL := endpointURL(apiURL, segments...)
		query := requestURL.Query()
		query.Set("offset", strconv.Itoa(offset))
		query.Set("limit", strconv.Itoa(pageSize))
		requestURL.RawQuery = query.Encode()
		var current page[T]
		if failure := collector.getJSON(ctx, client, requestURL, apiKey, &current); failure != nil {
			return items, failure
		}
		if current.Offset != offset || current.Count != len(current.Data) || current.TotalCount < 0 ||
			current.Count < 0 || current.Limit < 0 || current.Count > pageSize {
			return items, &collectError{code: "invalid_response", summary: "UniFi returned inconsistent pagination metadata."}
		}
		if expectedTotal == -1 {
			expectedTotal = current.TotalCount
		} else if current.TotalCount != expectedTotal {
			return items, &collectError{code: "collection_changed", summary: "UniFi collection size changed during pagination.", retryable: true}
		}
		items = append(items, current.Data...)
		offset += len(current.Data)
		if offset == expectedTotal {
			return items, nil
		}
		if offset > expectedTotal || len(current.Data) == 0 {
			return items, &collectError{code: "invalid_response", summary: "UniFi pagination ended before the declared collection was complete."}
		}
	}
}

func (c *Collector) getJSON(
	ctx context.Context,
	client *http.Client,
	requestURL *url.URL,
	apiKey string,
	target any,
) *collectError {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL.String(), nil)
	if err != nil {
		return &collectError{code: "request_failed", summary: "UniFi request could not be created."}
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-API-Key", apiKey)
	response, err := client.Do(request)
	if err != nil {
		return classifyRequestError(err)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return &collectError{
			code: "http_status", summary: fmt.Sprintf("UniFi returned HTTP %d.", response.StatusCode),
			retryable: response.StatusCode == http.StatusTooManyRequests || response.StatusCode == http.StatusRequestTimeout || response.StatusCode >= 500,
		}
	}
	limited := io.LimitReader(response.Body, maxResponseSize+1)
	payload, err := io.ReadAll(limited)
	if err != nil {
		return &collectError{code: "invalid_response", summary: "UniFi response could not be read.", retryable: true}
	}
	if len(payload) > maxResponseSize {
		return &collectError{code: "response_too_large", summary: "UniFi response exceeded the safety limit."}
	}
	if err := json.Unmarshal(payload, target); err != nil {
		return &collectError{code: "invalid_response", summary: "UniFi response was not valid JSON."}
	}
	return nil
}

func (c *Collector) httpClient(config configuration) *http.Client {
	if c.client != nil {
		return c.client
	}
	verifyTLS := config.VerifyTLS == nil || *config.VerifyTLS
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: !verifyTLS} // #nosec G402 -- explicit operator setting for private controllers
	return &http.Client{
		Timeout:   time.Duration(config.TimeoutSeconds * float64(time.Second)),
		Transport: transport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return errors.New("redirects are disabled for authenticated UniFi requests")
		},
	}
}

func parseConfiguration(raw map[string]any) (configuration, *url.URL, error) {
	encoded, err := json.Marshal(raw)
	if err != nil {
		return configuration{}, nil, err
	}
	config := configuration{PageSize: defaultPageSize, TimeoutSeconds: 30}
	decoder := json.NewDecoder(strings.NewReader(string(encoded)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return configuration{}, nil, err
	}
	if config.APIURL == "" || config.APIKeyRef == "" || config.SiteRef == "" || config.PageSize < 1 || config.PageSize > 200 ||
		config.TimeoutSeconds < 1 || config.TimeoutSeconds > 120 {
		return configuration{}, nil, errors.New("invalid UniFi configuration")
	}
	if (config.SiteNameOverride != "" || config.SiteSlugOverride != "") && config.SiteRef == "" {
		return configuration{}, nil, errors.New("site overrides require an exact site reference")
	}
	apiURL, err := url.Parse(config.APIURL)
	if err != nil || apiURL.Scheme != "https" || apiURL.Host == "" || apiURL.User != nil ||
		apiURL.RawQuery != "" || apiURL.Fragment != "" {
		return configuration{}, nil, errors.New("invalid UniFi Integration API URL")
	}
	cleanPath := strings.TrimRight(apiURL.Path, "/")
	if strings.HasSuffix(cleanPath, "/v1") {
		return configuration{}, nil, errors.New("UniFi Integration API URL must not include the /v1 suffix")
	}
	apiURL.Path = cleanPath + "/v1/"
	apiURL.RawPath = ""
	return config, apiURL, nil
}

func selectSites(sites []site, config configuration) ([]site, error) {
	selected := make([]site, 0, 1)
	for _, candidate := range sites {
		if candidate.ID == config.SiteRef || candidate.InternalReference == config.SiteRef {
			selected = append(selected, candidate)
		}
	}
	if len(selected) == 0 {
		return nil, errors.New("The configured UniFi site reference was not found.")
	}
	if len(selected) > 1 {
		return nil, errors.New("The configured UniFi site reference is ambiguous.")
	}
	return selected, nil
}

func siteObservation(
	request contracts.CollectionRequest,
	source site,
	config configuration,
	observedAt time.Time,
) (contracts.Observation, error) {
	if source.ID == "" || source.Name == "" || source.InternalReference == "" {
		return contracts.Observation{}, errors.New("UniFi site is missing a stable identity or name")
	}
	name := source.Name
	if config.SiteNameOverride != "" {
		name = config.SiteNameOverride
	}
	slug := slugify(source.InternalReference)
	if config.SiteSlugOverride != "" {
		slug = config.SiteSlugOverride
	}
	if name == "" || len(name) > 100 || slug == "" || len(slug) > 100 {
		return contracts.Observation{}, errors.New("UniFi site name or slug is outside the canonical NetBox boundary")
	}
	return newObservation(
		request, "site", siteExternalID(source.ID), "site", source.ID,
		map[string]any{"/name": name, "/slug": slug, "/status": "active"}, nil, observedAt,
	)
}

func deviceObservations(
	request contracts.CollectionRequest,
	sourceSite site,
	device deviceOverview,
	observedAt time.Time,
) ([]contracts.Observation, error) {
	if device.ID == "" || device.Name == "" || device.Model == "" || len(device.Name) > 64 || len(device.Model) > 100 {
		return nil, errors.New("UniFi device is missing a stable, portable identity")
	}
	manufacturer, err := newObservation(
		request, "manufacturer", "unifi:manufacturer:ubiquiti", "manufacturer", "ubiquiti",
		map[string]any{"/name": "Ubiquiti", "/slug": "ubiquiti"}, nil, observedAt,
	)
	if err != nil {
		return nil, err
	}
	roleName, roleSlug, roleColor := roleForDevice(device)
	roleExternalID := "unifi:device-role:" + roleSlug
	role, err := newObservation(
		request, "device_role", roleExternalID, "device-role", roleSlug,
		map[string]any{"/name": roleName, "/slug": roleSlug, "/color": roleColor, "/vm_role": false}, nil, observedAt,
	)
	if err != nil {
		return nil, err
	}
	deviceTypeExternalID := "unifi:device-type:" + device.Model
	deviceType, err := newObservation(
		request, "device_type", deviceTypeExternalID, "device-model", device.Model,
		map[string]any{"/model": device.Model, "/slug": slugify(device.Model)},
		[]contracts.Relationship{{Kind: "manufacturer", TargetKind: "manufacturer", TargetExternalID: manufacturer.ExternalID}},
		observedAt,
	)
	if err != nil {
		return nil, err
	}
	canonicalDevice, err := newObservation(
		request, "device", deviceExternalID(sourceSite.ID, device.ID), "adopted-device", device.ID,
		map[string]any{"/name": device.Name, "/status": deviceStatus(device.State)},
		[]contracts.Relationship{
			{Kind: "device_type", TargetKind: "device_type", TargetExternalID: deviceTypeExternalID},
			{Kind: "role", TargetKind: "device_role", TargetExternalID: roleExternalID},
			{Kind: "site", TargetKind: "site", TargetExternalID: siteExternalID(sourceSite.ID)},
		},
		observedAt,
	)
	if err != nil {
		return nil, err
	}
	return []contracts.Observation{manufacturer, role, deviceType, canonicalDevice}, nil
}

func interfaceObservations(
	request contracts.CollectionRequest,
	sourceSite site,
	device deviceOverview,
	details deviceDetails,
	observedAt time.Time,
) ([]contracts.Observation, error) {
	deviceExternal := deviceExternalID(sourceSite.ID, device.ID)
	managementExternal := interfaceExternalID(device.ID, "management")
	observations := make([]contracts.Observation, 0, len(details.Interfaces.Ports)+len(details.Interfaces.Radios)+3)
	management, err := newObservation(
		request, "interface", managementExternal, "device-management-interface", device.ID,
		map[string]any{"/name": "mgmt", "/type": "virtual", "/enabled": true, "/mgmt_only": true},
		[]contracts.Relationship{{Kind: "device", TargetKind: "device", TargetExternalID: deviceExternal}}, observedAt,
	)
	if err != nil {
		return nil, err
	}
	observations = append(observations, management)
	seenNames := map[string]bool{"mgmt": true}
	for _, port := range details.Interfaces.Ports {
		if port.Index < 1 || port.Connector == "" || port.MaxSpeedMbps < 1 {
			return nil, errors.New("UniFi device port is missing a stable index or media type")
		}
		name := fmt.Sprintf("Port %d", port.Index)
		if seenNames[name] {
			return nil, errors.New("UniFi device exposes duplicate interface names")
		}
		seenNames[name] = true
		attributes := map[string]any{
			"/name": name, "/type": interfaceType(port.Connector, port.MaxSpeedMbps), "/enabled": true,
		}
		if port.PoE != nil {
			attributes["/poe_mode"] = "pse"
			if poeType := interfacePoEType(port.PoE.Type); poeType != "" {
				attributes["/poe_type"] = poeType
			}
		}
		observation, err := newObservation(
			request, "interface", interfaceExternalID(device.ID, fmt.Sprintf("port:%d", port.Index)),
			"device-port", fmt.Sprintf("%s:%d", device.ID, port.Index), attributes,
			[]contracts.Relationship{{Kind: "device", TargetKind: "device", TargetExternalID: deviceExternal}}, observedAt,
		)
		if err != nil {
			return nil, err
		}
		observations = append(observations, observation)
	}
	for _, radio := range details.Interfaces.Radios {
		frequency := strings.TrimSpace(string(radio.FrequencyGHz))
		if frequency == "" || radio.WLANStandard == "" {
			return nil, errors.New("UniFi radio is missing a stable band or wireless standard")
		}
		name := fmt.Sprintf("Radio %s GHz", frequency)
		if seenNames[name] {
			return nil, errors.New("UniFi device exposes more than one radio in the same band without stable identities")
		}
		seenNames[name] = true
		attributes := map[string]any{
			"/name": name, "/type": radioInterfaceType(radio.WLANStandard), "/enabled": true, "/rf_role": "ap",
		}
		if radio.ChannelWidthMHz > 0 {
			attributes["/rf_channel_width"] = radio.ChannelWidthMHz
		}
		observation, err := newObservation(
			request, "interface", interfaceExternalID(device.ID, "radio:"+frequency),
			"device-radio", device.ID+":"+frequency, attributes,
			[]contracts.Relationship{{Kind: "device", TargetKind: "device", TargetExternalID: deviceExternal}}, observedAt,
		)
		if err != nil {
			return nil, err
		}
		observations = append(observations, observation)
	}
	if device.MACAddress != "" {
		mac, err := normalizeMAC(device.MACAddress)
		if err != nil {
			return nil, err
		}
		observation, err := newObservation(
			request, "mac_address", "unifi:mac-address:"+mac, "device-mac-address", device.ID,
			map[string]any{"/mac_address": mac, "/assigned_object_type": "dcim.interface"},
			[]contracts.Relationship{{Kind: "assigned_interface", TargetKind: "interface", TargetExternalID: managementExternal}}, observedAt,
		)
		if err != nil {
			return nil, err
		}
		observations = append(observations, observation)
	}
	if device.IPAddress != "" {
		address, err := hostAddress(device.IPAddress)
		if err != nil {
			return nil, err
		}
		observation, err := newObservation(
			request, "ip_address", "unifi:ip-address:"+device.ID+":"+address, "device-management-ip", device.ID,
			map[string]any{"/address": address, "/status": "active", "/assigned_object_type": "dcim.interface"},
			[]contracts.Relationship{{Kind: "assigned_interface", TargetKind: "interface", TargetExternalID: managementExternal}}, observedAt,
		)
		if err != nil {
			return nil, err
		}
		observations = append(observations, observation)
	}
	return observations, nil
}

func vlanObservation(
	request contracts.CollectionRequest,
	sourceSite site,
	network networkOverview,
	observedAt time.Time,
) (contracts.Observation, error) {
	if network.ID == "" || network.Name == "" || network.VLANID < 1 || network.VLANID > 4094 {
		return contracts.Observation{}, errors.New("UniFi network is missing a portable VLAN identity")
	}
	return newObservation(
		request, "vlan", networkExternalID(sourceSite.ID, network.ID), "network", network.ID,
		map[string]any{"/vid": network.VLANID, "/name": network.Name},
		[]contracts.Relationship{{Kind: "site", TargetKind: "site", TargetExternalID: siteExternalID(sourceSite.ID)}}, observedAt,
	)
}

func prefixObservation(
	request contracts.CollectionRequest,
	sourceSite site,
	network networkDetails,
	vlanExternalID string,
	observedAt time.Time,
) (contracts.Observation, bool, error) {
	if network.IPv4Configuration == nil || network.IPv4Configuration.HostIPAddress == "" {
		return contracts.Observation{}, false, nil
	}
	address, err := netip.ParseAddr(network.IPv4Configuration.HostIPAddress)
	if err != nil || !address.Is4() || network.IPv4Configuration.PrefixLength < 0 || network.IPv4Configuration.PrefixLength > 32 {
		return contracts.Observation{}, false, errors.New("UniFi network contains an invalid IPv4 prefix")
	}
	prefix := netip.PrefixFrom(address.Unmap(), network.IPv4Configuration.PrefixLength).Masked().String()
	observation, err := newObservation(
		request, "prefix", "unifi:prefix:"+sourceSite.ID+":"+network.ID, "network-ipv4-configuration", network.ID,
		map[string]any{"/prefix": prefix, "/scope_type": "dcim.site"},
		[]contracts.Relationship{
			{Kind: "scope_site", TargetKind: "site", TargetExternalID: siteExternalID(sourceSite.ID)},
			{Kind: "vlan", TargetKind: "vlan", TargetExternalID: vlanExternalID},
		},
		observedAt,
	)
	return observation, true, err
}

func wirelessObservation(
	request contracts.CollectionRequest,
	sourceSite site,
	broadcast wifiBroadcast,
	networks map[string]string,
	observedAt time.Time,
) (contracts.Observation, error) {
	if broadcast.ID == "" || broadcast.Name == "" {
		return contracts.Observation{}, errors.New("UniFi Wi-Fi broadcast is missing a stable identity or SSID")
	}
	authType, err := wirelessAuthType(broadcast.SecurityConfiguration.Type)
	if err != nil {
		return contracts.Observation{}, err
	}
	attributes := map[string]any{
		"/ssid": broadcast.Name, "/status": "disabled", "/auth_type": authType, "/scope_type": "dcim.site",
	}
	if broadcast.Enabled {
		attributes["/status"] = "active"
	}
	relationships := []contracts.Relationship{
		{Kind: "scope_site", TargetKind: "site", TargetExternalID: siteExternalID(sourceSite.ID)},
	}
	if broadcast.Network != nil && broadcast.Network.NetworkID != "" {
		vlanExternalID, found := networks[broadcast.Network.NetworkID]
		if !found {
			return contracts.Observation{}, errors.New("UniFi Wi-Fi broadcast references a network outside the collected site")
		}
		relationships = append(relationships, contracts.Relationship{
			Kind: "vlan", TargetKind: "vlan", TargetExternalID: vlanExternalID,
		})
	}
	return newObservation(
		request, "wireless_lan", "unifi:wireless-lan:"+sourceSite.ID+":"+broadcast.ID,
		"wifi-broadcast", broadcast.ID, attributes, relationships, observedAt,
	)
}

func newObservation(
	request contracts.CollectionRequest,
	resourceKind string,
	externalID string,
	sourceObjectType string,
	sourceObjectID string,
	attributeValues map[string]any,
	relationships []contracts.Relationship,
	observedAt time.Time,
) (contracts.Observation, error) {
	if externalID == "" || sourceObjectID == "" {
		return contracts.Observation{}, errors.New("source object has no stable external identity")
	}
	attributes := make([]contracts.ObservationAttribute, 0, len(attributeValues))
	for path, value := range attributeValues {
		if value == nil || value == "" {
			continue
		}
		attributes = append(attributes, contracts.ObservationAttribute{Path: path, Value: value})
	}
	sort.Slice(attributes, func(i, j int) bool { return attributes[i].Path < attributes[j].Path })
	sort.Slice(relationships, func(i, j int) bool {
		if relationships[i].Kind == relationships[j].Kind {
			return relationships[i].TargetExternalID < relationships[j].TargetExternalID
		}
		return relationships[i].Kind < relationships[j].Kind
	})
	attributePaths := make([]string, len(attributes))
	for index, attribute := range attributes {
		attributePaths[index] = attribute.Path
	}
	portable, err := json.Marshal(struct {
		Attributes    []contracts.ObservationAttribute `json:"attributes"`
		Relationships []contracts.Relationship         `json:"relationships"`
	}{Attributes: attributes, Relationships: relationships})
	if err != nil {
		return contracts.Observation{}, errors.New("portable UniFi projection could not be encoded")
	}
	digest := sha256.Sum256(portable)
	return contracts.Observation{
		ResourceKind: resourceKind, ExternalID: externalID, SourceID: request.SourceID, ProviderID: request.ProviderID,
		Scope: request.Scope, CollectedAt: observedAt, Attributes: attributes, Relationships: relationships,
		Evidence: []contracts.Evidence{{
			SourceObjectType: sourceObjectType, SourceObjectID: sourceObjectID, AttributePaths: attributePaths,
			RawDigest: hex.EncodeToString(digest[:]), Note: "Collected from the official UniFi Network Integration API.",
			ObservedAt: observedAt,
		}},
	}, nil
}

func endpointURL(apiURL *url.URL, segments ...string) *url.URL {
	joined := apiURL.JoinPath(segments...)
	return joined
}

func validAPIKey(value string) bool {
	return value != "" && value == strings.TrimSpace(value) && !strings.ContainsAny(value, "\r\n")
}

func failedConnection(code string, summary string, retryable bool) contracts.ConnectionTestResult {
	return contracts.ConnectionTestResult{
		Succeeded: false, Summary: summary,
		Details: []contracts.CollectionMessage{{Code: code, Message: summary, Retryable: retryable}},
	}
}

func finishBatch(
	batch contracts.ObservationBatch,
	version string,
	code string,
	message string,
	retryable bool,
) contracts.ObservationBatch {
	batch.ProviderVersion = version
	batch.CompletedAt = time.Now().UTC()
	batch.Messages = append(batch.Messages, contracts.CollectionMessage{Code: code, Message: message, Retryable: retryable})
	return batch
}

func mappingFailure(err error) *collectError {
	return &collectError{code: "mapping_error", summary: err.Error()}
}

func classifyRequestError(err error) *collectError {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, syscall.ETIMEDOUT) {
		return &collectError{code: "request_timeout", summary: "UniFi request timed out.", retryable: true}
	}
	if errors.Is(err, context.Canceled) {
		return &collectError{code: "request_cancelled", summary: "UniFi request was cancelled.", retryable: true}
	}
	var networkError net.Error
	if errors.As(err, &networkError) {
		return &collectError{code: "network_error", summary: "UniFi could not be reached.", retryable: true}
	}
	return &collectError{code: "request_failed", summary: "UniFi request failed.", retryable: false}
}

func completenessToken(
	apiURL *url.URL,
	datasets []string,
	sites []site,
	counts map[string]int,
) string {
	siteIDs := make([]string, len(sites))
	for index, sourceSite := range sites {
		siteIDs[index] = sourceSite.ID
	}
	sort.Strings(siteIDs)
	payload := struct {
		Source   string         `json:"source"`
		Datasets []string       `json:"datasets"`
		Sites    []string       `json:"sites"`
		Counts   map[string]int `json:"counts"`
	}{Source: apiURL.Scheme + "://" + apiURL.Host + apiURL.Path, Datasets: datasets, Sites: siteIDs, Counts: counts}
	encoded, _ := json.Marshal(payload)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func roleForDevice(device deviceOverview) (string, string, string) {
	for _, feature := range device.Features {
		if feature == "accessPoint" {
			return "Wireless AP", "wireless-ap", "4caf50"
		}
	}
	return "Network Device", "network-device", "2196f3"
}

func deviceStatus(state string) string {
	switch state {
	case "ONLINE":
		return "active"
	case "OFFLINE", "CONNECTION_INTERRUPTED", "ISOLATED":
		return "offline"
	case "PENDING_ADOPTION", "UPDATING", "GETTING_READY", "ADOPTING":
		return "staged"
	case "DELETING":
		return "decommissioning"
	default:
		return "failed"
	}
}

func interfaceType(connector string, maxSpeedMbps int) string {
	switch connector {
	case "RJ45":
		switch {
		case maxSpeedMbps <= 100:
			return "100base-tx"
		case maxSpeedMbps <= 1000:
			return "1000base-t"
		case maxSpeedMbps <= 2500:
			return "2.5gbase-t"
		case maxSpeedMbps <= 5000:
			return "5gbase-t"
		case maxSpeedMbps <= 10_000:
			return "10gbase-t"
		case maxSpeedMbps <= 25_000:
			return "25gbase-t"
		}
	case "SFP":
		return "1000base-x-sfp"
	case "SFPPLUS":
		return "10gbase-x-sfpp"
	case "SFP28":
		return "25gbase-x-sfp28"
	case "QSFP28":
		return "100gbase-x-qsfp28"
	}
	return "other"
}

func interfacePoEType(value int) string {
	switch value {
	case 1:
		return "type1-ieee802.3af"
	case 2:
		return "type2-ieee802.3at"
	case 3:
		return "type3-ieee802.3bt"
	case 4:
		return "type4-ieee802.3bt"
	default:
		return ""
	}
}

func radioInterfaceType(standard string) string {
	switch standard {
	case "802.11a":
		return "ieee802.11a"
	case "802.11b", "802.11g":
		return "ieee802.11g"
	case "802.11n":
		return "ieee802.11n"
	case "802.11ac":
		return "ieee802.11ac"
	case "802.11ax":
		return "ieee802.11ax"
	case "802.11be":
		return "ieee802.11be"
	default:
		return "other-wireless"
	}
}

func wirelessAuthType(value string) (string, error) {
	switch value {
	case "OPEN":
		return "open", nil
	case "WEP":
		return "wep", nil
	case "WPA_PERSONAL", "WPA2_PERSONAL", "WPA3_PERSONAL", "WPA2_WPA3_PERSONAL":
		return "wpa-personal", nil
	case "WPA_ENTERPRISE", "WPA2_ENTERPRISE", "WPA3_ENTERPRISE", "WPA2_WPA3_ENTERPRISE":
		return "wpa-enterprise", nil
	default:
		return "", errors.New("UniFi Wi-Fi broadcast uses an unsupported security family")
	}
}

func normalizeMAC(value string) (string, error) {
	hardware, err := net.ParseMAC(value)
	if err != nil || len(hardware) != 6 {
		return "", errors.New("UniFi device contains an invalid MAC address")
	}
	return strings.ToLower(hardware.String()), nil
}

func hostAddress(value string) (string, error) {
	address, err := netip.ParseAddr(value)
	if err != nil {
		return "", errors.New("UniFi device contains an invalid management IP address")
	}
	address = address.Unmap()
	bits := 128
	if address.Is4() {
		bits = 32
	}
	return fmt.Sprintf("%s/%d", address, bits), nil
}

func slugify(value string) string {
	var result strings.Builder
	separator := false
	for _, character := range strings.ToLower(strings.TrimSpace(value)) {
		if unicode.IsLetter(character) || unicode.IsDigit(character) {
			if separator && result.Len() > 0 {
				result.WriteByte('-')
			}
			result.WriteRune(character)
			separator = false
		} else {
			separator = true
		}
	}
	return strings.Trim(result.String(), "-")
}

func siteExternalID(siteID string) string {
	return "unifi:site:" + siteID
}

func deviceExternalID(siteID string, deviceID string) string {
	return "unifi:device:" + siteID + ":" + deviceID
}

func interfaceExternalID(deviceID string, suffix string) string {
	return "unifi:interface:" + deviceID + ":" + suffix
}

func networkExternalID(siteID string, networkID string) string {
	return "unifi:vlan:" + siteID + ":" + networkID
}
