package netboxprovider

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"slices"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

type staticSecrets struct {
	value     string
	reference string
}

func (s *staticSecrets) Resolve(_ context.Context, reference string) (string, error) {
	s.reference = reference
	return s.value, nil
}

func TestManifestMatchesCompiledCollector(t *testing.T) {
	manifest, err := New().Manifest()
	if err != nil {
		t.Fatalf("Manifest() error = %v", err)
	}
	if manifest.ProviderID != "netbox" {
		t.Fatalf("ProviderID = %q, want netbox", manifest.ProviderID)
	}
	if manifest.AgentCompatibility.CollectorID != manifest.ProviderID {
		t.Fatalf("collector ID = %q, want %q", manifest.AgentCompatibility.CollectorID, manifest.ProviderID)
	}
	if len(manifest.Capabilities) != 1 || manifest.Capabilities[0] != "source_read" {
		t.Fatalf("Capabilities = %v, want source_read only", manifest.Capabilities)
	}
}

func TestEveryManifestMappingHasTheSameCompiledEndpoint(t *testing.T) {
	manifest, err := New().Manifest()
	if err != nil {
		t.Fatalf("Manifest() error = %v", err)
	}
	for _, dataset := range manifest.Datasets {
		compiled := make(map[string]string, len(datasetEndpoints[dataset.ID]))
		for _, endpoint := range datasetEndpoints[dataset.ID] {
			compiled[endpoint.Kind] = endpoint.Path
		}
		if len(compiled) != len(dataset.DataMappings) {
			t.Fatalf("dataset %q has %d compiled endpoints, want %d mappings", dataset.ID, len(compiled), len(dataset.DataMappings))
		}
		for _, mapping := range dataset.DataMappings {
			if got := compiled[mapping.DestinationKind]; got != mapping.SourcePath {
				t.Errorf("dataset %q kind %q endpoint = %q, want %q", dataset.ID, mapping.DestinationKind, got, mapping.SourcePath)
			}
		}
	}
}

func TestDynamicDCIMRelationshipsUseTheCorrectTargetKinds(t *testing.T) {
	templateRelationships := relationshipsFor("front_port_template", map[string]any{
		"rear_ports": []any{map[string]any{
			"position": json.Number("1"), "rear_port": json.Number("7"), "rear_port_position": json.Number("2"),
		}},
	})
	if len(templateRelationships) != 1 || templateRelationships[0].Kind != "mapping_1_2" ||
		templateRelationships[0].TargetKind != "rear_port_template" ||
		templateRelationships[0].TargetExternalID != "netbox:rear_port_template:7" {
		t.Fatalf("template relationships = %+v", templateRelationships)
	}

	cableRelationships := relationshipsFor("cable", map[string]any{
		"a_terminations": []any{map[string]any{"object_type": "dcim.interface", "object_id": json.Number("9")}},
		"b_terminations": []any{map[string]any{"object_type": "dcim.powerfeed", "object_id": json.Number("3")}},
	})
	if len(cableRelationships) != 2 || cableRelationships[0].TargetKind != "interface" ||
		cableRelationships[1].TargetKind != "power_feed" {
		t.Fatalf("cable relationships = %+v", cableRelationships)
	}
}

func TestCableRecordsExposeUnsupportedTerminationTypes(t *testing.T) {
	attributes := attributesFor("cable", map[string]any{
		"a_terminations": []any{map[string]any{"object_type": "dcim.interface", "object_id": 9}},
		"b_terminations": []any{map[string]any{"object_type": "wireless.wirelesslink", "object_id": 3}},
	})
	var got any
	for _, attribute := range attributes {
		if attribute.Path == "/unsupported_termination_types" {
			got = attribute.Value
		}
	}
	values, ok := got.([]string)
	if !ok || len(values) != 1 || values[0] != "wireless.wirelesslink" {
		t.Fatalf("unsupported termination types = %#v", got)
	}

	relationships := relationshipsFor("cable", map[string]any{
		"a_terminations": []any{
			map[string]any{"object_type": "circuits.circuittermination", "object_id": json.Number("3")},
		},
	})
	if len(relationships) != 1 || relationships[0].TargetKind != "circuit_termination" {
		t.Fatalf("circuit termination relationships = %+v", relationships)
	}
}

