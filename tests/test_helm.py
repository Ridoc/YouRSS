"""Helm chart parity checks; skipped locally when helm is absent, required in CI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

CHART = Path(__file__).parents[1] / "charts" / "yourss"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def render() -> list[dict[str, Any]]:
    helm = shutil.which("helm") or "helm"
    result = subprocess.run(  # noqa: S603 - helm is resolved above, the chart is local
        [helm, "template", "yourss", str(CHART)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def test_image_probes_rollout_and_runtime_user() -> None:
    docs = render()
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"].startswith("ghcr.io/ridoc/yourss:")
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert container["livenessProbe"]["httpGet"]["path"] == "/api/version"
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert pod["securityContext"]["runAsUser"] == 999
    assert pod["securityContext"]["fsGroup"] == 999


def test_settings_map_and_storage() -> None:
    docs = render()
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {entry["name"]: entry for entry in container["env"]}
    expected = {
        "YOURSS_DB_PATH",
        "YOURSS_PUBLIC_BASE_URL",
        "YOURSS_REGISTRATION_ENABLED",
        "YOURSS_SESSION_TTL",
        "YOURSS_PRIVATE",
        "YOURSS_NOINDEX",
        "YOURSS_ADMIN_EMAIL",
        "YOURSS_ADMIN_USERNAME",
        "YOURSS_MAX_PAGES_PER_USER",
        "YOURSS_MAX_PAGE_SUBSCRIPTIONS",
        "YOURSS_FORWARDED_ALLOW_IPS",
        "YOURSS_IMPORT_EXPECT_PAGES",
        "YOURSS_IMPORT_EXPECT_SUBS",
        "YOURSS_VIDEO_DURATIONS",
        "YOURSS_DURATION_TTL",
        "YOURSS_SMTP_HOST",
        "YOURSS_SMTP_PORT",
        "YOURSS_SMTP_USER",
        "YOURSS_SMTP_STARTTLS",
        "YOURSS_SMTP_SSL",
        "YOURSS_SMTP_FROM",
        "YOURSS_TURNSTILE_SITE_KEY",
        "YOURSS_SECRET_KEY",
        "YOURSS_SMTP_PASSWORD",
        "YOURSS_TURNSTILE_SECRET_KEY",
    }
    assert expected <= set(env)
    assert env["YOURSS_DB_PATH"]["value"] == "/data/db/yourss.db"
    assert env["YOURSS_SECRET_KEY"]["valueFrom"]["secretKeyRef"]["name"].endswith("-env")

    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["data-db"]["persistentVolumeClaim"]["claimName"].endswith("-db")
    assert volumes["data-backup"]["persistentVolumeClaim"]["claimName"].endswith("-backup")
    assert volumes["data-cache"]["emptyDir"] == {}

    pvcs = [doc for doc in docs if doc.get("kind") == "PersistentVolumeClaim"]
    assert {doc["metadata"]["name"].rsplit("-", 1)[-1] for doc in pvcs} == {"db", "backup"}
    assert all(doc["spec"]["accessModes"] == ["ReadWriteOnce"] for doc in pvcs)
