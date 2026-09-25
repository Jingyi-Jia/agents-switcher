from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests import conftest


@pytest.mark.parametrize("fixtures,keychain,groups", [
    ([], False, []),
    (["monkeypatch", "node"], False, ["native-codex-processes"]),
    (["monkeypatch"], True, ["real-keychain"]),
])
def test_native_resource_groups_preserve_parallelism_for_unrelated_tests(fixtures, keychain, groups):
    item = SimpleNamespace(
        fixturenames=fixtures,
        get_closest_marker=lambda name: keychain if name == "no_keychain_fake" else None,
        add_marker=Mock(),
    )
    conftest.pytest_collection_modifyitems([item])
    assert [call.args[0].name for call in item.add_marker.call_args_list] == ["xdist_group"] * len(groups)
    assert [call.args[0].args for call in item.add_marker.call_args_list] == [(group,) for group in groups]
