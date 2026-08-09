"""Guard against the *real* upstream payload shapes.

Captured from live `notebooklm source list --json` and `notebooklm list --json`
runs (v0.7.3). The real wire format differs from the idealized one in ways that are
easy to get wrong:

* the source type field is ``type``, **not** ``kind``;
* ``status`` is lowercase (``"ready"``), not ``"READY"``;
* rows carry extra fields (``index``, ``status_id``) we ignore;
* sources are nested under a ``sources`` key alongside notebook metadata;
* ``list --json`` nests notebooks under ``notebooks`` alongside a ``count``, and its
  rows carry an ``index`` the upstream Python model does not have.

`source_list.json` is scrubbed of nothing sensitive — it references only example.com
and a public RFC. `notebook_list.json` keeps the real shape but its ids and titles
are placeholders, since the live ones are the user's private notebooks. See the
test-fixtures skill before capturing new ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notebooklm_sync.nlm import source_from_payload

FIXTURE = Path(__file__).parent / "fixtures" / "source_list.json"
NOTEBOOKS_FIXTURE = Path(__file__).parent / "fixtures" / "notebook_list.json"


@pytest.fixture
def live_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def live_notebooks() -> dict:
    return json.loads(NOTEBOOKS_FIXTURE.read_text(encoding="utf-8"))


def test_type_field_populates_kind(live_payload):
    # Upstream sends "type"; our model exposes it as `kind`.
    source = source_from_payload(live_payload["sources"][0])
    assert source.kind == "web_page"


def test_real_web_page_is_refreshable(live_payload):
    # This is what makes the `override` policy work at all.
    for row in live_payload["sources"]:
        assert source_from_payload(row).is_refreshable


def test_core_fields_survive_the_real_shape(live_payload):
    source = source_from_payload(live_payload["sources"][0])
    assert source.id == "6240829a-e30d-49d2-bd47-2d89879336de"
    assert source.url == "https://example.com/"
    assert source.title == "Example Domain"
    assert source.created_at is not None


def test_lowercase_status_is_preserved(live_payload):
    assert source_from_payload(live_payload["sources"][0]).status == "ready"


def test_extra_unknown_fields_are_ignored(live_payload):
    # index/status_id exist on the wire; parsing must not choke on them.
    row = dict(live_payload["sources"][0], future_field="whatever")
    assert source_from_payload(row).id


# -- notebooklm list --json ---------------------------------------------------


def test_notebook_rows_are_nested_under_notebooks(live_notebooks, fake_cli):
    from notebooklm_sync.nlm import NlmClient

    fake_cli.scenario({"list": live_notebooks})
    rows = NlmClient().list_notebooks()
    assert len(rows) == 2


def test_notebook_id_key_is_plain_id(live_notebooks):
    # The health check matches NOTEBOOK_*_ID against this key.
    assert live_notebooks["notebooks"][0]["id"] == "11111111-2222-4333-8444-555555555555"


def test_bare_list_shape_is_also_accepted(live_notebooks, fake_cli):
    # Defensive: upstream could drop the envelope. Unwrapping must not be required.
    from notebooklm_sync.nlm import NlmClient

    fake_cli.scenario({"list": live_notebooks["notebooks"]})
    assert len(NlmClient().list_notebooks()) == 2
