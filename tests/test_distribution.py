"""Validate public metadata and execute release packaging without publishing."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest
import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "proflame_connect_wifi"


def test_ci_covers_minimum_and_latest_stable_ha_weekly():
    """Keep a pinned support floor and a scheduled, unpinned stable compatibility check."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] == [{"cron": "0 8 * * 1"}]
    assert triggers["pull_request"]["branches"] == ["dev"]
    assert set(triggers["push"]["branches"]) == {"master", "dev"}
    job = workflow["jobs"]["ruff"]
    minimum, latest = job["strategy"]["matrix"]["include"]
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert minimum["homeassistant"] == hacs["homeassistant"]
    assert minimum["requirement"] == f"homeassistant=={hacs['homeassistant']}"
    assert minimum["requirement"] in (ROOT / "requirements.txt").read_text().splitlines()
    assert latest["requirement"] == "homeassistant"
    assert minimum["python"] == latest["python"] == "3.14"
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Set up Python"]["with"]["check-latest"] is True
    install = steps["Install requirements"]
    assert install["env"]["HA_REQUIREMENT"] == "${{ matrix.requirement }}"
    assert shlex.split(install["run"].splitlines()[1]) == [
        "python3", "-m", "pip", "install", "--upgrade", "$HA_REQUIREMENT",
    ]
    assert "Report installed runtime" in steps


def test_development_branch_receives_validation_and_dependency_updates():
    """Validate development PRs and route automated updates away from the release branch."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/validate.yaml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["pull_request"]["branches"] == ["dev"]
    assert set(triggers["push"]["branches"]) == {"master", "dev"}
    dependabot = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert all(update["target-branch"] == "dev" for update in dependabot["updates"])


def test_ci_installs_runtime_requirements_from_manifest():
    """Install the manifest's runtime pins without duplicating them in development requirements."""
    manifest = json.loads((ROOT / "custom_components" / DOMAIN / "manifest.json").read_text())
    runtime_names = {Requirement(value).name for value in manifest["requirements"]}
    development = (ROOT / "requirements.txt").read_text().splitlines()
    assert runtime_names.isdisjoint(Requirement(value).name for value in development if value.strip())
    workflow = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text())
    step = next(step for step in workflow["jobs"]["ruff"]["steps"] if step["name"] == "Install requirements")
    assert step["shell"] == "bash"
    command = step["run"].splitlines()[-1]
    reader, installer = command.split(" | ")
    assert shlex.split(installer) == ["python3", "-m", "pip", "install", "-r", "/dev/stdin"]
    result = subprocess.run(shlex.split(reader), cwd=ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == manifest["requirements"]


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


def test_pilot_selector_has_a_distinct_translated_name():
    """Name the pilot selector instead of falling back to the device's name."""
    directory = ROOT / "custom_components" / DOMAIN
    for filename in ("strings.json", "translations/en.json"):
        translations = json.loads((directory / filename).read_text())
        assert translations["entity"]["select"]["pilot"]["name"] == "Pilot mode"


def test_device_power_has_a_distinct_translated_name():
    """Use a device-power label rather than implying confirmed burner operation."""
    directory = ROOT / "custom_components" / DOMAIN
    for filename in ("strings.json", "translations/en.json"):
        translations = json.loads((directory / filename).read_text())
        assert translations["entity"]["switch"]["fireplace"]["name"] == "Device power"


def test_release_uses_checked_in_version_and_exact_commit(release_steps):
    """Do not derive versions from inherited tags or publish an unvalidated branch tip."""
    assert "Semantic Release" not in release_steps
    assert release_steps["Checkout the repository"]["with"]["ref"] == "${{ github.sha }}"
    release = release_steps["Create Release"]["with"]
    assert release["tag_name"] == "${{ steps.version.outputs.tag }}"
    assert release["target_commitish"] == "${{ github.sha }}"
    assert release["prerelease"] == "${{ steps.version.outputs.prerelease == 'true' }}"
    assert release["generate_release_notes"] is True
    assert "RF-initiated" in release["body"]
    assert "Read-only" in release["body"]


def test_release_requires_version_change_or_manual_confirmation_and_validation():
    """Publish only an intentional release after exact-commit validation succeeds."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["master"]
    assert triggers["push"]["paths"] == ["custom_components/proflame_connect_wifi/manifest.json"]
    confirmation = triggers["workflow_dispatch"]["inputs"]["confirm_publish"]
    assert confirmation["type"] == "boolean"
    assert confirmation["default"] is False
    assert confirmation["required"] is True
    jobs = workflow["jobs"]
    assert set(jobs["release"]["needs"]) == {"prepare", "tests", "validation"}
    assert jobs["release"]["if"] == "github.ref == 'refs/heads/master' && needs.prepare.outputs.publish == 'true'"
    assert jobs["tests"]["uses"] == "./.github/workflows/lint.yml"
    assert jobs["validation"]["uses"] == "./.github/workflows/validate.yaml"
    for name in ("tests", "validation"):
        assert jobs[name]["needs"] == "prepare"
        assert jobs[name]["if"] == "needs.prepare.outputs.publish == 'true'"


@pytest.mark.parametrize("event,ref,confirm,changed,expected", [
    ("push", "refs/heads/master", "", False, False),
    ("push", "refs/heads/master", "", True, True),
    ("push", "refs/heads/feature", "", True, False),
    ("pull_request", "refs/heads/master", "", True, False),
    ("workflow_dispatch", "refs/heads/master", "false", True, False),
    ("workflow_dispatch", "refs/heads/master", "true", False, True),
    ("workflow_dispatch", "refs/heads/feature", "true", True, False),
])
def test_publication_intent_gate(tmp_path, event, ref, confirm, changed, expected):
    """Test real version detection: metadata-only edits and unmerged PRs never publish."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    manifest = tmp_path / "custom_components" / DOMAIN / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "1.1.0-beta.2", "name": "Old metadata"}))
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "baseline"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    previous = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    manifest.write_text(json.dumps({
        "version": "1.1.0-beta.3" if changed else "1.1.0-beta.2", "name": "Updated metadata",
    }))
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"before": previous}))
    output = tmp_path / "outputs"
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    script = next(
        step["run"] for step in workflow["jobs"]["prepare"]["steps"] if step.get("id") == "gate"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path, env={
            **os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref,
            "CONFIRM_PUBLISH": confirm, "GITHUB_EVENT_PATH": str(event_file),
            "GITHUB_OUTPUT": str(output),
        },
        check=True, capture_output=True, text=True,
    )
    assert output.read_text() == f"publish={'true' if expected else 'false'}\n"