func TestCircuitRecordsPreserveTypedFieldsAndPortableRelationships(t *testing.T) {
	circuitAttributes := attributesFor("circuit", map[string]any{
		"cid": "CID-100", "status": map[string]any{"value": "active"},
		"install_date": "2026-01-02", "termination_date": "2028-01-02",
		"commit_rate": json.Number("1000000"), "distance": json.Number("12.5"),
		"distance_unit": map[string]any{"value": "km"}, "description": "Primary", "comments": "Notes",
	})
	values := make(map[string]any, len(circuitAttributes))
	for _, attribute := range circuitAttributes {
		values[attribute.Path] = attribute.Value
	}
	if values["/cid"] != "CID-100" || values["/status"] != "active" || values["/distance"] != 12.5 ||
		values["/distance_unit"] != "km" {
		t.Fatalf("circuit attributes = %#v", values)
	}

	tests := []struct {
		kind   string
		record map[string]any
		want   map[string]string
	}{
		{
			kind: "provider",
			record: map[string]any{
				"asns":  []any{map[string]any{"id": json.Number("1")}},
				"owner": map[string]any{"id": json.Number("2")},
				"tags":  []any{map[string]any{"id": json.Number("3")}},
			},
			want: map[string]string{"asn": "asn", "owner": "owner", "tag": "tag"},
		},
		{
			kind: "circuit_termination",
			record: map[string]any{
				"circuit":          map[string]any{"id": json.Number("4")},
				"termination_type": "dcim.location",
				"termination_id":   json.Number("5"),
			},
			want: map[string]string{"circuit": "circuit", "termination_location": "location"},
		},
		{
			kind: "virtual_circuit_termination",
			record: map[string]any{
				"virtual_circuit": map[string]any{"id": json.Number("6")},
				"interface":       map[string]any{"id": json.Number("7")},
			},
			want: map[string]string{"virtual_circuit": "virtual_circuit", "interface": "interface"},
		},
		{
			kind: "circuit_group_assignment",
			record: map[string]any{
				"group":       map[string]any{"id": json.Number("8")},
				"member_type": "circuits.virtualcircuit",
				"member_id":   json.Number("9"),
			},
			want: map[string]string{"group": "circuit_group", "member_virtual_circuit": "virtual_circuit"},
		},
	}
	for _, test := range tests {
		t.Run(test.kind, func(t *testing.T) {
			got := map[string]string{}
			for _, relationship := range relationshipsFor(test.kind, test.record) {
				got[relationship.Kind] = relationship.TargetKind
			}
			if len(got) != len(test.want) {
				t.Fatalf("relationships = %+v, want %+v", got, test.want)
			}
			for kind, targetKind := range test.want {
				if got[kind] != targetKind {
					t.Errorf("relationship %q target = %q, want %q", kind, got[kind], targetKind)
				}
			}
		})
	}
}

func TestUserRecordsExcludeCredentialsAndPreserveAccessRelationships(t *testing.T) {
	attributes := attributesFor("user", map[string]any{
		"username": "alice", "first_name": "Alice", "last_name": "Admin", "email": "alice@example.com",
		"is_active": true, "password": "source-password-hash", "is_superuser": true,
		"date_joined": "2026-01-01T00:00:00Z", "last_login": "2026-01-02T00:00:00Z",
	})
	values := make(map[string]any, len(attributes))
	for _, attribute := range attributes {
		values[attribute.Path] = attribute.Value
	}
	if len(values) != 5 || values["/username"] != "alice" || values["/is_active"] != true {
		t.Fatalf("user attributes = %#v", values)
	}
	for _, forbidden := range []string{"/password", "/is_superuser", "/date_joined", "/last_login"} {
		if _, ok := values[forbidden]; ok {
			t.Errorf("credential or destination-local field %q was collected", forbidden)
		}
	}

	permissionAttributes := attributesFor("object_permission", map[string]any{
		"name": "View sites", "description": "Read sites", "enabled": true,
		"actions": []any{"view", "change"}, "constraints": map[string]any{"status": "active"},
		"object_types": []any{"dcim.site", "dcim.location"},
	})
	permissionValues := make(map[string]any, len(permissionAttributes))
	for _, attribute := range permissionAttributes {
		permissionValues[attribute.Path] = attribute.Value
	}
	if strings.Join(permissionValues["/actions"].([]string), ",") != "change,view" ||
		strings.Join(permissionValues["/object_types"].([]string), ",") != "dcim.location,dcim.site" {
		t.Fatalf("permission attributes = %#v", permissionValues)
	}

	groupRelationships := relationshipsFor("user_group", map[string]any{
		"permissions": []any{map[string]any{"id": json.Number("11")}},
	})
	userRelationships := relationshipsFor("user", map[string]any{
		"groups":      []any{map[string]any{"id": json.Number("12")}},
		"permissions": []any{map[string]any{"id": json.Number("11")}},
	})
	if len(groupRelationships) != 1 || groupRelationships[0].TargetKind != "object_permission" {
		t.Fatalf("group relationships = %+v", groupRelationships)
	}
	if len(userRelationships) != 2 || userRelationships[0].TargetKind != "user_group" ||
		userRelationships[1].TargetKind != "object_permission" {
		t.Fatalf("user relationships = %+v", userRelationships)
	}

	manifest, err := New().Manifest()
	if err != nil {
		t.Fatalf("Manifest() error = %v", err)
	}
	for _, dataset := range manifest.Datasets {
		for _, mapping := range dataset.DataMappings {
			if mapping.SourceModel == "users.Token" || mapping.SourceModel == "users.UserConfig" {
				t.Fatalf("non-portable users model was advertised: %+v", mapping)
			}
		}
	}
}

