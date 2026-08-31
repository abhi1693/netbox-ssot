package unifiprovider

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

type staticResolver struct {
	value string
	err   error
}

func (r staticResolver) Resolve(context.Context, string) (string, error) {
	return r.value, r.err
}

func TestManifestMatchesCompiledCollector(t *testing.T) {
	manifest, err := New().Manifest()
	if err != nil {
		t.Fatalf("Manifest() error = %v", err)
	}
	if manifest.ProviderID != "unifi" || manifest.ImplementationVersion != "0.0.1" ||
		manifest.ContractVersion != contracts.ContractVersion {
		t.Fatalf("unexpected manifest identity: %+v", manifest)
	}
	if manifest.AgentCompatibility.CollectorID != manifest.ProviderID {
		t.Fatalf("collector ID = %q, want %q", manifest.AgentCompatibility.CollectorID, manifest.ProviderID)
	}
	if manifest.FieldOwnership != "observed" {
		t.Fatalf("field ownership = %q, want observed", manifest.FieldOwnership)
	}
	wantDatasets := []string{"unifi_sites", "unifi_devices", "unifi_interfaces", "unifi_networks", "unifi_wireless"}
	gotDatasets := make([]string, len(manifest.Datasets))
	for index, dataset := range manifest.Datasets {
		gotDatasets[index] = dataset.ID
	}
	if !reflect.DeepEqual(gotDatasets, wantDatasets) {
		t.Fatalf("dataset IDs = %v, want %v", gotDatasets, wantDatasets)
	}
}

func TestParseConfigurationDefaultsAndRejectsUnsafeValues(t *testing.T) {
	config, apiURL, err := parseConfiguration(map[string]any{
		"api_url":     "https://unifi.example.com/proxy/network/integration/",
		"api_key_ref": "env://UNIFI_API_KEY",
		"site_ref":    "default",
	})
	if err != nil {
		t.Fatalf("parseConfiguration() error = %v", err)
	}
	if config.PageSize != 200 || config.TimeoutSeconds != 30 || apiURL.String() !=
		"https://unifi.example.com/proxy/network/integration/v1/" {
		t.Fatalf("parseConfiguration() = %+v, %s", config, apiURL)
	}

	tests := []map[string]any{
		{"api_url": "http://unifi.example.com/integration", "api_key_ref": "env://UNIFI_API_KEY"},
		{"api_url": "https://user:pass@unifi.example.com/integration", "api_key_ref": "env://UNIFI_API_KEY"},
		{"api_url": "https://unifi.example.com/integration?key=secret", "api_key_ref": "env://UNIFI_API_KEY"},
		{"api_url": "https://unifi.example.com/integration/v1", "api_key_ref": "env://UNIFI_API_KEY"},
		{"api_url": "https://unifi.example.com/integration", "api_key_ref": "env://UNIFI_API_KEY", "page_size": 201},
		{"api_url": "https://unifi.example.com/integration", "api_key_ref": "env://UNIFI_API_KEY", "unknown": true},
		{
			"api_url": "https://unifi.example.com/integration", "api_key_ref": "env://UNIFI_API_KEY",
			"site_name_override": "Home",
		},
	}
	for _, raw := range tests {
		if _, _, err := parseConfiguration(raw); err == nil {
			t.Fatalf("parseConfiguration() accepted unsafe configuration: %#v", raw)
		}
	}
}

