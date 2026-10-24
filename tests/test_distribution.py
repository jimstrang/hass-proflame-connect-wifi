"""Validate public metadata and execute release packaging without publishing."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZipFile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "proflame_connect_wifi"


@pytest.fixture
def release_steps():
    """Read the actual workflow scripts, not a copy of the packaging logic."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    return {step["name"]: step for step in workflow["jobs"]["release"]["steps"]}


def test_public_metadata():
    """Keep HACS and HA metadata aligned with this fork and the existing domain."""
    components = list((ROOT / "custom_components").glob("*/manifest.json"))
    assert len(components) == 1
    manifest = json.loads(components[0].read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["integration_type"] == "device"
    assert manifest["codeowners"] == ["@jimstrang"]
    assert manifest["documentation"] == "https://github.com/jimstrang/hass-proflame-connect-wifi"
    assert manifest["issue_tracker"] == manifest["documentation"] + "/issues"
    assert manifest["version"]
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "Proflame Connect WiFi"
    assert hacs["filename"] == f"{DOMAIN}.zip"
    assert hacs["zip_release"] is True
    assert hacs["hide_default_branch"] is True
    assert hacs["render_readme"] is True


def test_access_mode_translation_keys_match_forms():
    """Keep every setup/reconfigure access-mode label and warning translatable."""
    directory = ROOT / "custom_components" / DOMAIN
    source = json.loads((directory / "strings.json").read_text())
    english = json.loads((directory / "translations/en.json").read_text())
    for step in ("user", "discovery_confirm", "reconfigure"):
        source_step = source["config"]["step"][step]
        english_step = english["config"]["step"][step]
        assert source_step["data"]["read_only"] == english_step["data"]["read_only"]
        assert source_step["description"] == english_step["description"]
        assert "Off" in english_step["description"]


def test_public_readme_has_no_installation_specific_addresses():
    """Keep concrete network addresses and local filesystem paths out of the README."""
    readme = (ROOT / "README.md").read_text()
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", readme)
    assert "/repos/" not in readme
    assert "/home/" not in readme


def test_non_release_steps_are_guarded(release_steps):
    """A non-release commit must not write a version, archive, or GitHub release."""
    guard = "steps.semantic_release.outputs.new_release_published == 'true'"
    for name in ("Make Release Body", "Package integration", "Create Release"):
        assert release_steps[name]["if"] == guard


@pytest.fixture
def package_environment(tmp_path):
    """Copy only the release inputs into an isolated workspace."""
    integration = tmp_path / "custom_components" / DOMAIN
    shutil.copytree(
        ROOT / "custom_components" / DOMAIN, integration,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copyfile(ROOT / "LICENSE", tmp_path / "LICENSE")
    (integration / "__pycache__").mkdir()
    (integration / "__pycache__/ignored.pyc").write_bytes(b"not a runtime file")
    (integration / "previous.zip").write_bytes(b"not part of the release")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    return {
        **os.environ, "GITHUB_WORKSPACE": str(tmp_path),
        "RUNNER_TEMP": str(runner_temp), "RELEASE_VERSION": "9.8.7",
    }


def test_release_archive_matches_hacs(release_steps, package_environment):
    """Execute the workflow and verify archive layout, version, license, and exclusions."""
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", release_steps["Package integration"]["run"]],
        env=package_environment, check=True, capture_output=True, text=True,
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    archive = Path(package_environment["RUNNER_TEMP"]) / hacs["filename"]
    with ZipFile(archive) as package:
        names = package.namelist()
        assert "manifest.json" in names
        assert "__init__.py" in names
        assert "translations/en.json" in names
        assert package.read("LICENSE").decode() == (ROOT / "LICENSE").read_text()
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["domain"] == DOMAIN
        assert manifest["version"] == package_environment["RELEASE_VERSION"]
        assert not any(
            name.startswith(("custom_components/", f"{DOMAIN}/", "tests/"))
            or "__pycache__" in name or name.endswith((".pyc", ".zip"))
            for name in names
        )


def test_empty_release_version_fails(release_steps, package_environment):
    """Reject an absent release version before modifying the manifest or creating a ZIP."""
    package_environment["RELEASE_VERSION"] = ""
    manifest = Path(package_environment["GITHUB_WORKSPACE"]) / "custom_components" / DOMAIN / "manifest.json"
    original = manifest.read_text()
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", release_steps["Package integration"]["run"]],
        env=package_environment, check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "A release version is required" in result.stderr
    assert manifest.read_text() == original
    assert not list(Path(package_environment["RUNNER_TEMP"]).glob("*.zip"))


def test_first_release_notes_need_no_previous_tag(release_steps, tmp_path):
    """Build first-release notes using read-only git history and no previous tag."""
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", release_steps["Make Release Body"]["run"]],
        cwd=ROOT, env={
            **os.environ, "RUNNER_TEMP": str(tmp_path),
            "PREV_RELEASE": "", "CUR_RELEASE": "HEAD",
        },
        check=True, capture_output=True, text=True,
    )
    body = (tmp_path / "release-body.txt").read_text()
    assert body.startswith("Changes in this release\n")
    assert "\n - " in body
