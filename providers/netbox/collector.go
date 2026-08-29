package netboxprovider

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
)

const (
	defaultPageSize = 500
	maxPages        = 10_000
	maxResponseSize = 32 << 20
)

//go:embed manifest.json
var manifestJSON []byte

type Collector struct {
	client *http.Client
}

type configuration struct {
	BaseURL        string  `json:"base_url"`
	TokenRef       string  `json:"token_ref"`
	VerifyTLS      *bool   `json:"verify_tls"`
	PageSize       int     `json:"page_size"`
	TimeoutSeconds float64 `json:"timeout_seconds"`
}

type endpoint struct {
	Path string
	Kind string
}

var datasetEndpoints = map[string][]endpoint{
	"references": {
		{Path: "extras/tags/", Kind: "tag"},
		{Path: "users/owner-groups/", Kind: "owner_group"},
		{Path: "users/owners/", Kind: "owner"},
		{Path: "tenancy/tenant-groups/", Kind: "tenant_group"},
		{Path: "tenancy/tenants/", Kind: "tenant"},
		{Path: "dcim/site-groups/", Kind: "site_group"},
		{Path: "ipam/rirs/", Kind: "rir"},
		{Path: "ipam/asns/", Kind: "asn"},
	},
	"users": {
		{Path: "users/permissions/", Kind: "object_permission"},
		{Path: "users/groups/", Kind: "user_group"},
		{Path: "users/users/", Kind: "user"},
	},
	"regions":   {{Path: "dcim/regions/", Kind: "region"}},
	"sites":     {{Path: "dcim/sites/", Kind: "site"}},
	"locations": {{Path: "dcim/locations/", Kind: "location"}},
	"device_catalog": {
		{Path: "dcim/manufacturers/", Kind: "manufacturer"},
		{Path: "dcim/device-roles/", Kind: "device_role"},
		{Path: "dcim/platforms/", Kind: "platform"},
		{Path: "dcim/device-types/", Kind: "device_type"},
	},
	"racks": {
		{Path: "dcim/rack-groups/", Kind: "rack_group"},
		{Path: "dcim/rack-roles/", Kind: "rack_role"},
		{Path: "dcim/rack-types/", Kind: "rack_type"},
		{Path: "dcim/racks/", Kind: "rack"},
	},
	"module_catalog": {
		{Path: "dcim/module-type-profiles/", Kind: "module_type_profile"},
		{Path: "dcim/module-types/", Kind: "module_type"},
		{Path: "dcim/inventory-item-roles/", Kind: "inventory_item_role"},
	},
	"component_templates": {
		{Path: "dcim/console-port-templates/", Kind: "console_port_template"},
		{Path: "dcim/console-server-port-templates/", Kind: "console_server_port_template"},
		{Path: "dcim/power-port-templates/", Kind: "power_port_template"},
		{Path: "dcim/power-outlet-templates/", Kind: "power_outlet_template"},
		{Path: "dcim/interface-templates/", Kind: "interface_template"},
		{Path: "dcim/rear-port-templates/", Kind: "rear_port_template"},
		{Path: "dcim/front-port-templates/", Kind: "front_port_template"},
		{Path: "dcim/module-bay-templates/", Kind: "module_bay_template"},
		{Path: "dcim/device-bay-templates/", Kind: "device_bay_template"},
		{Path: "dcim/inventory-item-templates/", Kind: "inventory_item_template"},
	},
	"devices": {
		{Path: "dcim/virtual-chassis/", Kind: "virtual_chassis"},
		{Path: "dcim/devices/", Kind: "device"},
	},
	"device_components": {
		{Path: "dcim/virtual-device-contexts/", Kind: "virtual_device_context"},
		{Path: "dcim/module-bays/", Kind: "module_bay"},
		{Path: "dcim/device-bays/", Kind: "device_bay"},
		{Path: "dcim/modules/", Kind: "module"},
		{Path: "dcim/console-ports/", Kind: "console_port"},
		{Path: "dcim/console-server-ports/", Kind: "console_server_port"},
		{Path: "dcim/power-ports/", Kind: "power_port"},
		{Path: "dcim/power-outlets/", Kind: "power_outlet"},
		{Path: "dcim/interfaces/", Kind: "interface"},
		{Path: "dcim/rear-ports/", Kind: "rear_port"},
		{Path: "dcim/front-ports/", Kind: "front_port"},
		{Path: "dcim/inventory-items/", Kind: "inventory_item"},
		{Path: "dcim/mac-addresses/", Kind: "mac_address"},
	},
	"rack_reservations": {{Path: "dcim/rack-reservations/", Kind: "rack_reservation"}},
	"power": {
		{Path: "dcim/power-panels/", Kind: "power_panel"},
		{Path: "dcim/power-feeds/", Kind: "power_feed"},
	},
	"circuit_catalog": {
		{Path: "circuits/providers/", Kind: "provider"},
		{Path: "circuits/provider-accounts/", Kind: "provider_account"},
		{Path: "circuits/provider-networks/", Kind: "provider_network"},
		{Path: "circuits/circuit-types/", Kind: "circuit_type"},
		{Path: "circuits/virtual-circuit-types/", Kind: "virtual_circuit_type"},
		{Path: "circuits/circuit-groups/", Kind: "circuit_group"},
	},
	"circuits": {
		{Path: "circuits/circuits/", Kind: "circuit"},
		{Path: "circuits/circuit-terminations/", Kind: "circuit_termination"},
	},
	"virtual_circuits": {
		{Path: "circuits/virtual-circuits/", Kind: "virtual_circuit"},
		{Path: "circuits/virtual-circuit-terminations/", Kind: "virtual_circuit_termination"},
	},
	"circuit_group_assignments": {
		{Path: "circuits/circuit-group-assignments/", Kind: "circuit_group_assignment"},
	},
	"cabling": {
		{Path: "dcim/cable-bundles/", Kind: "cable_bundle"},
		{Path: "dcim/cables/", Kind: "cable"},
	},
}