func TestConnectionValidatesIdentitySecretAndSelectedSite(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.Header.Get("X-API-Key") != "api-key" {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		switch request.URL.Path {
		case "/proxy/network/integration/v1/info":
			json.NewEncoder(response).Encode(map[string]any{"applicationVersion": "10.6.101"})
		case "/proxy/network/integration/v1/sites":
			writePage(response, request, []site{{ID: "site-1", InternalReference: "default", Name: "Default"}})
		default:
			http.NotFound(response, request)
		}
	}))
	defer server.Close()
	collector := NewWithClient(server.Client())

	result := collector.TestConnection(context.Background(), contracts.ConnectionTestRequest{
		SourceID: "source-1", ProviderID: "unifi", ExecutionMode: "agent",
		Configuration: testConfiguration(server.URL, map[string]any{"site_ref": "default"}),
	}, staticResolver{value: "api-key"})
	if !result.Succeeded || result.Summary != "Connected to UniFi Network 10.6.101 and found 1 selected site(s)." {
		t.Fatalf("TestConnection() = %+v", result)
	}

	missingSite := collector.TestConnection(context.Background(), contracts.ConnectionTestRequest{
		SourceID: "source-1", ProviderID: "unifi", ExecutionMode: "agent",
		Configuration: testConfiguration(server.URL, map[string]any{"site_ref": "missing"}),
	}, staticResolver{value: "api-key"})
	if missingSite.Succeeded || missingSite.Details[0].Code != "site_selection" {
		t.Fatalf("missing site result = %+v", missingSite)
	}

	secretFailure := collector.TestConnection(context.Background(), contracts.ConnectionTestRequest{
		SourceID: "source-1", ProviderID: "unifi", ExecutionMode: "agent",
		Configuration: testConfiguration(server.URL, nil),
	}, staticResolver{err: errors.New("not configured")})
	if secretFailure.Succeeded || secretFailure.Details[0].Code != "secret_unavailable" {
		t.Fatalf("secret failure = %+v", secretFailure)
	}
}

