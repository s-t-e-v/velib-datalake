from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    tools = tmp_path / "bin"
    tools.mkdir()
    stub = tools / "stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, tarfile\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "with open(os.environ['CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
        "if name == 'az':\n"
        "    print(os.environ['DEPLOYMENT_OUTPUTS'])\n"
        "elif name == 'ssh':\n"
        "    if os.getenv('SSH_FAIL'):\n"
        "        sys.exit(255)\n"
        "    if 'mktemp' in sys.argv[-1]:\n"
        "        print('/home/azureuser/velib-release.ABC12345')\n"
        "    else:\n"
        "        pathlib.Path(os.environ['REMOTE_SCRIPT']).write_text(sys.stdin.read())\n"
        "elif name == 'scp':\n"
        "    with tarfile.open(sys.argv[-2]) as archive:\n"
        "        names = '\\n'.join(archive.getnames())\n"
        "        pathlib.Path(os.environ['ARCHIVE_LIST']).write_text(names)\n"
    )
    stub.chmod(0o755)
    for name in ("az", "ssh", "scp"):
        (tools / name).symlink_to(stub)
    key = tmp_path / "test-key"
    key.write_text("test fixture, not a private key")
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "CALL_LOG": str(tmp_path / "calls.jsonl"),
        "REMOTE_SCRIPT": str(tmp_path / "remote.sh"),
        "ARCHIVE_LIST": str(tmp_path / "archive.txt"),
        "DEPLOYMENT_OUTPUTS": json.dumps(
            {
                "vM_PUBLIC_IP": {"value": "203.0.113.5"},
                "raW_STORAGE_ACCOUNT_URL": {
                    "value": "https://testaccount.blob.core.windows.net/"
                },
            }
        ),
    }
    return env, key


def deploy(env: dict[str, str], key: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts/deploy-app.sh"), "velib-dev", str(key)],
        cwd=key.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_vm_deploy_uploads_allowlist_and_bootstraps(
    deployment_environment: tuple[dict[str, str], Path],
) -> None:
    env, key = deployment_environment
    result = deploy(env, key)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(env["CALL_LOG"]).read_text().splitlines()]
    assert [call[0] for call in calls] == ["az", "ssh", "scp", "ssh"]
    for call in calls[1:]:
        assert "StrictHostKeyChecking=yes" in call
        assert "BatchMode=yes" in call
    files = Path(env["ARCHIVE_LIST"]).read_text().splitlines()
    assert "infra/vm/bootstrap.sh" in files
    assert "src/velib_datalake/cli.py" in files
    assert not any(
        name.endswith(".env") or "parameters.json" in name or "__pycache__" in name
        for name in files
    )
    remote = Path(env["REMOTE_SCRIPT"]).read_text()
    assert 'sudo -n bash infra/vm/bootstrap.sh "$storage_url"' in remote
    assert "sudo -n velib-admin indicator" in remote


def test_vm_deploy_rejects_unexpected_storage_endpoint(
    deployment_environment: tuple[dict[str, str], Path],
) -> None:
    env, key = deployment_environment
    outputs = json.loads(env["DEPLOYMENT_OUTPUTS"])
    outputs["raW_STORAGE_ACCOUNT_URL"]["value"] = "https://unexpected.example/"
    env["DEPLOYMENT_OUTPUTS"] = json.dumps(outputs)
    result = deploy(env, key)
    assert result.returncode != 0
    assert "Unexpected Blob endpoint" in result.stderr
    calls = Path(env["CALL_LOG"]).read_text().splitlines()
    assert len(calls) == 1


def test_vm_deploy_stops_when_ssh_fails(
    deployment_environment: tuple[dict[str, str], Path],
) -> None:
    env, key = deployment_environment
    env["SSH_FAIL"] = "1"
    result = deploy(env, key)
    assert result.returncode != 0
    assert not Path(env["ARCHIVE_LIST"]).exists()
    assert not Path(env["REMOTE_SCRIPT"]).exists()