var errInvalidTokenFormat = errors.New("invalid NetBox token format")

type page struct {
	Next    *string          `json:"next"`
	Results []map[string]any `json:"results"`
}

type collectError struct {
	code      string
	retryable bool
}

func (e *collectError) Error() string {
	return e.code
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
		return contracts.ProviderManifest{}, fmt.Errorf("decode embedded NetBox manifest: %w", err)
	}
	return manifest, nil
}

func (c *Collector) TestConnection(
	ctx context.Context,
	request contracts.ConnectionTestRequest,
	secrets providercontract.SecretResolver,
) contracts.ConnectionTestResult {
	manifest, err := c.Manifest()
	if err != nil || request.ProviderID != manifest.ProviderID {
		return failedConnection("provider_contract", "NetBox collector identity is incompatible.", false)
	}
	config, apiURL, err := parseConfiguration(request.Configuration)
	if err != nil {
		return failedConnection("invalid_configuration", "NetBox source configuration is invalid.", false)
	}
	token, err := secrets.Resolve(ctx, config.TokenRef)
	if err != nil {
		return failedConnection("secret_unavailable", "NetBox API token reference could not be resolved.", false)
	}

	statusURL := apiURL.ResolveReference(&url.URL{Path: "status/"})
	response, err := c.doGET(ctx, c.httpClient(config), statusURL, token)
	if err != nil {
		failure := classifyRequestError(err)
		return failedConnection(failure.code, failure.summary, failure.retryable)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return failedConnection("http_status", fmt.Sprintf("NetBox returned HTTP %d.", response.StatusCode), response.StatusCode >= 500)
	}

	var status struct {
		Version string `json:"netbox-version"`
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	if err := decoder.Decode(&status); err != nil {
		return failedConnection("invalid_response", "NetBox status response was not valid JSON.", false)
	}
	summary := "Connected to NetBox."
	if status.Version != "" {
		summary = fmt.Sprintf("Connected to NetBox %s.", status.Version)
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
		RunID:           request.RunID,
		SourceID:        request.SourceID,
		ProviderID:      request.ProviderID,
		ContractVersion: contracts.ContractVersion,
		State:           "failed",
		StartedAt:       startedAt,
		Datasets:        append([]string(nil), request.Datasets...),
		Scope:           request.Scope,
		Observations:    []contracts.Observation{},
		Messages:        []contracts.CollectionMessage{},
	}

	manifest, err := c.Manifest()
	if err != nil || request.ProviderID != manifest.ProviderID || request.ExecutionMode != "agent" {
		return finishBatch(batch, manifest.ImplementationVersion, "provider_contract", "Collection request is incompatible with this collector.", false)
	}
	batch.ProviderVersion = manifest.ImplementationVersion
	if len(request.Scope) != 0 {
		return finishBatch(batch, manifest.ImplementationVersion, "unsupported_scope", "Scoped NetBox collection is not implemented yet; refusing to claim completeness.", false)
	}

	config, apiURL, err := parseConfiguration(request.Configuration)
	if err != nil {
		return finishBatch(batch, manifest.ImplementationVersion, "invalid_configuration", "NetBox source configuration is invalid.", false)
	}
	resolvedDatasets, err := providercontract.ResolveDatasets(manifest, request.Datasets)
	if err != nil || len(resolvedDatasets) == 0 {
		return finishBatch(batch, manifest.ImplementationVersion, "invalid_datasets", "Requested NetBox datasets are invalid.", false)
	}
	batch.Datasets = resolvedDatasets
	token, err := secrets.Resolve(ctx, config.TokenRef)
	if err != nil {
		return finishBatch(batch, manifest.ImplementationVersion, "secret_unavailable", "NetBox API token reference could not be resolved.", false)
	}

	counts := make(map[string]int)
	client := c.httpClient(config)
	for _, datasetID := range resolvedDatasets {
		for _, endpoint := range datasetEndpoints[datasetID] {
			observations, collectErr := c.collectEndpoint(ctx, request, config, client, apiURL, token, endpoint)
			batch.Observations = append(batch.Observations, observations...)
			counts[endpoint.Kind] += len(observations)
			if collectErr != nil {
				state := "partial"
				if len(batch.Observations) == 0 {
					state = "failed"
				}
				batch.State = state
				batch.Messages = append(batch.Messages, contracts.CollectionMessage{
					Code:      collectErr.code,
					Message:   "NetBox collection stopped before the declared dataset scope was complete.",
					Retryable: collectErr.retryable,
				})
				batch.CompletedAt = time.Now().UTC()
				return batch
			}
		}
	}

	batch.State = "complete"
	batch.CompletedAt = time.Now().UTC()
	batch.CompletenessToken = completenessToken(apiURL, resolvedDatasets, request.Scope, counts)
	batch.Messages = append(batch.Messages, contracts.CollectionMessage{
		Code:    "collection_complete",
		Message: "All pages in the selected NetBox datasets were collected.",
	})
	return batch
}

func (c *Collector) collectEndpoint(
	ctx context.Context,
	request contracts.CollectionRequest,
	config configuration,
	client *http.Client,
	apiURL *url.URL,
	token string,
	endpoint endpoint,
) ([]contracts.Observation, *collectError) {
	endpointURL := apiURL.ResolveReference(&url.URL{Path: endpoint.Path})
	query := endpointURL.Query()
	query.Set("limit", strconv.Itoa(config.PageSize))
	endpointURL.RawQuery = query.Encode()
	observations := make([]contracts.Observation, 0)

	for pageNumber := 0; endpointURL != nil; pageNumber++ {
		if pageNumber >= maxPages {
			return observations, &collectError{code: "pagination_limit", retryable: false}
		}
		response, err := c.doGET(ctx, client, endpointURL, token)
		if err != nil {
			failure := classifyRequestError(err)
			return observations, &collectError{code: failure.code, retryable: failure.retryable}
		}
		if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
			response.Body.Close()
			return observations, &collectError{code: "http_status", retryable: response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500}
		}

		var payload page
		decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
		decoder.UseNumber()
		decodeErr := decoder.Decode(&payload)
		response.Body.Close()
		if decodeErr != nil {
			return observations, &collectError{code: "invalid_response", retryable: false}
		}
		for _, record := range payload.Results {
			observation, err := mapObservation(request, endpoint.Kind, record)
			if err != nil {
				return observations, &collectError{code: "invalid_record", retryable: false}
			}
			observations = append(observations, observation)
		}

		if payload.Next == nil || *payload.Next == "" {
			endpointURL = nil
			continue
		}
		nextURL, err := url.Parse(*payload.Next)
		if err != nil || !sameAPIOrigin(apiURL, nextURL) {
			return observations, &collectError{code: "unsafe_pagination_url", retryable: false}
		}
		endpointURL = nextURL
	}
	return observations, nil
}