def test_validation_is_reusable_and_hacs_checks_exact_commit():
    """Reuse normal CI without ignored branding or accidentally checking a prior release."""
    for filename in ("lint.yml", "validate.yaml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        assert "workflow_call" in workflow.get("on", workflow.get(True))
    workflow = yaml.safe_load((ROOT / ".github/workflows/validate.yaml").read_text())
    action = next(
        step for step in workflow["jobs"]["hacs"]["steps"]
        if step.get("uses") == "hacs/action@main"
    )
    assert "ignore" not in action["with"]
    assert action["env"]["REPOSITORY_REF"] == "${{ github.event.pull_request.head.sha || github.sha }}"


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
    manifest_path = integration / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "9.8.7"
    manifest_path.write_text(json.dumps(manifest))
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


@pytest.mark.parametrize("version,prerelease", [
    ("1.1.0-beta.1", True), ("1.1.0", False), ("1.0.5-beta.1", True),
])
def test_version_outputs_match_manifest(release_steps, package_environment, version, prerelease):
    """Exercise the actual release-version script for stable and beta versions."""
    workspace = Path(package_environment["GITHUB_WORKSPACE"])
    manifest_path = workspace / "custom_components" / DOMAIN / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest))
    output = workspace / "outputs"
    subprocess.run(
        [sys.executable, "-c", release_steps["Read release version"]["run"]],
        cwd=workspace, env={**package_environment, "GITHUB_OUTPUT": str(output)},
        check=True, capture_output=True, text=True,
    )
    assert output.read_text().splitlines() == [
        f"version={version}", f"tag=v{version}",
        f"prerelease={'true' if prerelease else 'false'}",
    ]


@pytest.mark.parametrize("version", [
    "", "1.05-beta.1", "v1.1.0", "1.1.0-beta.01", "1.1.0+build", "../unsafe", None,
])
def test_invalid_version_cannot_create_release_outputs(release_steps, package_environment, version):
    """Reject malformed release identifiers before tagging or packaging."""
    workspace = Path(package_environment["GITHUB_WORKSPACE"])
    manifest_path = workspace / "custom_components" / DOMAIN / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest))
    output = workspace / "outputs"
    result = subprocess.run(
        [sys.executable, "-c", release_steps["Read release version"]["run"]],
        cwd=workspace, env={**package_environment, "GITHUB_OUTPUT": str(output)},
        check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Manifest version must be SemVer" in result.stderr
    assert not output.exists()


def test_mismatched_archive_version_fails(release_steps, package_environment):
    """Never rewrite the source manifest to conceal a mismatched release version."""
    package_environment["RELEASE_VERSION"] = "1.2.3"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", release_steps["Package integration"]["run"]],
        env=package_environment, check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "must match the checked-in manifest" in result.stderr
    assert not list(Path(package_environment["RUNNER_TEMP"]).glob("*.zip"))


@pytest.mark.parametrize("tag,exists", [("v1.0.4", True), ("v99.99.99-test-only", False)])
def test_existing_tags_are_never_overwritten(release_steps, tmp_path, tag, exists):
    """Exercise duplicate-tag refusal in a disposable repo, leaving project tags untouched."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "v1.0.4"], cwd=tmp_path, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", release_steps["Refuse existing tag"]["run"]],
        cwd=tmp_path, env={**os.environ, "RELEASE_TAG": tag},
        check=False, capture_output=True, text=True,
    )
    assert (result.returncode != 0) is exists
