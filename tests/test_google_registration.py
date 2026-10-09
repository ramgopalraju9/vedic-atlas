"""The six Gmail and Calendar tools must all register at startup (a NameError here once left only gmail_read/gmail_search)."""

from types import SimpleNamespace

import server
from service.skills.registry import SkillRegistry
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore


def test_all_google_tools_register_with_the_contact_book():
    online = SimpleNamespace(
        allowlist=["oauth2.googleapis.com", "gmail.googleapis.com", "www.googleapis.com"],
        google_client_id_env="X_TEST_ID", google_client_secret_env="X_TEST_SECRET", google_refresh_token_env="X_TEST_TOKEN",
    )
    manifests = {m.name: m for m in YamlToolManifestStore().load_all()}
    registry = SkillRegistry()
    auth, calendar = server._register_google_tools(online, manifests, registry)
    assert auth is not None and calendar is not None
    for name in server._GOOGLE_TOOLS:
        assert registry.get(name) is not None, f"{name} was not registered"