func (c *Collector) doGET(ctx context.Context, client *http.Client, endpointURL *url.URL, token string) (*http.Response, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpointURL.String(), nil)
	if err != nil {
		return nil, err
	}
	authorization, err := authorizationHeader(token)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", authorization)
	request.Header.Set("User-Agent", "netbox-ssot-agent/0.1")
	return client.Do(request)
}

func authorizationHeader(token string) (string, error) {
	if token == "" || strings.ContainsAny(token, " \t\r\n") {
		return "", errInvalidTokenFormat
	}
	if strings.HasPrefix(token, "nbt_") {
		key, secret, found := strings.Cut(strings.TrimPrefix(token, "nbt_"), ".")
		if !found || key == "" || secret == "" {
			return "", errInvalidTokenFormat
		}
		return "Bearer " + token, nil
	}
	return "Token " + token, nil
}

type requestFailure struct {
	code      string
	summary   string
	retryable bool
}

func classifyRequestError(err error) requestFailure {
	if errors.Is(err, errInvalidTokenFormat) {
		return requestFailure{code: "invalid_token_format", summary: "NetBox API token format is invalid.", retryable: false}
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return requestFailure{code: "request_timeout", summary: "NetBox status request timed out.", retryable: true}
	}

	var dnsError *net.DNSError
	if errors.As(err, &dnsError) {
		return requestFailure{code: "dns_resolution_failed", summary: "NetBox hostname could not be resolved.", retryable: true}
	}
	var unknownAuthority x509.UnknownAuthorityError
	var hostnameError x509.HostnameError
	var certificateError x509.CertificateInvalidError
	if errors.As(err, &unknownAuthority) || errors.As(err, &hostnameError) || errors.As(err, &certificateError) {
		return requestFailure{code: "tls_validation_failed", summary: "NetBox TLS certificate validation failed.", retryable: false}
	}
	if errors.Is(err, syscall.ECONNREFUSED) {
		return requestFailure{code: "connection_refused", summary: "NetBox refused the network connection.", retryable: true}
	}
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() {
		return requestFailure{code: "request_timeout", summary: "NetBox status request timed out.", retryable: true}
	}
	return requestFailure{code: "connection_failed", summary: "NetBox status endpoint could not be read.", retryable: true}
}

func (c *Collector) httpClient(config configuration) *http.Client {
	if c.client != nil {
		client := *c.client
		client.CheckRedirect = rejectRedirect
		return &client
	}
	verifyTLS := config.VerifyTLS == nil || *config.VerifyTLS
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: !verifyTLS} //nolint:gosec
	return &http.Client{
		Transport:     transport,
		Timeout:       time.Duration(config.TimeoutSeconds * float64(time.Second)),
		CheckRedirect: rejectRedirect,
	}
}

func rejectRedirect(_ *http.Request, _ []*http.Request) error {
	return http.ErrUseLastResponse
}

