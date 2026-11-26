"""Opt-in local/CI Docker checks. Creates synthetic files only; no publication."""

import io
import json
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = "mymachines-3d-quote-backend:latest"
FRONTEND = "mymachines-3d-quote-frontend:latest"


def docker(*args, **kwargs):
    kwargs.setdefault("timeout", 300)
    return subprocess.run(["docker", *args], check=True, **kwargs)


def stop_on_signal(number, frame):
    raise SystemExit(128 + number)


def wait_healthy(name):
    deadline = time.monotonic() + 30
    while True:
        state = json.loads(docker("inspect", name, capture_output=True).stdout)[0]["State"]
        assert state["Running"], "Backend did not remain running."
        if state["Health"]["Status"] == "healthy":
            return
        assert time.monotonic() < deadline, "Backend healthcheck timed out."
        time.sleep(0.2)


def reference_quote(backend):
    # Direct backend HTTP avoids borrowing the Nginx smoke suite's rate budget.
    code = r"""
import json
from urllib.request import Request, urlopen
import trimesh
model = trimesh.creation.box(extents=[10, 20, 30]).export(file_type='stl')
body = (b'--mm007\r\nContent-Disposition: form-data; name="file"; filename="synthetic.stl"\r\n'
        b'Content-Type: model/stl\r\n\r\n' + model + b'\r\n--mm007--\r\n')
request = Request('http://127.0.0.1:8000/estimate', data=body,
                  headers={'Content-Type': 'multipart/form-data; boundary=mm007'})
with urlopen(request, timeout=90) as response:
    result = json.load(response)
assert result['status'] == 'estimated'
print(json.dumps(result['pricing']))
"""
    return json.loads(docker("exec", backend, "python", "-c", code, capture_output=True).stdout)