func TestDataSourceRecordsPreservePortableConfigurationWithoutCredentialsOrRuntimeState(t *testing.T) {
	record := map[string]any{
		"id":   json.Number("41"),
		"name": "Automation", "type": map[string]any{"value": "git"},
		"source_url": "https://git.example.com/network/automation.git", "enabled": true,
		"sync_interval": json.Number("60"), "ignore_rules": "secrets/*", "description": "Config",
		"comments": "Managed", "status": map[string]any{"value": "completed"},
		"last_synced": "2026-01-01T00:00:00Z",
		"parameters": map[string]any{
			"branch": "production", "username": "source-user", "password": "source-password",
		},
	}
	attributes := attributesFor("data_source", record)
	values := make(map[string]any, len(attributes))
	for _, attribute := range attributes {
		values[attribute.Path] = attribute.Value
	}
	parameters, ok := values["/parameters"].(map[string]any)
	if !ok || len(parameters) != 1 || parameters["branch"] != "production" {
		t.Fatalf("portable parameters = %#v", values["/parameters"])
	}
	for _, forbidden := range []string{"/status", "/last_synced", "/password", "/username"} {
		if _, ok := values[forbidden]; ok {
			t.Errorf("runtime or credential field %q was collected", forbidden)
		}
	}
	if strings.Contains(string(mustJSON(t, attributes)), "source-password") ||
		strings.Contains(string(mustJSON(t, attributes)), "source-user") {
		t.Fatalf("data source attributes leaked backend credentials: %+v", attributes)
	}
	request := collectionRequest("https://netbox.example.test")
	first, err := mapObservation(request, "data_source", record)
	if err != nil {
		t.Fatalf("mapObservation() error = %v", err)
	}
	record["parameters"].(map[string]any)["password"] = "rotated-source-password"
	second, err := mapObservation(request, "data_source", record)
	if err != nil {
		t.Fatalf("mapObservation() error = %v", err)
	}
	if first.Evidence[0].RawDigest != second.Evidence[0].RawDigest {
		t.Fatal("destination-local credential changed the portable evidence digest")
	}

	relationships := relationshipsFor("data_source", map[string]any{
		"owner": map[string]any{"id": json.Number("21")},
	})
	if len(relationships) != 1 || relationships[0].Kind != "owner" || relationships[0].TargetKind != "owner" {
		t.Fatalf("data source relationships = %+v", relationships)
	}

	manifest, err := New().Manifest()
	if err != nil {
		t.Fatalf("Manifest() error = %v", err)
	}
	for _, dataset := range manifest.Datasets {
		for _, mapping := range dataset.DataMappings {
			switch mapping.SourceModel {
			case "core.DataFile", "core.Job", "core.ObjectChange", "core.ObjectType", "core.ConfigRevision":
				t.Fatalf("runtime Core model was advertised: %+v", mapping)
			}
		}
	}
}

func TestExtrasRecordsPreservePortableConfigurationAndRelationships(t *testing.T) {
	request := collectionRequest("https://netbox.example.com")
	tests := []struct {
		kind          string
		record        map[string]any
		attributePath string
		attributeWant any
		relationship  string
	}{
		{
			kind: "custom_field_choice_set",
			record: map[string]any{
				"id": json.Number("1"), "name": "Environment", "base_choices": map[string]any{"value": "IATA"},
				"extra_choices": []any{[]any{"prod", "Production"}}, "choice_colors": map[string]any{"prod": "red"},
				"order_alphabetically": true, "owner": map[string]any{"id": json.Number("20")},
			},
			attributePath: "/base_choices", attributeWant: "IATA", relationship: "owner",
		},
		{
			kind: "custom_field",
			record: map[string]any{
				"id": json.Number("2"), "name": "environment", "type": map[string]any{"value": "select"},
				"object_types": []any{"dcim.device"}, "related_object_type": "dcim.site",
				"choice_set": map[string]any{"id": json.Number("1")}, "owner": map[string]any{"id": json.Number("20")},
			},
			attributePath: "/related_object_type", attributeWant: "dcim.site", relationship: "choice_set",
		},
		{
			kind: "config_template",
			record: map[string]any{
				"id": json.Number("3"), "name": "Network OS", "template_code": "hostname {{ device.name }}", "debug": false,
				"tags": []any{map[string]any{"id": json.Number("30")}},
			},
			attributePath: "/template_code", attributeWant: "hostname {{ device.name }}", relationship: "tag",
		},
		{
			kind: "config_context",
			record: map[string]any{
				"id": json.Number("4"), "name": "Sites", "data": map[string]any{"ntp": "192.0.2.1"},
				"profile": map[string]any{"id": json.Number("40")}, "sites": []any{map[string]any{"id": json.Number("50")}},
			},
			attributePath: "/data", attributeWant: map[string]any{"ntp": "192.0.2.1"}, relationship: "site",
		},
		{
			kind: "notification_group",
			record: map[string]any{
				"id": json.Number("5"), "name": "Operators", "groups": []any{map[string]any{"id": json.Number("60")}},
				"users": []any{map[string]any{"id": json.Number("61")}},
			},
			attributePath: "/name", attributeWant: "Operators", relationship: "group",
		},
		{
			kind: "event_rule",
			record: map[string]any{
				"id": json.Number("6"), "name": "Device changes", "action_type": map[string]any{"value": "webhook"},
				"event_types": []any{"object_updated"}, "object_types": []any{"dcim.device"},
				"action_object_type": "extras.webhook", "action_object_id": json.Number("70"),
			},
			attributePath: "/action_type", attributeWant: "webhook", relationship: "action_webhook",
		},
	}

	for _, test := range tests {
		t.Run(test.kind, func(t *testing.T) {
			observation, err := mapObservation(request, test.kind, test.record)
			if err != nil {
				t.Fatalf("mapObservation() error = %v", err)
			}
			if got := attributeValue(observation, test.attributePath); !reflect.DeepEqual(got, test.attributeWant) {
				t.Fatalf("attribute %s = %#v, want %#v", test.attributePath, got, test.attributeWant)
			}
			if !slices.ContainsFunc(observation.Relationships, func(value contracts.Relationship) bool {
				return value.Kind == test.relationship
			}) {
				t.Fatalf("relationships = %+v, want %q", observation.Relationships, test.relationship)
			}
		})
	}
}

