from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "ssh_location.py"
    spec = importlib.util.spec_from_file_location("ssh_location", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ssh_location = load_script()


def test_normalize_cidr_accepts_one_exact_ipv4() -> None:
    assert ssh_location.normalize_cidr("203.0.113.10") == "203.0.113.10/32"
    assert ssh_location.normalize_cidr("203.0.113.10/32") == "203.0.113.10/32"

    with pytest.raises(ssh_location.LocationError, match="exact public IPv4"):
        ssh_location.normalize_cidr("203.0.113.0/24")


def test_registry_is_created_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "ssh-locations.local.json"

    assert ssh_location.load_registry(path) == {}
    ssh_location.save_registry({"home": "203.0.113.10/32"}, path)

    assert ssh_location.load_registry(path) == {"home": "203.0.113.10/32"}


def test_selector_accepts_name_index_and_direct_ip() -> None:
    locations = {"home": "203.0.113.10/32", "office": "198.51.100.20/32"}

    assert ssh_location.resolve_selector("home", locations) == ("home", "203.0.113.10/32")
    assert ssh_location.resolve_selector("2", locations) == ("office", "198.51.100.20/32")
    assert ssh_location.resolve_selector("192.0.2.30", locations) == (None, "192.0.2.30/32")


def test_update_parameters_changes_only_active_cidr(tmp_path: Path) -> None:
    path = tmp_path / "dev.local.parameters.json"
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    "environmentName": {"value": "dev"},
                    "sshAllowedCidr": {"value": "203.0.113.10/32"},
                }
            }
        ),
        encoding="utf-8",
    )

    ssh_location.update_parameters("198.51.100.20/32", path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["parameters"] == {
        "environmentName": {"value": "dev"},
        "sshAllowedCidr": {"value": "198.51.100.20/32"},
    }


def test_update_azure_discovers_deployment_and_nsg() -> None:
    calls = []

    def runner(arguments):  # type: ignore[no-untyped-def]
        calls.append(arguments)
        if arguments[:3] == ["deployment", "sub", "show"]:
            return {"resourcE_GROUP": {"value": "rg-velib-dev"}}
        if arguments[:3] == ["network", "nsg", "list"]:
            return [
                {
                    "name": "nsg-velib-token",
                    "securityRules": [{"name": "AllowSshFromCaller"}],
                }
            ]
        return {}

    result = ssh_location.update_azure("203.0.113.10/32", "velib-dev", runner)

    assert result == ("rg-velib-dev", "nsg-velib-token")
    assert calls[-1][-2:] == ["--source-address-prefixes", "203.0.113.10/32"]
