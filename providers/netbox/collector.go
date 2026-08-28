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
	"regions":   {{Path: "dcim/regions/", Kind: "region"}},
	"sites":     {{Path: "dcim/sites/", Kind: "site"}},
	"locations": {{Path: "dcim/locations/", Kind: "location"}},
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
	attributes := make([]contracts.ObservationAttribute, 0, 12)
	add := func(path string, value any) {
		if value != nil && value != "" {
			attributes = append(attributes, contracts.ObservationAttribute{Path: path, Value: value})
		}
	}
	addDirect := func(path string, key string) { add(path, record[key]) }
	addChoice := func(path string, key string) { add(path, nestedValue(record[key], "value")) }

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
		addDirect("/latitude", "latitude")
		addDirect("/longitude", "longitude")
		addDirect("/comments", "comments")
	case "location":
		addDirect("/name", "name")
		addDirect("/slug", "slug")
		addChoice("/status", "status")
		addDirect("/facility", "facility")
		addDirect("/description", "description")
		addDirect("/comments", "comments")
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

	switch kind {
	case "tag":
		add("owner", "owner", record["owner"])
	case "owner":
		add("group", "owner_group", record["group"])
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
	}
	return relationships
}

func nestedValue(value any, key string) any {
	object, ok := value.(map[string]any)
	if !ok {
		return nil
	}
	return object[key]
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