func TestCollectsDependencyClosedOfficialAPIProjection(t *testing.T) {
	var lock sync.Mutex
	requests := make([]string, 0)
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		lock.Lock()
		requests = append(requests, request.Method+" "+request.URL.RequestURI())
		lock.Unlock()
		if request.Method != http.MethodGet || request.Header.Get("X-API-Key") != "super-secret-api-key" {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		switch request.URL.Path {
		case "/proxy/network/integration/v1/sites":
			writePage(response, request, []site{{ID: "site-1", InternalReference: "default", Name: "Default"}})
		case "/proxy/network/integration/v1/sites/site-1/devices":
			writePage(response, request, []deviceOverview{
				{
					ID: "switch-1", MACAddress: "AA-BB-CC-DD-EE-01", IPAddress: "192.0.2.10",
					Name: "Switch 1", Model: "USW Pro", State: "ONLINE", Supported: true,
					Features: []string{"switching"}, Interfaces: []string{"ports"},
				},
				{
					ID: "ap-1", MACAddress: "AA:BB:CC:DD:EE:02", IPAddress: "2001:db8::10",
					Name: "AP 1", Model: "U6+", State: "OFFLINE", Supported: true,
					Features: []string{"accessPoint"}, Interfaces: []string{"radios"},
				},
			})
		case "/proxy/network/integration/v1/sites/site-1/devices/switch-1":
			json.NewEncoder(response).Encode(deviceDetails{
				ID: "switch-1",
				Interfaces: deviceInterfaces{Ports: []portOverview{{
					Index: 1, State: "DOWN", Connector: "RJ45", MaxSpeedMbps: 2500,
					PoE: &poeOverview{Standard: "802.3bt", Type: 3, Enabled: false, State: "DOWN"},
				}}},
			})
		case "/proxy/network/integration/v1/sites/site-1/devices/ap-1":
			json.NewEncoder(response).Encode(map[string]any{
				"id": "ap-1",
				"interfaces": map[string]any{"radios": []map[string]any{
					{"wlanStandard": "802.11ax", "frequencyGHz": "2.4", "channelWidthMHz": 20, "channel": 6},
					{"wlanStandard": "802.11ac", "frequencyGHz": 5, "channelWidthMHz": 80, "channel": 44},
				}},
			})
		case "/proxy/network/integration/v1/sites/site-1/networks":
			writePage(response, request, []networkOverview{{
				Management: "GATEWAY", ID: "network-1", Name: "Default", Enabled: true, VLANID: 1,
			}})
		case "/proxy/network/integration/v1/sites/site-1/networks/network-1":
			json.NewEncoder(response).Encode(networkDetails{
				Management: "GATEWAY", ID: "network-1", Name: "Default", Enabled: true, VLANID: 1,
				IPv4Configuration: &ipv4Configuration{HostIPAddress: "192.0.2.1", PrefixLength: 24},
			})
		case "/proxy/network/integration/v1/sites/site-1/wifi/broadcasts":
			writePage(response, request, []wifiBroadcast{{
				Type: "STANDARD", ID: "wifi-1", Name: "IoT", Enabled: true,
				Network:               &wifiNetworkReference{Type: "SPECIFIC", NetworkID: "network-1"},
				SecurityConfiguration: wifiSecurity{Type: "WPA2_PERSONAL"},
			}})
		default:
			http.NotFound(response, request)
		}
	}))
	defer server.Close()
	collector := NewWithClient(server.Client())
	request := contracts.CollectionRequest{
		RunID: "run-1", SourceID: "source-1", ProviderID: "unifi", ExecutionMode: "agent",
		Datasets: []string{"unifi_wireless", "unifi_interfaces"},
		Configuration: testConfiguration(server.URL, map[string]any{
			"site_ref": "default", "site_name_override": "Home", "site_slug_override": "home",
		}),
	}

	batch := collector.Collect(context.Background(), request, staticResolver{value: "super-secret-api-key"})
	if batch.State != "complete" || batch.CompletenessToken == "" {
		t.Fatalf("Collect() state = %q, messages = %+v", batch.State, batch.Messages)
	}
	wantDatasets := []string{"unifi_sites", "unifi_devices", "unifi_interfaces", "unifi_networks", "unifi_wireless"}
	if !reflect.DeepEqual(batch.Datasets, wantDatasets) {
		t.Fatalf("datasets = %v, want %v", batch.Datasets, wantDatasets)
	}
	counts := make(map[string]int)
	byID := make(map[string]contracts.Observation)
	for _, observation := range batch.Observations {
		counts[observation.ResourceKind]++
		byID[observation.ExternalID] = observation
	}
	wantCounts := map[string]int{
		"site": 1, "manufacturer": 1, "device_role": 2, "device_type": 2, "device": 2,
		"interface": 5, "mac_address": 2, "ip_address": 2, "vlan": 1, "prefix": 1, "wireless_lan": 1,
	}
	if !reflect.DeepEqual(counts, wantCounts) {
		t.Fatalf("observation counts = %#v, want %#v", counts, wantCounts)
	}

	home := attributeMap(byID["unifi:site:site-1"])
	if home["/name"] != "Home" || home["/slug"] != "home" {
		t.Fatalf("site attributes = %#v", home)
	}
	switchDevice := byID["unifi:device:site-1:switch-1"]
	if attributeMap(switchDevice)["/status"] != "active" || relationshipMap(switchDevice)["role"] !=
		"unifi:device-role:network-device" {
		t.Fatalf("switch device = %+v", switchDevice)
	}
	apDevice := byID["unifi:device:site-1:ap-1"]
	if attributeMap(apDevice)["/status"] != "offline" || relationshipMap(apDevice)["role"] !=
		"unifi:device-role:wireless-ap" {
		t.Fatalf("AP device = %+v", apDevice)
	}
	port := attributeMap(byID["unifi:interface:switch-1:port:1"])
	if port["/type"] != "2.5gbase-t" || port["/enabled"] != true || port["/poe_mode"] != "pse" ||
		port["/poe_type"] != "type3-ieee802.3bt" {
		t.Fatalf("port attributes = %#v", port)
	}
	radio := attributeMap(byID["unifi:interface:ap-1:radio:5"])
	if radio["/type"] != "ieee802.11ac" || radio["/rf_role"] != "ap" || radio["/rf_channel_width"] != float64(80) &&
		radio["/rf_channel_width"] != 80 {
		t.Fatalf("radio attributes = %#v", radio)
	}
	if attributeMap(byID["unifi:ip-address:switch-1:192.0.2.10/32"])["/address"] != "192.0.2.10/32" {
		t.Fatalf("IPv4 management address was not normalized")
	}
	if attributeMap(byID["unifi:ip-address:ap-1:2001:db8::10/128"])["/address"] != "2001:db8::10/128" {
		t.Fatalf("IPv6 management address was not normalized")
	}
	prefix := byID["unifi:prefix:site-1:network-1"]
	if attributeMap(prefix)["/prefix"] != "192.0.2.0/24" || relationshipMap(prefix)["vlan"] !=
		"unifi:vlan:site-1:network-1" {
		t.Fatalf("prefix = %+v", prefix)
	}
	wlan := byID["unifi:wireless-lan:site-1:wifi-1"]
	if attributeMap(wlan)["/auth_type"] != "wpa-personal" || relationshipMap(wlan)["scope_site"] !=
		"unifi:site:site-1" {
		t.Fatalf("wireless LAN = %+v", wlan)
	}

	encoded, err := json.Marshal(batch)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "super-secret-api-key") {
		t.Fatal("observation batch leaked the UniFi API key")
	}
	for _, received := range requests {
		if !strings.HasPrefix(received, "GET ") {
			t.Fatalf("collector issued a non-read-only request: %s", received)
		}
	}
}