func parseConfiguration(raw map[string]any) (configuration, *url.URL, error) {
	encoded, err := json.Marshal(raw)
	if err != nil {
		return configuration{}, nil, err
	}
	config := configuration{PageSize: defaultPageSize, TimeoutSeconds: 30}
	if err := json.Unmarshal(encoded, &config); err != nil {
		return configuration{}, nil, err
	}
	if config.BaseURL == "" || config.TokenRef == "" || config.PageSize < 1 || config.TimeoutSeconds < 1 || config.TimeoutSeconds > 120 {
		return configuration{}, nil, errors.New("invalid NetBox configuration")
	}
	baseURL, err := url.Parse(config.BaseURL)
	if err != nil || (baseURL.Scheme != "https" && baseURL.Scheme != "http") || baseURL.Host == "" || baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return configuration{}, nil, errors.New("invalid NetBox base URL")
	}
	baseURL.Path = strings.TrimRight(baseURL.Path, "/") + "/api/"
	baseURL.RawPath = ""
	return config, baseURL, nil
}

func failedConnection(code string, summary string, retryable bool) contracts.ConnectionTestResult {
	return contracts.ConnectionTestResult{
		Succeeded: false,
		Summary:   summary,
		Details:   []contracts.CollectionMessage{{Code: code, Message: summary, Retryable: retryable}},
	}
}

func finishBatch(batch contracts.ObservationBatch, version string, code string, message string, retryable bool) contracts.ObservationBatch {
	batch.ProviderVersion = version
	batch.CompletedAt = time.Now().UTC()
	batch.Messages = append(batch.Messages, contracts.CollectionMessage{Code: code, Message: message, Retryable: retryable})
	return batch
}

func sameAPIOrigin(apiURL *url.URL, candidate *url.URL) bool {
	return strings.EqualFold(apiURL.Scheme, candidate.Scheme) && strings.EqualFold(apiURL.Host, candidate.Host) && strings.HasPrefix(candidate.EscapedPath(), apiURL.EscapedPath())
}

