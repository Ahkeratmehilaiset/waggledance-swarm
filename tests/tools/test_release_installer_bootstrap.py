"""Static build contract; these tests do not claim a Docker image was built."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_docker_bootstraps_fixed_installer_before_application_dependencies():
    # GHSA-qwm4-qh6w-59xr is fixed in pip 26.2.0. Pin the verified patch
    # release instead of depending on the moving base image's bundled pip.
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    instructions = " ".join(
        line.strip().rstrip("\\").strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    bootstrap = "python -m pip install --no-cache-dir --upgrade pip==26.2.1"
    dependencies = "python -m pip install --no-cache-dir -r requirements-ci.txt"
    assert f"RUN {bootstrap} && {dependencies}" in instructions
    # The installer upgrade must be the first install, so failure stops the
    # build before resolving application dependencies with the old installer.
    assert instructions.index("pip install") == instructions.index(bootstrap) + len("python -m ")


def test_docker_keeps_existing_dependency_profile_and_entrypoint():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements-ci.txt ." in dockerfile
    assert 'CMD ["python", "-m", "waggledance.adapters.cli.start_runtime"]' in dockerfile
    assert "-r requirements.lock.txt" not in dockerfile