func TestCollectionUsesStableOffsetPagination(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/proxy/network/integration/v1/sites" {
			http.NotFound(response, request)
			return
		}
		offset, _ := strconvAtoi(request.URL.Query().Get("offset"))
		sites := []site{
			{ID: "site-1", InternalReference: "one", Name: "One"},
			{ID: "site-2", InternalReference: "two", Name: "Two"},
			{ID: "site-3", InternalReference: "three", Name: "Three"},
		}
		end := offset + 2
		if end > len(sites) {
			end = len(sites)
		}
		json.NewEncoder(response).Encode(page[site]{
			Offset: offset, Limit: 2, Count: end - offset, TotalCount: len(sites), Data: sites[offset:end],
		})
	}))
	defer server.Close()
	config := testConfiguration(server.URL, map[string]any{"page_size": 2, "site_ref": "three"})
	collector := NewWithClient(server.Client())
	batch := collector.Collect(context.Background(), contracts.CollectionRequest{
		RunID: "run", SourceID: "source", ProviderID: "unifi", ExecutionMode: "agent",
		Datasets: []string{"unifi_sites"}, Configuration: config,
	}, staticResolver{value: "api-key"})
	if batch.State != "complete" || len(batch.Observations) != 1 {
		t.Fatalf("paginated batch = %+v", batch)
	}
}

func TestCollectionFailsClosedWhenPaginationChanges(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		offset, _ := strconvAtoi(request.URL.Query().Get("offset"))
		total := 2
		if offset > 0 {
			total = 3
		}
		json.NewEncoder(response).Encode(page[site]{
			Offset: offset, Limit: 1, Count: 1, TotalCount: total,
			Data: []site{{ID: fmt.Sprintf("site-%d", offset), InternalReference: fmt.Sprintf("site-%d", offset), Name: "Site"}},
		})
	}))
	defer server.Close()
	collector := NewWithClient(server.Client())
	batch := collector.Collect(context.Background(), contracts.CollectionRequest{
		RunID: "run", SourceID: "source", ProviderID: "unifi", ExecutionMode: "agent",
		Datasets: []string{"unifi_sites"}, Configuration: testConfiguration(server.URL, map[string]any{"page_size": 1}),
	}, staticResolver{value: "api-key"})
	if batch.State != "failed" || batch.Messages[0].Code != "collection_changed" || !batch.Messages[0].Retryable {
		t.Fatalf("changed pagination batch = %+v", batch)
	}
}