func completenessToken(apiURL *url.URL, datasets []string, scope []contracts.ScopeDimension, counts map[string]int) string {
	payload := struct {
		Source   string                     `json:"source"`
		Datasets []string                   `json:"datasets"`
		Scope    []contracts.ScopeDimension `json:"scope"`
		Counts   map[string]int             `json:"counts"`
	}{Source: apiURL.Scheme + "://" + apiURL.Host + apiURL.Path, Datasets: datasets, Scope: scope, Counts: counts}
	encoded, _ := json.Marshal(payload)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func mapObservation(request contracts.CollectionRequest, kind string, record map[string]any) (contracts.Observation, error) {
	id, ok := objectID(record["id"])
	if !ok {
		return contracts.Observation{}, errors.New("record has no stable ID")
	}
	collectedAt := time.Now().UTC()
	attributes := attributesFor(kind, record)
	sort.Slice(attributes, func(i, j int) bool { return attributes[i].Path < attributes[j].Path })
	relationships := relationshipsFor(kind, record)
	sort.Slice(relationships, func(i, j int) bool {
		if relationships[i].Kind == relationships[j].Kind {
			return relationships[i].TargetExternalID < relationships[j].TargetExternalID
		}
		return relationships[i].Kind < relationships[j].Kind
	})
	attributePaths := make([]string, 0, len(attributes))
	for _, attribute := range attributes {
		attributePaths = append(attributePaths, attribute.Path)
	}
	raw, err := json.Marshal(record)
	if err != nil {
		return contracts.Observation{}, errors.New("record is not serializable")
	}
	digest := sha256.Sum256(raw)
	return contracts.Observation{
		ResourceKind:  kind,
		ExternalID:    externalID(kind, id),
		SourceID:      request.SourceID,
		ProviderID:    request.ProviderID,
		Scope:         request.Scope,
		CollectedAt:   collectedAt,
		Attributes:    attributes,
		Relationships: relationships,
		Evidence: []contracts.Evidence{{
			SourceObjectType: kind,
			SourceObjectID:   id,
			AttributePaths:   attributePaths,
			RawDigest:        hex.EncodeToString(digest[:]),
			Note:             "Collected from the NetBox REST API.",
			ObservedAt:       collectedAt,
		}},
	}, nil
}

func attributesFor(kind string, record map[string]any) []contracts.ObservationAttribute {
	attributes := make([]contracts.ObservationAttribute, 0, 24)
	add := func(path string, value any) {
		if value != nil && value != "" {
			attributes = append(attributes, contracts.ObservationAttribute{Path: path, Value: value})
		}
	}
	addDirect := func(path string, key string) { add(path, record[key]) }
	addChoice := func(path string, key string) { add(path, choiceValue(record[key])) }
	addDecimal := func(path string, key string) { add(path, decimalValue(record[key])) }
	addFields := func(keys ...string) {
		for _, key := range keys {
			addDirect("/"+key, key)
		}
	}
	addChoices := func(keys ...string) {
		for _, key := range keys {
			addChoice("/"+key, key)
		}
	}
	addDecimals := func(keys ...string) {
		for _, key := range keys {
			addDecimal("/"+key, key)
		}
	}

	switch kind {
	case "tag":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/color", "color")
		addDirect("/description", "description")
		addDirect("/weight", "weight")
		if values := stringValues(record["object_types"]); len(values) > 0 {
			add("/object_types", values)
		}
	case "owner_group":
		addDirect("/name", "name")
		addDirect("/description", "description")
	case "owner":
		addDirect("/name", "name")
		addDirect("/description", "description")
	case "object_permission":
		addFields("name", "description", "enabled", "constraints")
		add("/actions", stringValues(record["actions"]))
		add("/object_types", stringValues(record["object_types"]))
	case "user_group":
		addFields("name", "description")
	case "user":
		addFields("username", "first_name", "last_name", "email", "is_active")
	case "tenant_group", "site_group":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "tenant":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "rir":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/is_private", "is_private")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "asn":
		addDirect("/asn", "asn")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
		add("/role", nestedValue(record["role"], "slug"))
	case "region":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "site":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addChoice("/status", "status")
		addDirect("/facility", "facility")
		addDirect("/time_zone", "time_zone")
		addDirect("/description", "description")
		addDirect("/physical_address", "physical_address")
		addDirect("/shipping_address", "shipping_address")
		addDecimal("/latitude", "latitude")
		addDecimal("/longitude", "longitude")
		addDirect("/comments", "comments")
	case "location":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addChoice("/status", "status")
		addDirect("/facility", "facility")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "manufacturer", "rack_group":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "rack_role":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/color", "color")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "device_role":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addDirect("/color", "color")
		addDirect("/vm_role", "vm_role")
		add("/config_template", nestedValue(record["config_template"], "name"))
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "platform":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		add("/config_template", nestedValue(record["config_template"], "name"))
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "device_type":
		addDirect("/model", "model")
		addDirect("/slug", "slug")
		addDirect("/part_number", "part_number")
		addDecimal("/u_height", "u_height")
		addDirect("/exclude_from_utilization", "exclude_from_utilization")
		addDirect("/is_full_depth", "is_full_depth")
		addChoice("/subdevice_role", "subdevice_role")
		addChoice("/airflow", "airflow")
		addDecimal("/weight", "weight")
		addChoice("/weight_unit", "weight_unit")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "rack_type":
		addDirect("/model", "model")
		addDirect("/slug", "slug")
		addChoice("/form_factor", "form_factor")
		addChoice("/width", "width")
		addDirect("/u_height", "u_height")
		addDirect("/starting_unit", "starting_unit")
		addDirect("/desc_units", "desc_units")
		addDirect("/outer_width", "outer_width")
		addDirect("/outer_height", "outer_height")
		addDirect("/outer_depth", "outer_depth")
		addChoice("/outer_unit", "outer_unit")
		addDecimal("/weight", "weight")
		addDirect("/max_weight", "max_weight")
		addChoice("/weight_unit", "weight_unit")
		addDirect("/mounting_depth", "mounting_depth")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "rack":
		addDirect("/name", "name")
		addDirect("/facility_id", "facility_id")
		addChoice("/status", "status")
		addDirect("/serial", "serial")
		addDirect("/asset_tag", "asset_tag")
		addChoice("/form_factor", "form_factor")
		addChoice("/width", "width")
		addDirect("/u_height", "u_height")
		addDirect("/starting_unit", "starting_unit")
		addDirect("/desc_units", "desc_units")
		addDirect("/outer_width", "outer_width")
		addDirect("/outer_height", "outer_height")
		addDirect("/outer_depth", "outer_depth")
		addChoice("/outer_unit", "outer_unit")
		addDirect("/mounting_depth", "mounting_depth")
		addChoice("/airflow", "airflow")
		addDecimal("/weight", "weight")
		addDirect("/max_weight", "max_weight")
		addChoice("/weight_unit", "weight_unit")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
	case "module_type_profile":
		addFields("name", "description", "schema", "comments")
	case "module_type":
		addFields("model", "part_number", "attributes", "description", "comments")
		addChoices("airflow", "weight_unit")
		addDecimals("weight")
	case "inventory_item_role":
		addFields("name", "slug", "color", "description", "comments")
	case "console_port_template", "console_server_port_template":
		addFields("name", "label", "description")
		addChoices("type")
	case "power_port_template":
		addFields("name", "label", "maximum_draw", "allocated_draw", "description")
		addChoices("type")
	case "power_outlet_template":
		addFields("name", "label", "color", "description")
		addChoices("type", "feed_leg")
	case "interface_template":
		addFields("name", "label", "enabled", "mgmt_only", "description")
		addChoices("type", "poe_mode", "poe_type", "rf_role")
	case "front_port_template", "rear_port_template":
		addFields("name", "label", "color", "positions", "description")
		addChoices("type")
	case "module_bay_template":
		addFields("name", "label", "position", "enabled", "description")
	case "device_bay_template":
		addFields("name", "label", "enabled", "description")
	case "inventory_item_template":
		addFields("name", "label", "part_id", "description")
		add("/component_type", contentTypeValue(record["component_type"]))
	case "virtual_chassis":
		addFields("name", "domain", "description", "comments")
	case "device":
		addFields(
			"name", "serial", "asset_tag", "vc_position", "vc_priority", "description", "comments",
			"local_context_data",
		)
		addChoices("face", "status", "airflow")
		addDecimals("position", "latitude", "longitude")
		add("/config_template", nestedValue(record["config_template"], "name"))
	case "virtual_device_context":
		addFields("name", "identifier", "description", "comments")
		addChoices("status")
	case "module":
		addFields("serial", "asset_tag", "description", "comments")
		addChoices("status")
	case "console_port", "console_server_port":
		addFields("name", "label", "description", "mark_connected")
		addChoices("type", "speed")
	case "power_port":
		addFields("name", "label", "maximum_draw", "allocated_draw", "description", "mark_connected")
		addChoices("type")
	case "power_outlet":
		addFields("name", "label", "color", "description", "mark_connected")
		addChoices("type", "status", "feed_leg")
	case "interface":
		addFields(
			"name", "label", "enabled", "mtu", "speed", "wwn", "mgmt_only", "description", "mark_connected",
			"rf_channel_frequency", "rf_channel_width", "tx_power",
		)
		addChoices("type", "duplex", "mode", "rf_role", "rf_channel", "poe_mode", "poe_type")
	case "front_port", "rear_port":
		addFields("name", "label", "color", "positions", "description", "mark_connected")
		addChoices("type")
	case "module_bay":
		addFields("name", "label", "position", "enabled", "description")
	case "device_bay":
		addFields("name", "label", "enabled", "description")
	case "inventory_item":
		addFields(
			"name", "label", "part_id", "serial", "asset_tag", "discovered", "description",
		)
		addChoices("status")
		add("/component_type", contentTypeValue(record["component_type"]))
	case "mac_address":
		addFields("mac_address", "description", "comments")
		add("/assigned_object_type", contentTypeValue(record["assigned_object_type"]))
	case "rack_reservation":
		addFields("units", "description", "comments")
		addChoices("status")
		add("/user", nestedValue(record["user"], "username"))
	case "power_panel":
		addFields("name", "description", "comments")
	case "power_feed":
		addFields("name", "voltage", "amperage", "max_utilization", "mark_connected", "description", "comments")
		addChoices("status", "type", "supply", "phase")
	case "cable_bundle":
		addFields("name", "description", "comments")
	case "cable":
		addFields("label", "color", "description", "comments")
		addChoices("type", "status", "profile", "length_unit")
		addDecimals("length")
		if values := unsupportedTerminationTypes(record); len(values) > 0 {
			add("/unsupported_termination_types", values)
		}
	case "provider":
		addFields("name", "slug", "description", "comments")
	case "provider_account":
		addFields("account", "name", "description", "comments")
	case "provider_network":
		addFields("name", "service_id", "description", "comments")
	case "circuit_type", "virtual_circuit_type":
		addFields("name", "slug", "color", "description", "comments")
	case "circuit_group":
		addFields("name", "slug", "description", "comments")
	case "circuit":
		addFields("cid", "install_date", "termination_date", "commit_rate", "description", "comments")
		addChoices("status", "distance_unit")
		addDecimals("distance")
	case "circuit_termination":
		addFields("term_side", "port_speed", "upstream_speed", "xconnect_id", "pp_info", "description", "mark_connected")
	case "virtual_circuit":
		addFields("cid", "description", "comments")
		addChoices("status")
	case "virtual_circuit_termination":
		addFields("description")
		addChoices("role")
	case "circuit_group_assignment":
		addChoices("priority")
	}
	return attributes
}

func relationshipsFor(kind string, record map[string]any) []contracts.Relationship {
	relationships := make([]contracts.Relationship, 0, 6)
	add := func(relationshipKind string, targetKind string, value any) {
		if id, ok := nestedID(value); ok {
			relationships = append(relationships, contracts.Relationship{
				Kind:             relationshipKind,
				TargetKind:       targetKind,
				TargetExternalID: externalID(targetKind, id),
			})
		}
	}
	addMany := func(relationshipKind string, targetKind string, value any) {
		items, ok := value.([]any)
		if !ok {
			return
		}
		for _, item := range items {
			add(relationshipKind, targetKind, item)
		}
	}
	addID := func(relationshipKind string, targetKind string, value any) {
		if id, ok := objectID(value); ok {
			relationships = append(relationships, contracts.Relationship{
				Kind:             relationshipKind,
				TargetKind:       targetKind,
				TargetExternalID: externalID(targetKind, id),
			})
		}
	}
	addGeneric := func(prefix string, objectType any, objectIDValue any) {
		objectTypeName, ok := contentTypeValue(objectType).(string)
		if !ok {
			return
		}
		targetKind, ok := resourceKindForObjectType(objectTypeName)
		if !ok {
			return
		}
		addID(prefix+"_"+targetKind, targetKind, objectIDValue)
	}
	addMappings := func(value any, targetField string, targetKind string) {
		items, ok := value.([]any)
		if !ok {
			return
		}
		for _, item := range items {
			mapping, ok := item.(map[string]any)
			if !ok {
				continue
			}
			frontPosition, frontOK := objectID(mapping["position"])
			rearPosition, rearOK := objectID(mapping[targetField+"_position"])
			if !frontOK || !rearOK {
				continue
			}
			addID("mapping_"+frontPosition+"_"+rearPosition, targetKind, mapping[targetField])
		}
	}
	addTerminations := func(side string, value any) {
		items, ok := value.([]any)
		if !ok {
			return
		}
		for _, item := range items {
			termination, ok := item.(map[string]any)
			if !ok {
				continue
			}
			addGeneric("termination_"+side, termination["object_type"], termination["object_id"])
		}
	}

	switch kind {
	case "tag":
		add("owner", "owner", record["owner"])
	case "owner":
		add("group", "owner_group", record["group"])
	case "user_group":
		addMany("permission", "object_permission", record["permissions"])
	case "user":
		addMany("group", "user_group", record["groups"])
		addMany("permission", "object_permission", record["permissions"])
	case "tenant_group":
		add("parent", "tenant_group", record["parent"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "tenant":
		add("group", "tenant_group", record["group"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "site_group":
		add("parent", "site_group", record["parent"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "rir":
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "asn":
		add("rir", "rir", record["rir"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "region":
		add("parent", "region", record["parent"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "site":
		add("region", "region", record["region"])
		add("group", "site_group", record["group"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("asn", "asn", record["asns"])
		addMany("tag", "tag", record["tags"])
	case "location":
		add("site", "site", record["site"])
		add("parent", "location", record["parent"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "manufacturer", "rack_group", "rack_role":
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "device_role":
		add("parent", "device_role", record["parent"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "platform":
		add("parent", "platform", record["parent"])
		add("manufacturer", "manufacturer", record["manufacturer"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "device_type":
		add("manufacturer", "manufacturer", record["manufacturer"])
		add("default_platform", "platform", record["default_platform"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "rack_type":
		add("manufacturer", "manufacturer", record["manufacturer"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "rack":
		add("site", "site", record["site"])
		add("location", "location", record["location"])
		add("group", "rack_group", record["group"])
		add("tenant", "tenant", record["tenant"])
		add("role", "rack_role", record["role"])
		add("rack_type", "rack_type", record["rack_type"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "module_type_profile", "inventory_item_role", "cable_bundle":
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "module_type":
		add("profile", "module_type_profile", record["profile"])
		add("manufacturer", "manufacturer", record["manufacturer"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "console_port_template", "console_server_port_template", "power_port_template",
		"power_outlet_template", "interface_template", "rear_port_template", "front_port_template",
		"module_bay_template":
		add("device_type", "device_type", record["device_type"])
		add("module_type", "module_type", record["module_type"])
		if kind == "power_outlet_template" {
			add("power_port", "power_port_template", record["power_port"])
		}
		if kind == "interface_template" {
			add("bridge", "interface_template", record["bridge"])
		}
		if kind == "front_port_template" {
			addMappings(record["rear_ports"], "rear_port", "rear_port_template")
		}
	case "device_bay_template":
		add("device_type", "device_type", record["device_type"])
	case "inventory_item_template":
		add("device_type", "device_type", record["device_type"])
		addID("parent", "inventory_item_template", record["parent"])
		add("role", "inventory_item_role", record["role"])
		add("manufacturer", "manufacturer", record["manufacturer"])
		addGeneric("component", record["component_type"], record["component_id"])
	case "virtual_chassis":
		add("master", "device", record["master"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "device":
		add("device_type", "device_type", record["device_type"])
		add("role", "device_role", record["role"])
		add("tenant", "tenant", record["tenant"])
		add("platform", "platform", record["platform"])
		add("site", "site", record["site"])
		add("location", "location", record["location"])
		add("rack", "rack", record["rack"])
		add("virtual_chassis", "virtual_chassis", record["virtual_chassis"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "virtual_device_context":
		add("device", "device", record["device"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "module":
		add("device", "device", record["device"])
		add("module_bay", "module_bay", record["module_bay"])
		add("module_type", "module_type", record["module_type"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "module_bay":
		add("device", "device", record["device"])
		add("module", "module", record["module"])
		add("parent", "module_bay", record["parent"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "device_bay":
		add("device", "device", record["device"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "console_port", "console_server_port", "power_port", "power_outlet", "interface", "rear_port", "front_port":
		add("device", "device", record["device"])
		add("module", "module", record["module"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
		if kind == "power_outlet" {
			add("power_port", "power_port", record["power_port"])
		}
		if kind == "interface" {
			add("parent", "interface", record["parent"])
			add("bridge", "interface", record["bridge"])
			add("lag", "interface", record["lag"])
			add("primary_mac_address", "mac_address", record["primary_mac_address"])
			addMany("vdc", "virtual_device_context", record["vdcs"])
		}
		if kind == "front_port" {
			addMappings(record["rear_ports"], "rear_port", "rear_port")
		}
	case "inventory_item":
		add("device", "device", record["device"])
		addID("parent", "inventory_item", record["parent"])
		add("role", "inventory_item_role", record["role"])
		add("manufacturer", "manufacturer", record["manufacturer"])
		addGeneric("component", record["component_type"], record["component_id"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "mac_address":
		addGeneric("assigned", record["assigned_object_type"], record["assigned_object_id"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "rack_reservation":
		add("rack", "rack", record["rack"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "power_panel":
		add("site", "site", record["site"])
		add("location", "location", record["location"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "power_feed":
		add("power_panel", "power_panel", record["power_panel"])
		add("rack", "rack", record["rack"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "cable":
		add("tenant", "tenant", record["tenant"])
		add("bundle", "cable_bundle", record["bundle"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
		addTerminations("a", record["a_terminations"])
		addTerminations("b", record["b_terminations"])
	case "provider":
		addMany("asn", "asn", record["asns"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "provider_account", "provider_network":
		add("provider", "provider", record["provider"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "circuit_type", "virtual_circuit_type":
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "circuit_group":
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "circuit":
		add("provider", "provider", record["provider"])
		add("provider_account", "provider_account", record["provider_account"])
		add("type", "circuit_type", record["type"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "circuit_termination":
		add("circuit", "circuit", record["circuit"])
		addGeneric("termination", record["termination_type"], record["termination_id"])
		addMany("tag", "tag", record["tags"])
	case "virtual_circuit":
		add("provider_network", "provider_network", record["provider_network"])
		add("provider_account", "provider_account", record["provider_account"])
		add("type", "virtual_circuit_type", record["type"])
		add("tenant", "tenant", record["tenant"])
		add("owner", "owner", record["owner"])
		addMany("tag", "tag", record["tags"])
	case "virtual_circuit_termination":
		add("virtual_circuit", "virtual_circuit", record["virtual_circuit"])
		add("interface", "interface", record["interface"])
		addMany("tag", "tag", record["tags"])
	case "circuit_group_assignment":
		add("group", "circuit_group", record["group"])
		addGeneric("member", record["member_type"], record["member_id"])
		addMany("tag", "tag", record["tags"])
	}
	return relationships
}

func resourceKindForObjectType(objectType string) (string, bool) {
	kind, ok := map[string]string{
		"dcim.consoleporttemplate":       "console_port_template",
		"dcim.consoleserverporttemplate": "console_server_port_template",
		"dcim.powerporttemplate":         "power_port_template",
		"dcim.poweroutlettemplate":       "power_outlet_template",
		"dcim.interfacetemplate":         "interface_template",
		"dcim.rearporttemplate":          "rear_port_template",
		"dcim.frontporttemplate":         "front_port_template",
		"dcim.consoleport":               "console_port",
		"dcim.consoleserverport":         "console_server_port",
		"dcim.powerport":                 "power_port",
		"dcim.poweroutlet":               "power_outlet",
		"dcim.interface":                 "interface",
		"dcim.rearport":                  "rear_port",
		"dcim.frontport":                 "front_port",
		"dcim.powerfeed":                 "power_feed",
		"dcim.region":                    "region",
		"dcim.sitegroup":                 "site_group",
		"dcim.site":                      "site",
		"dcim.location":                  "location",
		"circuits.provider":              "provider",
		"circuits.providernetwork":       "provider_network",
		"circuits.circuit":               "circuit",
		"circuits.circuittermination":    "circuit_termination",
		"circuits.virtualcircuit":        "virtual_circuit",
	}[objectType]
	return kind, ok
}

func unsupportedTerminationTypes(record map[string]any) []string {
	unsupported := make(map[string]struct{})
	for _, side := range []string{"a_terminations", "b_terminations"} {
		terminations, ok := record[side].([]any)
		if !ok {
			continue
		}
		for _, value := range terminations {
			termination, ok := value.(map[string]any)
			if !ok {
				continue
			}
			objectType, ok := contentTypeValue(termination["object_type"]).(string)
			if !ok || objectType == "" {
				unsupported["unknown"] = struct{}{}
				continue
			}
			if _, ok := resourceKindForObjectType(objectType); !ok {
				unsupported[objectType] = struct{}{}
			}
		}
	}
	values := make([]string, 0, len(unsupported))
	for value := range unsupported {
		values = append(values, value)
	}
	sort.Strings(values)
	return values
}

func nestedValue(value any, key string) any {
	object, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	return object[key]
}

func choiceValue(value any) any {
	if object, ok := value.(map[string]any); ok {
		return object["value"]
	}
	return value
}

func contentTypeValue(value any) any {
	if object, ok := value.(map[string]any); ok {
		appLabel, appOK := object["app_label"].(string)
		model, modelOK := object["model"].(string)
		if appOK && modelOK && appLabel != "" && model != "" {
			return appLabel + "." + model
		}
	}
	if value, ok := value.(string); ok {
		return value
	}
	return nil
}

func decimalValue(value any) any {
	switch typed := value.(type) {
	case json.Number:
		parsed, err := typed.Float64()
		if err == nil {
			return parsed
		}
	case string:
		parsed, err := strconv.ParseFloat(typed, 64)
		if err == nil {
			return parsed
		}
	}
	return value
}

func stringValues(value any) []string {
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	values := make([]string, 0, len(items))
	for _, item := range items {
		if value, ok := item.(string); ok && value != "" {
			values = append(values, value)
		}
	}
	sort.Strings(values)
	return values
}

func nestedID(value any) (string, bool) {
	return objectID(nestedValue(value, "id"))
}

func objectID(value any) (string, bool) {
	switch typed := value.(type) {
	case json.Number:
		return typed.String(), typed.String() != ""
	case float64:
		return strconv.FormatFloat(typed, 'f', -1, 64), true
	case string:
		return typed, typed != ""
	default:
		return "", false
	}
}

func externalID(kind string, id string) string {
	return "netbox:" + kind + ":" + id
}
