from netbox_ssot.comparison_presentation import comparison_field_rows, field_label, format_comparison_value


def test_comparison_rows_present_exact_matches_without_raw_json() -> None:
    data = {
        "attributes": {"/asn": 65001, "/active": True},
        "relationships": {"rir": '["rir",["slug","rfc-6996"]]'},
    }

    rows = comparison_field_rows(data, data, target_exists=True)

    assert [(row.category, row.label, row.status) for row in rows] == [
        ("attributes", "Active", "Matches"),
        ("attributes", "ASN", "Matches"),
        ("relationships", "RIR", "Matches"),
    ]
    assert rows[-1].provider_value == "RIR · rfc-6996"
    assert all(not row.changed for row in rows)


def test_comparison_rows_explain_add_change_and_remove() -> None:
    source = {"attributes": {"/name": "New name", "/description": "Added"}}
    target = {"attributes": {"/name": "Old name", "/comments": "Remove me"}}

    rows = comparison_field_rows(source, target, target_exists=True)

    assert {row.label: row.status for row in rows} == {
        "Comments": "Remove",
        "Description": "Add",
        "Name": "Change",
    }


def test_new_record_fields_are_marked_new() -> None:
    rows = comparison_field_rows({"attributes": {"/name": "New"}}, {}, target_exists=False, action="create")

    assert len(rows) == 1
    assert rows[0].status == "New"
    assert not rows[0].local_present


def test_unapplied_conflict_fields_are_observations_not_creates() -> None:
    rows = comparison_field_rows({"attributes": {"/name": "Ambiguous"}}, {}, target_exists=False, action="conflict")

    assert rows[0].status == "Observed"
    assert rows[0].status_color == "secondary"


def test_value_formatting_humanizes_common_types() -> None:
    assert field_label("/asn") == "ASN"
    assert format_comparison_value(True) == "Yes"
    assert format_comparison_value(["one", "two"]) == "one, two"
    assert format_comparison_value('["asn","asn",65001]', relationship=True) == "ASN · 65001"