func TestWebhookEvidenceNeverHashesDestinationLocalCredentials(t *testing.T) {
	request := collectionRequest("https://netbox.example.com")
	record := map[string]any{
		"id": json.Number("80"), "name": "Automation", "payload_url": "https://automation.example.com/hooks/netbox",
		"http_method": map[string]any{"value": "POST"}, "http_content_type": "application/json",
		"body_template": "{{ data | json }}", "ssl_verification": true,
		"secret": "source-secret", "additional_headers": "Authorization: source-token", "ca_file_path": "/source/ca.pem",
	}
	first, err := mapObservation(request, "webhook", record)
	if err != nil {
		t.Fatalf("mapObservation() error = %v", err)
	}
	for _, path := range []string{"/secret", "/additional_headers", "/ca_file_path"} {
		if got := attributeValue(first, path); got != nil {
			t.Fatalf("destination-local attribute %s leaked as %#v", path, got)
		}
	}
	record["secret"] = "changed-secret"
	record["additional_headers"] = "Authorization: changed-token"
	second, err := mapObservation(request, "webhook", record)
	if err != nil {
		t.Fatalf("mapObservation() error = %v", err)
	}
	if first.Evidence[0].RawDigest != second.Evidence[0].RawDigest {
		t.Fatal("webhook evidence digest depends on destination-local credentials")
	}
}

func TestConfigContextMarksUnsupportedVirtualizationQualifiers(t *testing.T) {
	observation, err := mapObservation(collectionRequest("https://netbox.example.com"), "config_context", map[string]any{
		"id": json.Number("90"), "name": "Virtualization", "data": map[string]any{},
		"cluster_types": []any{map[string]any{"id": json.Number("1")}}, "clusters": []any{map[string]any{"id": json.Number("2")}},
	})
	if err != nil {
		t.Fatalf("mapObservation() error = %v", err)
	}
	if got := attributeValue(observation, "/unsupported_assignment_types"); !reflect.DeepEqual(
		got,
		[]string{"cluster_types", "clusters"},
	) {
		t.Fatalf("unsupported assignments = %#v", got)
	}
}

func TestCollectRejectsCredentialBearingDataSourceURL(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		if request.URL.Path != "/api/core/data-sources/" {
			json.NewEncoder(response).Encode(map[string]any{"next": nil, "results": []any{}})
			return
		}
		json.NewEncoder(response).Encode(map[string]any{
			"next": nil,
			"results": []any{map[string]any{
				"id": 1, "name": "Unsafe", "type": map[string]any{"value": "git"},
				"source_url": "https://user:secret@git.example.com/repository.git",
			}},
		})
	}))
	defer server.Close()

	request := collectionRequest(server.URL)
	request.Datasets = []string{"data_sources"}
	batch := NewWithClient(server.Client()).Collect(
		context.Background(), request, &staticSecrets{value: "source-token"},
	)

	if batch.State != "failed" || len(batch.Messages) != 1 ||
		batch.Messages[0].Code != "unsafe_source_configuration" {
		t.Fatalf("batch = %+v", batch)
	}
	encoded := string(mustJSON(t, batch))
	if strings.Contains(encoded, "secret") || strings.Contains(encoded, "user") {
		t.Fatalf("failure leaked source URL credentials: %s", encoded)
	}
}

func TestConnectionReadsStatusWithoutExposingToken(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", request.Method)
		}
		if request.URL.Path != "/api/status/" {
			t.Errorf("path = %s, want /api/status/", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Token source-token" {
			t.Error("authorization header was not set from the secret resolver")
		}
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(map[string]any{"netbox-version": "4.6.9"})
	}))
	defer server.Close()

	resolver := &staticSecrets{value: "source-token"}
	result := NewWithClient(server.Client()).TestConnection(context.Background(), connectionRequest(server.URL), resolver)

	if !result.Succeeded || result.Summary != "Connected to NetBox 4.6.9." {
		t.Fatalf("result = %+v", result)
	}
	if resolver.reference != "env://NETBOX_TOKEN" {
		t.Fatalf("resolved reference = %q", resolver.reference)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "source-token") || strings.Contains(string(encoded), "NETBOX_TOKEN") {
		t.Fatalf("connection result leaked secret material: %s", encoded)
	}
}

