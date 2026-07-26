"""Tests for the filter form (step 22).

Counts are checked against the equivalent API call throughout: the form's whole reason to
reuse `apply_filters` is that `?origin=crm_only` must mean one thing, and only a test that
compares the two would notice if it stopped.
"""

import re

from fastapi.testclient import TestClient


def _rows(html: str) -> list[str]:
    if "<tbody>" not in html:
        return []
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [row for row in body.split("<tr>") if row.strip()]


def _api_count(client: TestClient, params: dict) -> int:
    return len(client.get("/api/meetings", params=params).json())


# --- the form renders ---


def test_the_form_is_a_plain_get(client: TestClient) -> None:
    """The URL is the state: bookmarkable, reloadable, and the back button works."""
    html = client.get("/").text

    assert 'method="get"' in html
    assert "<form" in html


def test_every_api_parameter_has_a_control(client: TestClient) -> None:
    """Step 18's five parameters, mirrored one for one."""
    html = client.get("/").text

    for name in ("origin", "has_conflicts", "date_from", "date_to", "owner"):
        assert f'name="{name}"' in html, name


def test_an_untouched_form_returns_everything(client: TestClient) -> None:
    """A submitted form sends every field, including the blank ones. Treating `?origin=` as
    "match the empty string" would return nothing the first time anyone pressed Filter."""
    response = client.get("/", params={
        "origin": "", "has_conflicts": "", "date_from": "", "date_to": "", "owner": ""
    })

    assert len(_rows(response.text)) == 24


# --- each control filters ---


def test_origin_filter_matches_the_api(client: TestClient) -> None:
    for origin, expected in (("both", 17), ("crm_only", 3), ("calendar_only", 4)):
        rows = _rows(client.get("/", params={"origin": origin}).text)

        assert len(rows) == expected == _api_count(client, {"origin": origin}), origin


def test_conflicts_filter_matches_the_api(client: TestClient) -> None:
    rows = _rows(client.get("/", params={"has_conflicts": "true"}).text)

    assert len(rows) == 4 == _api_count(client, {"has_conflicts": "true"})
    assert all("badge-conflict" in row for row in rows)


def test_no_conflicts_is_distinct_from_any(client: TestClient) -> None:
    """The tri-state a checkbox cannot express: without it, "meetings where the sources
    agree" is unaskable."""
    none = _rows(client.get("/", params={"has_conflicts": "false"}).text)
    any_ = _rows(client.get("/", params={"has_conflicts": ""}).text)

    assert len(none) == 20
    assert len(any_) == 24
    assert not any("badge-conflict" in row for row in none)


def test_owner_filter_matches_the_api(client: TestClient) -> None:
    """14, not 11 — the calendar-only meetings Sarah organised are included (step 18)."""
    rows = _rows(client.get("/", params={"owner": "sarah"}).text)

    assert len(rows) == 14 == _api_count(client, {"owner": "sarah"})


def test_date_filters_narrow_the_list(client: TestClient) -> None:
    params = {"date_from": "2025-03-17", "date_to": "2025-03-19"}
    rows = _rows(client.get("/", params=params).text)

    assert len(rows) == _api_count(client, params)
    assert all(re.search(r"2025-03-1[789]", row) for row in rows)


def test_filters_combine(client: TestClient) -> None:
    params = {"owner": "sarah", "origin": "calendar_only"}
    rows = _rows(client.get("/", params=params).text)

    assert len(rows) == 3 == _api_count(client, params)


# --- selections persist ---


def test_the_selected_origin_is_marked(client: TestClient) -> None:
    """A form that filters but comes back blank hides what was asked for, and silently drops
    it on the next submission."""
    html = client.get("/", params={"origin": "crm_only"}).text
    option = re.search(r'<option value="crm_only"[^>]*>', html)

    assert option is not None
    assert "selected" in option.group(0)


def test_the_unselected_origins_are_not_marked(client: TestClient) -> None:
    html = client.get("/", params={"origin": "crm_only"}).text

    assert "selected" in re.search(r'<option value="crm_only"[^>]*>', html).group(0)
    assert "selected" not in re.search(r'<option value="both"[^>]*>', html).group(0)


def test_the_owner_query_is_echoed(client: TestClient) -> None:
    html = client.get("/", params={"owner": "sarah"}).text

    assert 'name="owner"' in html
    assert 'value="sarah"' in html


def test_the_dates_are_echoed(client: TestClient) -> None:
    html = client.get("/", params={"date_from": "2025-03-17", "date_to": "2025-03-19"}).text

    assert 'value="2025-03-17"' in html
    assert 'value="2025-03-19"' in html


def test_the_conflicts_selection_persists(client: TestClient) -> None:
    html = client.get("/", params={"has_conflicts": "false"}).text
    option = re.search(r'<option value="false"[^>]*>', html)

    assert "selected" in option.group(0)


# --- feedback ---


def test_the_count_reflects_the_filtered_total(client: TestClient) -> None:
    """Always reporting 24 would make the page look broken when it is working."""
    html = client.get("/", params={"origin": "crm_only"}).text

    assert "3 of 24 meetings match" in html


def test_an_empty_result_explains_itself(client: TestClient) -> None:
    """A bare table with no rows reads as a bug; a sentence reads as an answer."""
    html = client.get("/", params={"date_from": "2030-01-01"}).text

    assert _rows(html) == []
    assert "No meetings match these filters" in html
    assert "<tbody>" not in html


def test_a_clear_link_appears_only_when_filtered(client: TestClient) -> None:
    unfiltered = client.get("/").text
    filtered = client.get("/", params={"origin": "crm_only"}).text

    assert ">Clear<" not in unfiltered
    assert ">Clear<" in filtered


# --- the page must survive whatever arrives in the query string ---


def test_a_nonsense_origin_is_ignored_rather_than_a_422(client: TestClient) -> None:
    """The API is stricter on purpose: there a typo is a programming error worth reporting.
    Here it is a hand-edited URL, and an error page helps nobody."""
    response = client.get("/", params={"origin": "banana"})

    assert response.status_code == 200
    assert len(_rows(response.text)) == 24


def test_a_malformed_date_is_ignored(client: TestClient) -> None:
    response = client.get("/", params={"date_from": "last tuesday"})

    assert response.status_code == 200
    assert len(_rows(response.text)) == 24


def test_a_nonsense_conflicts_value_is_ignored(client: TestClient) -> None:
    response = client.get("/", params={"has_conflicts": "perhaps"})

    assert response.status_code == 200
    assert len(_rows(response.text)) == 24


def test_the_owner_query_is_escaped(client: TestClient) -> None:
    """The echoed value goes straight back into an attribute."""
    html = client.get("/", params={"owner": '"><script>alert(1)</script>'}).text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html or "&#34;&gt;&lt;script&gt;" in html