func TestCollectionReturnsPartialEvidenceWhenRequiredDetailFails(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/proxy/network/integration/v1/sites":
			writePage(response, request, []site{{ID: "site-1", InternalReference: "default", Name: "Default"}})
		case "/proxy/network/integration/v1/sites/site-1/devices":
			writePage(response, request, []deviceOverview{{
				ID: "device-1", Name: "Device", Model: "Model", State: "ONLINE", Features: []string{"switching"},
			}})
		case "/proxy/network/integration/v1/sites/site-1/devices/device-1":
			http.Error(response, "temporary", http.StatusServiceUnavailable)
		default:
			http.NotFound(response, request)
		}
	}))
	defer server.Close()
	collector := NewWithClient(server.Client())
	batch := collector.Collect(context.Background(), contracts.CollectionRequest{
		RunID: "run", SourceID: "source", ProviderID: "unifi", ExecutionMode: "agent",
		Datasets: []string{"unifi_interfaces"}, Configuration: testConfiguration(server.URL, nil),
	}, staticResolver{value: "api-key"})
	if batch.State != "partial" || len(batch.Observations) != 5 || batch.CompletenessToken != "" ||
		batch.Messages[0].Code != "http_status" || !batch.Messages[0].Retryable {
		t.Fatalf("partial batch = %+v", batch)
	}
}

func TestWirelessUnknownSecurityAndMissingNetworkFailClosed(t *testing.T) {
	request := contracts.CollectionRequest{SourceID: "source", ProviderID: "unifi"}
	sourceSite := site{ID: "site-1", InternalReference: "default", Name: "Default"}
	_, err := wirelessObservation(request, sourceSite, wifiBroadcast{
		ID: "wifi-1", Name: "Future", SecurityConfiguration: wifiSecurity{Type: "FUTURE_SECURITY"},
	}, map[string]string{}, testTime())
	if err == nil || !strings.Contains(err.Error(), "unsupported security") {
		t.Fatalf("unknown security error = %v", err)
	}
	_, err = wirelessObservation(request, sourceSite, wifiBroadcast{
		ID: "wifi-1", Name: "Missing", SecurityConfiguration: wifiSecurity{Type: "OPEN"},
		Network: &wifiNetworkReference{NetworkID: "missing"},
	}, map[string]string{}, testTime())
	if err == nil || !strings.Contains(err.Error(), "outside the collected site") {
		t.Fatalf("missing network error = %v", err)
	}
}

func TestMappingHelpersCoverOfficialDeviceShapes(t *testing.T) {
	if got := deviceStatus("CONNECTION_INTERRUPTED"); got != "offline" {
		t.Fatalf("deviceStatus() = %q", got)
	}
	if got := interfaceType("SFPPLUS", 10_000); got != "10gbase-x-sfpp" {
		t.Fatalf("interfaceType() = %q", got)
	}
	if got := radioInterfaceType("802.11be"); got != "ieee802.11be" {
		t.Fatalf("radioInterfaceType() = %q", got)
	}
	if got, err := normalizeMAC("AA-BB-CC-DD-EE-FF"); err != nil || got != "aa:bb:cc:dd:ee:ff" {
		t.Fatalf("normalizeMAC() = %q, %v", got, err)
	}
	if _, err := normalizeMAC("not-a-mac"); err == nil {
		t.Fatal("normalizeMAC() accepted an invalid address")
	}
	if got, err := hostAddress("::ffff:192.0.2.1"); err != nil || got != "192.0.2.1/32" {
		t.Fatalf("hostAddress() = %q, %v", got, err)
	}
	if got := slugify("  UniFi Site + Lab  "); got != "unifi-site-lab" {
		t.Fatalf("slugify() = %q", got)
	}
}

