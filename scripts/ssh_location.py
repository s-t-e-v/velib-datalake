#!/usr/bin/env python3
"""Select a trusted SSH source location locally and in Azure."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "infra" / "ssh-locations.local.json"
PARAMETERS_PATH = ROOT / "infra" / "dev.local.parameters.json"
PARAMETERS_EXAMPLE_PATH = ROOT / "infra" / "dev.local.parameters.json.example"
LOCK_RULE_NAME = "AllowSshFromCaller"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class LocationError(RuntimeError):
    """Raised for an invalid location or an unsuccessful update."""


def display_path(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def normalize_cidr(value: str) -> str:
    candidate = value.strip()
    if "/" not in candidate:
        candidate = f"{candidate}/32"
    try:
        network = ipaddress.ip_network(candidate, strict=True)
    except ValueError as exc:
        raise LocationError(f"Invalid IPv4 address or CIDR: {value!r}") from exc
    if network.version != 4 or network.prefixlen != 32:
        raise LocationError("SSH access must use one exact public IPv4 address with /32")
    return str(network)


def validate_name(name: str) -> str:
    value = name.strip()
    if not NAME_PATTERN.fullmatch(value):
        raise LocationError(
            "Location names must start with a letter or digit and contain only letters, "
            "digits, underscores, or hyphens"
        )
    return value


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{\n  "locations": {}\n}\n', encoding="utf-8")
        print(f"Created {display_path(path)}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocationError(f"Could not read {path}: {exc}") from exc
    locations = payload.get("locations")
    if not isinstance(locations, dict):
        raise LocationError(f"{path} must contain a JSON object named 'locations'")
    result: dict[str, str] = {}
    for name, cidr in locations.items():
        if not isinstance(name, str) or not isinstance(cidr, str):
            raise LocationError(f"Every location in {path} must map a name to a CIDR string")
        result[validate_name(name)] = normalize_cidr(cidr)
    return result


def save_registry(locations: dict[str, str], path: Path = REGISTRY_PATH) -> None:
    payload = {"locations": locations}
    atomic_write(path, json.dumps(payload, indent=2) + "\n")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
    temporary.replace(path)


def current_parameter_cidr(path: Path = PARAMETERS_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload["parameters"]["sshAllowedCidr"]["value"]
        return normalize_cidr(value)
    except (KeyError, TypeError, OSError, json.JSONDecodeError, LocationError):
        return None


def update_parameters(cidr: str, path: Path = PARAMETERS_PATH) -> None:
    if not path.exists():
        if not PARAMETERS_EXAMPLE_PATH.exists():
            raise LocationError(f"Missing {path} and its tracked example")
        shutil.copyfile(PARAMETERS_EXAMPLE_PATH, path)
        print(f"Created {display_path(path)} from the tracked example")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["parameters"]["sshAllowedCidr"]["value"] = cidr
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise LocationError(f"Could not update {path}: {exc}") from exc
    atomic_write(path, json.dumps(payload, indent=2) + "\n")


def retrieve_public_ipv4() -> str:
    request = urllib.request.Request(
        "https://api.ipify.org", headers={"User-Agent": "velib-datalake-ssh-location/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return normalize_cidr(response.read().decode("ascii"))
    except (OSError, UnicodeError) as exc:
        raise LocationError(f"Could not retrieve the public IPv4 address: {exc}") from exc


def run_az(arguments: Sequence[str]) -> Any:
    if shutil.which("az") is None:
        raise LocationError("Azure CLI 'az' is not installed")
    command = ["az", *arguments, "--only-show-errors", "--output", "json"]
    try:
        completed = subprocess.run(command, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or f"exit status {exc.returncode}"
        raise LocationError(f"Azure CLI failed: {message}") from exc
    return json.loads(completed.stdout) if completed.stdout.strip() else None


def deployment_resource_group(deployment: str, runner: Callable[[Sequence[str]], Any]) -> str:
    outputs = runner(
        ["deployment", "sub", "show", "--name", deployment, "--query", "properties.outputs"]
    )
    if not isinstance(outputs, dict):
        raise LocationError(f"Deployment {deployment!r} did not return outputs")
    normalized = {name.lower(): item for name, item in outputs.items()}
    try:
        resource_group = normalized["resource_group"]["value"]
    except (KeyError, TypeError) as exc:
        raise LocationError(f"Deployment {deployment!r} has no RESOURCE_GROUP output") from exc
    if not isinstance(resource_group, str) or not resource_group:
        raise LocationError(f"Deployment {deployment!r} returned an invalid resource group")
    return resource_group


def collector_nsg(
    resource_group: str, runner: Callable[[Sequence[str]], Any]
) -> str:
    groups = runner(["network", "nsg", "list", "--resource-group", resource_group])
    matches = []
    for group in groups if isinstance(groups, list) else []:
        rules = group.get("securityRules", []) if isinstance(group, dict) else []
        if any(rule.get("name") == LOCK_RULE_NAME for rule in rules):
            matches.append(group.get("name"))
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise LocationError(
            f"Expected exactly one NSG with rule {LOCK_RULE_NAME!r} in {resource_group!r}"
        )
    return matches[0]


def update_azure(
    cidr: str,
    deployment: str,
    runner: Callable[[Sequence[str]], Any] = run_az,
) -> tuple[str, str]:
    resource_group = deployment_resource_group(deployment, runner)
    nsg = collector_nsg(resource_group, runner)
    runner(
        [
            "network",
            "nsg",
            "rule",
            "update",
            "--resource-group",
            resource_group,
            "--nsg-name",
            nsg,
            "--name",
            LOCK_RULE_NAME,
            "--source-address-prefixes",
            cidr,
        ]
    )
    return resource_group, nsg


def resolve_selector(selector: str, locations: dict[str, str]) -> tuple[str | None, str]:
    if selector.isdigit():
        index = int(selector)
        items = list(locations.items())
        if 1 <= index <= len(items):
            return items[index - 1]
        raise LocationError(f"Location index must be between 1 and {len(items)}")
    for name, cidr in locations.items():
        if name.casefold() == selector.casefold():
            return name, cidr
    try:
        return None, normalize_cidr(selector)
    except LocationError as exc:
        raise LocationError(f"Unknown location name, index, or IP address: {selector!r}") from exc


def next_location_name(locations: dict[str, str]) -> str:
    index = 1
    while f"location-{index}" in locations:
        index += 1
    return f"location-{index}"


def prompt_name(locations: dict[str, str]) -> str:
    suggested = next_location_name(locations)
    entered = input(f"Location name [{suggested}]: ").strip()
    name = validate_name(entered or suggested)
    if any(existing.casefold() == name.casefold() for existing in locations):
        replace = input(f"Location {name!r} exists. Replace it? [y/N]: ").strip().lower()
        if replace not in {"y", "yes"}:
            raise LocationError("Location was not changed")
    return name


def interactive_choice(locations: dict[str, str]) -> tuple[str | None, str] | None:
    active = current_parameter_cidr()
    if locations:
        print("\nSaved SSH locations:")
        for index, (name, cidr) in enumerate(locations.items(), start=1):
            suffix = " (active locally)" if cidr == active else ""
            print(f"  {index}. {name}: {cidr}{suffix}")
    else:
        print("\nNo SSH locations are registered yet.")
    print("\n  a. Retrieve and add this location's public IP")
    print("  m. Manually add an IP address")
    print("  q. Quit")
    choice = input("\nChoose a location by index/name, or select an action: ").strip()
    if choice.casefold() == "q":
        return None
    if choice.casefold() == "a":
        cidr = retrieve_public_ipv4()
        print(f"Detected public address: {cidr}")
        name = prompt_name(locations)
        locations[name] = cidr
        save_registry(locations)
        use_now = input("Use this location now? [Y/n]: ").strip().lower()
        return (name, cidr) if use_now not in {"n", "no"} else None
    if choice.casefold() == "m":
        cidr = normalize_cidr(input("Public IPv4 address: "))
        name = prompt_name(locations)
        locations[name] = cidr
        save_registry(locations)
        use_now = input("Use this location now? [Y/n]: ").strip().lower()
        return (name, cidr) if use_now not in {"n", "no"} else None
    return resolve_selector(choice, locations)


def activate(cidr: str, *, deployment: str, local_only: bool) -> None:
    update_parameters(cidr)
    print(f"Updated {display_path(PARAMETERS_PATH)}: sshAllowedCidr={cidr}")
    if local_only:
        print("Skipped Azure update (--local-only).")
        return
    resource_group, nsg = update_azure(cidr, deployment)
    print(f"Updated Azure NSG {resource_group}/{nsg}: {LOCK_RULE_NAME}={cidr}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Select a saved SSH location or exact public IPv4 address."
    )
    value.add_argument("selector", nargs="?", help="saved name, 1-based index, or IPv4 address")
    value.add_argument(
        "--add-current",
        metavar="NAME",
        help="retrieve the current public IPv4, save it under NAME, and activate it",
    )
    value.add_argument(
        "--save",
        nargs=2,
        metavar=("NAME", "IP"),
        help="save NAME with IP (or IP/32) and activate it",
    )
    value.add_argument("--deployment", default="velib-dev", help="subscription deployment name")
    value.add_argument(
        "--local-only", action="store_true", help="update local parameters but not Azure"
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        locations = load_registry()
        selected: tuple[str | None, str] | None
        if args.add_current:
            name = validate_name(args.add_current)
            cidr = retrieve_public_ipv4()
            locations[name] = cidr
            save_registry(locations)
            selected = name, cidr
        elif args.save:
            name = validate_name(args.save[0])
            cidr = normalize_cidr(args.save[1])
            locations[name] = cidr
            save_registry(locations)
            selected = name, cidr
        elif args.selector:
            selected = resolve_selector(args.selector, locations)
        else:
            selected = interactive_choice(locations)
        if selected is None:
            print("No location was activated.")
            return 0
        name, cidr = selected
        activate(cidr, deployment=args.deployment, local_only=args.local_only)
        print(f"Active location: {name or 'direct IP'} ({cidr})")
        return 0
    except (LocationError, EOFError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