func TestConnectionRejectsHTTPSDowngradeRedirectBeforeForwardingToken(t *testing.T) {
	var destinationReached atomic.Bool
	destination := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		destinationReached.Store(true)
		response.WriteHeader(http.StatusOK)
	}))
	defer destination.Close()

	source := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Token source-token" {
			t.Error("authorization header was not sent to the configured source")
		}
		http.Redirect(response, request, destination.URL+"/api/status/", http.StatusFound)
	}))
	defer source.Close()

	request := connectionRequest(source.URL)
	request.Configuration["verify_tls"] = false
	result := New().TestConnection(context.Background(), request, &staticSecrets{value: "source-token"})

	if result.Succeeded || len(result.Details) != 1 || result.Details[0].Code != "http_status" {
		t.Fatalf("result = %+v", result)
	}
	if destinationReached.Load() {
		t.Fatal("redirect destination was contacted")
	}
}

func TestAuthorizationHeaderSelectsNetBoxTokenVersion(t *testing.T) {
	tests := []struct {
		name  string
		token string
		want  string
	}{
		{name: "v1", token: "legacy-token", want: "Token legacy-token"},
		{name: "v2", token: "nbt_key.secret", want: "Bearer nbt_key.secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := authorizationHeader(test.token)
			if err != nil {
				t.Fatalf("authorizationHeader() error = %v", err)
			}
			if got != test.want {
				t.Fatalf("authorizationHeader() = %q, want %q", got, test.want)
			}
		})
	}
}

func TestAuthorizationHeaderRejectsMalformedTokensWithoutEchoingThem(t *testing.T) {
	for _, token := range []string{"", "contains whitespace", "nbt_missing_separator", "nbt_.secret", "nbt_key.", "nbt_key.secret\n"} {
		_, err := authorizationHeader(token)
		if err == nil {
			t.Fatalf("authorizationHeader(%q) accepted malformed token", token)
		}
		if strings.Contains(err.Error(), token) && token != "" {
			t.Fatalf("authorizationHeader() leaked malformed token: %v", err)
		}
	}
}

func TestConnectionReportsInvalidTokenFormat(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("network request was attempted with a malformed token")
		return nil, nil
	})}
	result := NewWithClient(client).TestConnection(
		context.Background(),
		connectionRequest("https://netbox.example.test"),
		&staticSecrets{value: "nbt_missing_separator"},
	)

	if result.Succeeded || len(result.Details) != 1 || result.Details[0].Code != "invalid_token_format" || result.Details[0].Retryable {
		t.Fatalf("result = %+v", result)
	}
}

func TestRequestFailureClassification(t *testing.T) {
	tests := []struct {
		name      string
		err       error
		code      string
		retryable bool
	}{
		{name: "deadline", err: context.DeadlineExceeded, code: "request_timeout", retryable: true},
		{name: "dns", err: &url.Error{Op: "Get", URL: "redacted", Err: &net.DNSError{Err: "no such host", Name: "redacted"}}, code: "dns_resolution_failed", retryable: true},
		{name: "tls", err: &url.Error{Op: "Get", URL: "redacted", Err: x509.UnknownAuthorityError{}}, code: "tls_validation_failed", retryable: false},
		{name: "refused", err: &net.OpError{Op: "dial", Net: "tcp", Err: syscall.ECONNREFUSED}, code: "connection_refused", retryable: true},
		{name: "generic", err: errors.New("opaque transport failure"), code: "connection_failed", retryable: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			failure := classifyRequestError(test.err)
			if failure.code != test.code || failure.retryable != test.retryable {
				t.Fatalf("failure = %+v, want code %q retryable %t", failure, test.code, test.retryable)
			}
		})
	}
}

func TestCollectPaginatesAndProducesCompleteCanonicalObservations(t *testing.T) {
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if isReferenceEndpoint(request.URL.Path) {
			response.Header().Set("Content-Type", "application/json")
			json.NewEncoder(response).Encode(map[string]any{"next": nil, "results": []any{}})
			return
		}
		if request.URL.Path != "/api/dcim/regions/" {
			http.NotFound(response, request)
			return
		}
		if request.Header.Get("Authorization") != "Token source-token" {
			t.Error("authorization header was not set")
		}
		response.Header().Set("Content-Type", "application/json")
		if request.URL.Query().Get("offset") == "1" {
			json.NewEncoder(response).Encode(map[string]any{
				"next": nil,
				"results": []any{
					map[string]any{"id": 2, "name": "Child", "slug": "child", "parent": map[string]any{"id": 1}},
				},
			})
			return
		}
		if request.URL.Query().Get("limit") != "1" {
			t.Errorf("limit = %q, want 1", request.URL.Query().Get("limit"))
		}
		json.NewEncoder(response).Encode(map[string]any{
			"next": server.URL + "/api/dcim/regions/?limit=1&offset=1",
			"results": []any{
				map[string]any{"id": 1, "name": "Parent", "slug": "parent", "parent": nil},
			},
		})
	}))
	defer server.Close()

	resolver := &staticSecrets{value: "source-token"}
	request := collectionRequest(server.URL)
	request.Configuration["page_size"] = 1
	batch := NewWithClient(server.Client()).Collect(context.Background(), request, resolver)

	if batch.State != "complete" {
		t.Fatalf("state = %q, messages = %+v", batch.State, batch.Messages)
	}
	if batch.CompletenessToken == "" {
		t.Fatal("complete collection has no completeness token")
	}
	if len(batch.Observations) != 2 {
		t.Fatalf("observation count = %d, want 2", len(batch.Observations))
	}
	if batch.Observations[0].ExternalID != "netbox:region:1" || batch.Observations[1].ExternalID != "netbox:region:2" {
		t.Fatalf("external IDs = %q, %q", batch.Observations[0].ExternalID, batch.Observations[1].ExternalID)
	}
	if len(batch.Observations[1].Relationships) != 1 || batch.Observations[1].Relationships[0].TargetExternalID != "netbox:region:1" {
		t.Fatalf("child relationships = %+v", batch.Observations[1].Relationships)
	}
	encoded, _ := json.Marshal(batch)
	if strings.Contains(string(encoded), "source-token") || strings.Contains(string(encoded), "NETBOX_TOKEN") {
		t.Fatalf("observation batch leaked secret material: %s", encoded)
	}
}