func TestCollectionRejectsScopeUnknownDatasetAndInvalidSecret(t *testing.T) {
	collector := New()
	base := contracts.CollectionRequest{
		RunID: "run", SourceID: "source", ProviderID: "unifi", ExecutionMode: "agent",
		Datasets: []string{"unifi_sites"},
		Configuration: map[string]any{
			"api_url": "https://unifi.example.com/integration", "api_key_ref": "env://KEY", "site_ref": "default",
		},
	}
	scoped := base
	scoped.Scope = []contracts.ScopeDimension{{Name: "site", Value: "one"}}
	if batch := collector.Collect(context.Background(), scoped, staticResolver{value: "key"}); batch.Messages[0].Code != "unsupported_scope" {
		t.Fatalf("scoped collection = %+v", batch)
	}
	unknown := base
	unknown.Datasets = []string{"missing"}
	if batch := collector.Collect(context.Background(), unknown, staticResolver{value: "key"}); batch.Messages[0].Code != "invalid_datasets" {
		t.Fatalf("unknown dataset collection = %+v", batch)
	}
	if batch := collector.Collect(context.Background(), base, staticResolver{value: " key\n"}); batch.Messages[0].Code != "secret_unavailable" {
		t.Fatalf("invalid secret collection = %+v", batch)
	}
}

func testConfiguration(serverURL string, updates map[string]any) map[string]any {
	configuration := map[string]any{
		"api_url":     serverURL + "/proxy/network/integration",
		"api_key_ref": "env://UNIFI_API_KEY",
		"site_ref":    "default",
		"verify_tls":  false,
	}
	for key, value := range updates {
		configuration[key] = value
	}
	return configuration
}

func writePage[T any](response http.ResponseWriter, request *http.Request, items []T) {
	offset, _ := strconvAtoi(request.URL.Query().Get("offset"))
	limit, _ := strconvAtoi(request.URL.Query().Get("limit"))
	if limit == 0 {
		limit = len(items)
	}
	end := offset + limit
	if end > len(items) {
		end = len(items)
	}
	selected := []T{}
	if offset < len(items) {
		selected = items[offset:end]
	}
	json.NewEncoder(response).Encode(page[T]{
		Offset: offset, Limit: limit, Count: len(selected), TotalCount: len(items), Data: selected,
	})
}

func strconvAtoi(value string) (int, error) {
	if value == "" {
		return 0, nil
	}
	var parsed int
	_, err := fmt.Sscanf(value, "%d", &parsed)
	return parsed, err
}

func attributeMap(observation contracts.Observation) map[string]any {
	attributes := make(map[string]any, len(observation.Attributes))
	for _, attribute := range observation.Attributes {
		attributes[attribute.Path] = attribute.Value
	}
	return attributes
}

func relationshipMap(observation contracts.Observation) map[string]string {
	relationships := make(map[string]string, len(observation.Relationships))
	for _, relationship := range observation.Relationships {
		relationships[relationship.Kind] = relationship.TargetExternalID
	}
	return relationships
}

func testTime() time.Time {
	return time.Date(2026, 8, 31, 12, 0, 0, 0, time.UTC)
}

func TestObservationOrderIsDeterministic(t *testing.T) {
	request := contracts.CollectionRequest{SourceID: "source", ProviderID: "unifi"}
	observation, err := newObservation(
		request, "site", "external", "site", "source-object",
		map[string]any{"/z": "last", "/a": "first"},
		[]contracts.Relationship{
			{Kind: "z", TargetKind: "site", TargetExternalID: "z"},
			{Kind: "a", TargetKind: "site", TargetExternalID: "a"},
		},
		testTime(),
	)
	if err != nil {
		t.Fatal(err)
	}
	attributePaths := []string{observation.Attributes[0].Path, observation.Attributes[1].Path}
	relationshipKinds := []string{observation.Relationships[0].Kind, observation.Relationships[1].Kind}
	if !sort.StringsAreSorted(attributePaths) || !sort.StringsAreSorted(relationshipKinds) {
		t.Fatalf("observation is not deterministic: %+v", observation)
	}
}