def wait_proxy(backend):
    deadline = time.monotonic() + 30  # Includes Nginx's existing 10-second DNS cache.
    while subprocess.run(["docker", "exec", backend, "curl", "--max-time", "2", "-fsS",
                         "http://frontend/api/health"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        assert time.monotonic() < deadline, "Frontend proxy did not become ready."
        time.sleep(0.2)


def main():
    token = uuid.uuid4().hex[:12]
    prefix = "mm007-" + token
    names = [prefix + suffix for suffix in ("-probe", "-backend", "-frontend", "-http", "-check")]
    image = prefix + ":context"
    network = prefix + "-network"
    canaries = []
    marker = ("SYNTHETIC_PRIVATE_CONFIG_MM007_" + token).encode()
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(number, stop_on_signal)
    try:
        for directory in ("config", "backend/app", "frontend/public"):
            for suffix in (".json", ".private.json"):
                path = ROOT / directory / (prefix + suffix)
                with path.open("xb") as stream:
                    stream.write(b'{"synthetic_canary":"' + marker + b'"}\n')
                canaries.append(path)
        # Exercise Docker's own context filter, not an approximate glob parser.
        docker("build", "--pull=false", "--network=none", "-t", image, "-f", "-", ".",
               input=b"FROM scratch\nCOPY . /context/\n", cwd=ROOT, capture_output=True)
        docker("create", "--name", names[0], image, "/unused", capture_output=True)
        exported = docker("export", names[0], capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(exported)) as archive:
            members = {item.name for item in archive.getmembers()}
            assert "context/config/pricing.demo.json" in members
            assert "context/config/printing.json" in members
            assert not any(name.startswith("context/backend/tests/") for name in members)
            for path in canaries:
                assert "context/" + path.relative_to(ROOT).as_posix() not in members
            for item in archive.getmembers():
                if item.isfile():
                    assert marker not in archive.extractfile(item).read(), "Canary entered build context."
        for path in canaries:
            if path.name.endswith(".private.json"):
                assert subprocess.run(["git", "check-ignore", "--quiet", str(path)], cwd=ROOT).returncode == 0
        print("PASS: real Docker context excludes six synthetic private/configuration canaries.", flush=True)

        # The published runtime images have no private mount/file. Context
        # exclusion also prevents these canaries from entering intermediate layers.
        docker("run", "--rm", "--pull", "never", "--network", "none", "--name", names[4], BACKEND,
               "python", "-c", "from pathlib import Path; from app.services.commercial_config import get_commercial_config; "
               "assert get_commercial_config().configuration_kind == 'demonstration'; "
               "assert {p.name for p in Path('/project/config').iterdir()} == {'printing.json','pricing.demo.json'}; "
               "assert not Path('/run/mymachines/pricing.json').exists()")
        docker("run", "--rm", "--pull", "never", "--network", "none", "--name", names[4],
               "--entrypoint", "sh", FRONTEND, "-c", "test ! -e /run/mymachines/pricing.json")

        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            directory = Path(directory)
            data = json.loads((ROOT / "config/pricing.demo.json").read_text())
            data["configuration_kind"] = "private"  # Explicit synthetic fixture, not real prices.
            pricing = directory / "synthetic.private.json"
            pricing.write_text(json.dumps(data))
            invalid = directory / "invalid.private.json"
            invalid.write_bytes(b'{"' + marker + b'":1}')
            mount = "/run/mymachines/pricing.json"
            for mode in ("missing", "invalid", "bundled-demo"):
                args = ["run", "--rm", "--pull", "never", "--network", "none", "--name", names[4],
                        "-e", "APP_ENV=production"]
                if mode == "invalid":
                    args += ["-e", "PRICING_CONFIG_PATH=" + mount, "-v", f"{invalid}:{mount}:ro"]
                elif mode == "bundled-demo":
                    args += ["-e", "PRICING_CONFIG_PATH=/project/config/pricing.demo.json"]
                result = subprocess.run(["docker", *args, BACKEND], capture_output=True, timeout=30)
                assert result.returncode == 1, mode
                assert marker not in result.stdout + result.stderr, "Startup leaked synthetic configuration."
            print("PASS: production startup rejects missing, invalid and bundled-demo configuration.", flush=True)

            docker("network", "create", "--internal", network, capture_output=True)
            backend_image = docker("image", "inspect", "--format", "{{.Id}}", BACKEND,
                                   capture_output=True, text=True).stdout.strip()

            def start_backend():
                docker("run", "-d", "--pull", "never", "--init", "--network", network,
                       "--network-alias", "backend", "--name", names[1],
                       "-e", "APP_ENV=production", "-e", "PRICING_CONFIG_PATH=" + mount,
                       "-v", f"{pricing}:{mount}:ro", "--health-cmd", "curl -fsS http://localhost:8000/health",
                       "--health-interval", "1s", "--health-timeout", "3s", "--health-retries", "20",
                       backend_image, capture_output=True)
                wait_healthy(names[1])
                actual = docker("inspect", "--format", "{{.Image}}", names[1],
                                capture_output=True, text=True).stdout.strip()
                assert actual == backend_image, "Backend image changed during configuration update."

            start_backend()
            print("PASS: production backend starts healthy with a read-only external synthetic configuration.", flush=True)
            # Isolated internal bridge: no published host ports or external egress.
            docker("run", "-d", "--pull", "never", "--name", names[2], "--network", network,
                   "--network-alias", "frontend", FRONTEND, capture_output=True)
            docker("exec", names[2], "nginx", "-t")
            wait_proxy(names[1])
            docker("run", "--rm", "--pull", "never", "--name", names[3], "--network", network,
                   "-v", f"{ROOT / 'backend/tests'}:/app/tests:ro", BACKEND,
                   "python", "/app/tests/nginx_smoke.py", "http://frontend")

            frontend_id = docker("inspect", "--format", "{{.Id}}", names[2], capture_output=True).stdout
            original = reference_quote(names[1])
            changed = json.loads(json.dumps(data))
            changed["commercial"]["minimum_order_eur"] = round(original["selling_price_eur"] + 17.25, 2)

            def atomic_replace(candidate):
                replacement = directory / "replacement.private.json"
                replacement.write_text(json.dumps(candidate))
                replacement.replace(pricing)

            atomic_replace(changed)
            # A file bind mount retains the old inode after host atomic replace.
            mounted_minimum = docker("exec", names[1], "python", "-c",
                "import json; print(json.load(open('/run/mymachines/pricing.json'))['commercial']['minimum_order_eur'])",
                capture_output=True, text=True).stdout
            assert float(mounted_minimum) == data["commercial"]["minimum_order_eur"]
            assert reference_quote(names[1]) == original, "Running snapshot changed without recreation."
            docker("rm", "-f", names[1], capture_output=True)
            start_backend()
            wait_proxy(names[1])
            updated = reference_quote(names[1])
            assert updated["selling_price_eur"] == changed["commercial"]["minimum_order_eur"]
            assert updated["minimum_order_applied"]
            atomic_replace(data)
            docker("rm", "-f", names[1], capture_output=True)
            start_backend()
            wait_proxy(names[1])
            assert reference_quote(names[1]) == original, "Rollback did not restore the original quote."
            assert docker("inspect", "--format", "{{.Id}}", names[2], capture_output=True).stdout == frontend_id
            print("PASS: atomic replacement retains the old bind/snapshot until backend recreation; "
                  "update and rollback restore expected real-Orca prices with the same image and unchanged frontend.", flush=True)
            print(f"PASS: synthetic price sequence {original['selling_price_eur']} -> "
                  f"{updated['selling_price_eur']} -> {original['selling_price_eur']}; image {backend_image}.", flush=True)
    finally:
        # Only resources generated by this invocation; keep test failure status.
        subprocess.run(["docker", "rm", "-f", *names], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for path in canaries:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
