"""Remote deployment is explicit, TLS-only, and credential-safe on rejection."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.test_remote_control_access import TOKEN


@pytest.mark.parametrize(
    "changes",
    [
        {"JARVIS_REMOTE_OPERATOR_ID": ""},
        {"JARVIS_REMOTE_OPERATOR_ID": " operator "},
        {"JARVIS_REMOTE_OPERATOR_TOKEN": "short"},
        {"JARVIS_REMOTE_ORIGIN": "http://remote.test:8443"},
        {"JARVIS_REMOTE_ORIGIN": "https://user:password@remote.test"},
        {"JARVIS_REMOTE_ORIGIN": "https://remote.test/path"},
        {"JARVIS_REMOTE_ORIGIN": "https://remote.test?token=bad"},
        {"JARVIS_REMOTE_ORIGIN": "https://remote.test:0"},
        {"JARVIS_REMOTE_ORIGIN": "https://remote.test:99999"},
        {"JARVIS_DATABASE_URL": "postgresql://invalid/remote"},
    ],
)
def test_remote_configuration_rejects_unsafe_transport_and_masks_secrets(changes):
    values = (
        dict(
            JARVIS_REMOTE_CONTROL_ENABLED=True,
            JARVIS_REMOTE_OPERATOR_ID="configured-operator",
            JARVIS_REMOTE_OPERATOR_TOKEN=TOKEN,
            JARVIS_REMOTE_ORIGIN="https://remote.test:8443",
        )
        | changes
    )
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, **values)
    assert TOKEN not in str(failure.value)


def test_remote_configuration_is_disabled_by_default_and_preserves_secret_masking():
    local = Settings(_env_file=None)
    assert not local.remote_control_enabled
    remote = Settings(
        _env_file=None,
        JARVIS_REMOTE_CONTROL_ENABLED=True,
        JARVIS_REMOTE_OPERATOR_ID="configured-operator",
        JARVIS_REMOTE_OPERATOR_TOKEN=TOKEN,
        JARVIS_REMOTE_ORIGIN="https://remote.test:8443",
    )
    assert remote.remote_control_enabled and TOKEN not in repr(remote)