func TestCollectRegionsSitesAndLocationsWithFullCoreFieldCoverage(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		var results []any
		switch request.URL.Path {
		case "/api/extras/tags/":
			results = []any{
				map[string]any{
					"id": 10, "name": "managed", "slug": "managed", "color": "00ff00", "weight": 100,
					"description": "Managed object", "object_types": []any{"dcim.site", "dcim.region"},
				},
				map[string]any{
					"id": 11, "name": "critical", "slug": "critical", "color": "ff0000", "weight": 200,
				},
			}
		case "/api/users/owner-groups/":
			results = []any{map[string]any{"id": 20, "name": "Infrastructure", "description": "Owner group"}}
		case "/api/users/owners/":
			results = []any{map[string]any{
				"id": 21, "name": "Network team", "description": "Network owners", "group": map[string]any{"id": 20},
			}}
		case "/api/tenancy/tenant-groups/":
			results = []any{map[string]any{
				"id": 30, "name": "Internal", "slug": "internal", "description": "Internal tenants",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/tenancy/tenants/":
			results = []any{map[string]any{
				"id": 31, "name": "Internal", "slug": "internal", "group": map[string]any{"id": 30},
				"description": "Internal tenant", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/site-groups/":
			results = []any{map[string]any{
				"id": 40, "name": "Production", "slug": "production", "description": "Production sites",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/ipam/rirs/":
			results = []any{map[string]any{
				"id": 50, "name": "Private", "slug": "private", "is_private": true,
				"description": "Private ASN space", "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/ipam/asns/":
			results = []any{
				map[string]any{
					"id": 51, "asn": 64512, "rir": map[string]any{"id": 50}, "tenant": map[string]any{"id": 31},
					"description": "Primary ASN", "tags": []any{map[string]any{"id": 10}},
				},
				map[string]any{"id": 52, "asn": 64513, "rir": map[string]any{"id": 50}},
			}
		case "/api/dcim/regions/":
			results = []any{map[string]any{
				"id": 1, "name": "World", "slug": "world", "description": "Root", "comments": "Region notes",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/sites/":
			results = []any{map[string]any{
				"id": 2, "name": "Datacenter", "slug": "dc1", "status": map[string]any{"value": "active"},
				"region": map[string]any{"id": 1}, "group": map[string]any{"id": 40},
				"tenant": map[string]any{"id": 31}, "owner": map[string]any{"id": 21},
				"facility": "DC-01", "time_zone": "UTC", "description": "Primary", "comments": "Site notes",
				"physical_address": "1 Main Street", "shipping_address": "Loading dock", "latitude": 1.25,
				"longitude": 2.5, "asns": []any{map[string]any{"id": 52}, map[string]any{"id": 51}},
				"tags": []any{map[string]any{"id": 11}, map[string]any{"id": 10}},
			}}
		case "/api/dcim/locations/":
			results = []any{
				map[string]any{
					"id": 3, "name": "Building", "slug": "building", "site": map[string]any{"id": 2},
					"status": map[string]any{"value": "active"}, "facility": "BLDG-A", "description": "Main building",
					"comments": "Location notes", "tenant": map[string]any{"id": 31},
					"owner": map[string]any{"id": 21},
				},
				map[string]any{
					"id": 4, "name": "Room", "slug": "room", "site": map[string]any{"id": 2},
					"parent": map[string]any{"id": 3}, "status": map[string]any{"value": "active"},
				},
			}
		default:
			http.NotFound(response, request)
			return
		}
		json.NewEncoder(response).Encode(map[string]any{"next": nil, "results": results})
	}))
	defer server.Close()

	request := collectionRequest(server.URL)
	request.Datasets = []string{"locations"}
	batch := NewWithClient(server.Client()).Collect(
		context.Background(),
		request,
		&staticSecrets{value: "source-token"},
	)

	if batch.State != "complete" || len(batch.Observations) != 14 {
		t.Fatalf("batch state = %q, observations = %d, messages = %+v", batch.State, len(batch.Observations), batch.Messages)
	}
	if strings.Join(batch.Datasets, ",") != "references,regions,sites,locations" {
		t.Fatalf("datasets = %v", batch.Datasets)
	}
	site := findObservation(t, batch, "site", "netbox:site:2")
	if site.ResourceKind != "site" || attributeValue(site, "/comments") != "Site notes" {
		t.Fatalf("site = %+v", site)
	}
	if attributeValue(site, "/asns") != nil || attributeValue(site, "/tags") != nil {
		t.Fatalf("site references were emitted as attributes: %+v", site.Attributes)
	}
	if len(site.Relationships) != 8 || site.Relationships[0].Kind != "asn" || site.Relationships[7].Kind != "tenant" {
		t.Fatalf("site relationships = %+v", site.Relationships)
	}
	child := findObservation(t, batch, "location", "netbox:location:4")
	if child.ResourceKind != "location" || len(child.Relationships) != 2 {
		t.Fatalf("child location = %+v", child)
	}
	if child.Relationships[0].Kind != "parent" || child.Relationships[1].Kind != "site" {
		t.Fatalf("child relationships = %+v", child.Relationships)
	}
}

func TestCollectDeviceCatalogAndRacksWithFullCoreFieldCoverage(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		var results []any
		switch request.URL.Path {
		case "/api/dcim/manufacturers/":
			results = []any{map[string]any{
				"id": 100, "name": "Acme", "slug": "acme", "description": "Hardware maker",
				"comments": "Manufacturer notes", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/device-roles/":
			results = []any{map[string]any{
				"id": 101, "name": "Leaf", "slug": "leaf", "color": "2196f3", "vm_role": false,
				"config_template": map[string]any{"id": 900, "name": "Network OS"},
				"description":     "Leaf switches", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/platforms/":
			results = []any{map[string]any{
				"id": 102, "name": "Acme OS", "slug": "acme-os", "manufacturer": map[string]any{"id": 100},
				"config_template": map[string]any{"id": 900, "name": "Network OS"},
				"description":     "Switch software", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/device-types/":
			results = []any{map[string]any{
				"id": 103, "manufacturer": map[string]any{"id": 100},
				"default_platform": map[string]any{"id": 102}, "model": "Switch 48", "slug": "switch-48",
				"part_number": "SW48", "u_height": "1.0", "exclude_from_utilization": false,
				"is_full_depth": true, "subdevice_role": map[string]any{"value": "parent"},
				"airflow": map[string]any{"value": "front-to-rear"}, "weight": "7.50",
				"weight_unit": map[string]any{"value": "kg"}, "description": "48-port switch",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/rack-groups/":
			results = []any{map[string]any{
				"id": 104, "name": "Row A", "slug": "row-a", "description": "First row",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/rack-roles/":
			results = []any{map[string]any{
				"id": 105, "name": "Compute", "slug": "compute", "color": "9c27b0",
				"description": "Compute racks", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/rack-types/":
			results = []any{map[string]any{
				"id": 106, "manufacturer": map[string]any{"id": 100}, "model": "R42", "slug": "r42",
				"form_factor": map[string]any{"value": "4-post-cabinet"}, "width": map[string]any{"value": 19},
				"u_height": 42, "starting_unit": 1, "desc_units": false, "outer_width": 600,
				"outer_height": 2000, "outer_depth": 1200, "outer_unit": map[string]any{"value": "mm"},
				"weight": "100.25", "max_weight": 1500, "weight_unit": map[string]any{"value": "kg"},
				"mounting_depth": 1000, "description": "Standard rack", "owner": map[string]any{"id": 21},
				"tags": []any{map[string]any{"id": 10}},
			}}
		case "/api/dcim/racks/":
			results = []any{map[string]any{
				"id": 107, "name": "A01", "facility_id": "DC-A01", "site": map[string]any{"id": 2},
				"location": map[string]any{"id": 4}, "group": map[string]any{"id": 104},
				"tenant": map[string]any{"id": 31}, "status": map[string]any{"value": "active"},
				"role": map[string]any{"id": 105}, "serial": "RACK-SERIAL", "asset_tag": "RACK-ASSET",
				"rack_type": map[string]any{"id": 106}, "form_factor": map[string]any{"value": "4-post-cabinet"},
				"width": map[string]any{"value": 19}, "u_height": 42, "starting_unit": 1, "desc_units": false,
				"outer_width": 600, "outer_height": 2000, "outer_depth": 1200,
				"outer_unit": map[string]any{"value": "mm"}, "mounting_depth": 1000,
				"airflow": map[string]any{"value": "front-to-rear"}, "weight": "100.25", "max_weight": 1500,
				"weight_unit": map[string]any{"value": "kg"}, "description": "Primary rack",
				"owner": map[string]any{"id": 21}, "tags": []any{map[string]any{"id": 10}},
			}}
		default:
			results = []any{}
		}
		json.NewEncoder(response).Encode(map[string]any{"next": nil, "results": results})
	}))
	defer server.Close()

	request := collectionRequest(server.URL)
	request.Datasets = []string{"racks"}
	batch := NewWithClient(server.Client()).Collect(
		context.Background(),
		request,
		&staticSecrets{value: "source-token"},
	)

	if batch.State != "complete" || len(batch.Observations) != 8 {
		t.Fatalf("batch state = %q, observations = %d, messages = %+v", batch.State, len(batch.Observations), batch.Messages)
	}
	if strings.Join(batch.Datasets, ",") != "references,extras_templates,regions,sites,locations,device_catalog,racks" {
		t.Fatalf("datasets = %v", batch.Datasets)
	}
	deviceType := findObservation(t, batch, "device_type", "netbox:device_type:103")
	if attributeValue(deviceType, "/u_height") != 1.0 || attributeValue(deviceType, "/weight") != 7.5 {
		t.Fatalf("device type numeric attributes = %+v", deviceType.Attributes)
	}
	if len(deviceType.Relationships) != 4 || deviceType.Relationships[0].Kind != "default_platform" {
		t.Fatalf("device type relationships = %+v", deviceType.Relationships)
	}
	rackType := findObservation(t, batch, "rack_type", "netbox:rack_type:106")
	if attributeValue(rackType, "/form_factor") != "4-post-cabinet" || attributeValue(rackType, "/weight") != 100.25 {
		t.Fatalf("rack type attributes = %+v", rackType.Attributes)
	}
	rack := findObservation(t, batch, "rack", "netbox:rack:107")
	if attributeValue(rack, "/status") != "active" || attributeValue(rack, "/width") != json.Number("19") {
		t.Fatalf("rack attributes = %+v", rack.Attributes)
	}
	if len(rack.Relationships) != 8 || rack.Relationships[0].Kind != "group" || rack.Relationships[7].Kind != "tenant" {
		t.Fatalf("rack relationships = %+v", rack.Relationships)
	}
}

func TestCollectRejectsCrossOriginPaginationAsPartial(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(map[string]any{
			"next": "https://evil.example/api/dcim/regions/?offset=1",
			"results": []any{
				map[string]any{"id": 1, "name": "Parent", "slug": "parent"},
			},
		})
	}))
	defer server.Close()

	batch := NewWithClient(server.Client()).Collect(
		context.Background(),
		collectionRequest(server.URL),
		&staticSecrets{value: "source-token"},
	)

	if batch.State != "partial" || batch.CompletenessToken != "" {
		t.Fatalf("state = %q, completeness token = %q", batch.State, batch.CompletenessToken)
	}
	if len(batch.Messages) != 1 || batch.Messages[0].Code != "unsafe_pagination_url" {
		t.Fatalf("messages = %+v", batch.Messages)
	}
}

func TestCollectRefusesUnsupportedScopeBeforeNetworkAccess(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		t.Fatal("network request was attempted for unsupported scope")
		return nil, nil
	})}
	request := collectionRequest("https://netbox.example.test")
	request.Scope = []contracts.ScopeDimension{{Name: "site", Value: "home"}}

	batch := NewWithClient(client).Collect(context.Background(), request, &staticSecrets{value: "source-token"})

	if batch.State != "failed" || len(batch.Messages) != 1 || batch.Messages[0].Code != "unsupported_scope" {
		t.Fatalf("batch = %+v", batch)
	}
}

func TestCollectLetsSourceNetBoxApplyItsConfiguredPageSizeLimit(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Query().Get("limit") != "1000" {
			t.Errorf("limit = %q, want 1000", request.URL.Query().Get("limit"))
		}
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(map[string]any{"next": nil, "results": []any{}})
	}))
	defer server.Close()
	request := collectionRequest(server.URL)
	request.Configuration["page_size"] = 1000
	batch := NewWithClient(server.Client()).Collect(
		context.Background(), request, &staticSecrets{value: "source-token"},
	)

	if batch.State != "complete" {
		t.Fatalf("batch = %+v", batch)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func attributeValue(observation contracts.Observation, path string) any {
	for _, attribute := range observation.Attributes {
		if attribute.Path == path {
			return attribute.Value
		}
	}
	return nil
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("json.Marshal() error = %v", err)
	}
	return encoded
}

func findObservation(
	t *testing.T,
	batch contracts.ObservationBatch,
	resourceKind string,
	externalID string,
) contracts.Observation {
	t.Helper()
	for _, observation := range batch.Observations {
		if observation.ResourceKind == resourceKind && observation.ExternalID == externalID {
			return observation
		}
	}
	t.Fatalf("observation %s %s was not collected", resourceKind, externalID)
	return contracts.Observation{}
}

func isReferenceEndpoint(path string) bool {
	for _, candidate := range []string{
		"/api/extras/tags/",
		"/api/users/owner-groups/",
		"/api/users/owners/",
		"/api/tenancy/tenant-groups/",
		"/api/tenancy/tenants/",
		"/api/dcim/site-groups/",
		"/api/ipam/rirs/",
		"/api/ipam/asns/",
	} {
		if path == candidate {
			return true
		}
	}
	return false
}

func connectionRequest(baseURL string) contracts.ConnectionTestRequest {
	return contracts.ConnectionTestRequest{
		SourceID:      "00000000-0000-0000-0000-000000000001",
		ProviderID:    "netbox",
		ExecutionMode: "agent",
		Configuration: map[string]any{
			"base_url":        baseURL,
			"token_ref":       "env://NETBOX_TOKEN",
			"verify_tls":      true,
			"page_size":       500,
			"timeout_seconds": 30,
		},
	}
}

func collectionRequest(baseURL string) contracts.CollectionRequest {
	connection := connectionRequest(baseURL)
	return contracts.CollectionRequest{
		RunID:         "00000000-0000-0000-0000-000000000002",
		SourceID:      connection.SourceID,
		ProviderID:    connection.ProviderID,
		ExecutionMode: connection.ExecutionMode,
		Datasets:      []string{"regions"},
		Scope:         []contracts.ScopeDimension{},
		Configuration: connection.Configuration,
	}
}
