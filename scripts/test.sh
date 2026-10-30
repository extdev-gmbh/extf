#!/bin/sh
# Run the test gate in the CI environment (python:3.12-slim + the `dev` extra).
#
# Needs nothing on the host besides Docker (the host has no pytest; never use the host's
# python3 for the gate). Tests the checkout it is called from — the repository root or any
# worktree — and leaves nothing behind in it: the checkout is
# mounted read-only, the container runs as the calling user, caches live inside the
# container, which is removed after the run. Safe to run in several worktrees at once.
#
#   scripts/test.sh                              # the whole gate, as in CI
#   scripts/test.sh tests/test_smoke.py -k x     # arguments go to pytest
#
# The dependency image is built once per content of pyproject.toml and this script; TEST_IMAGE_REBUILD=1
# forces a fresh build. The package itself is not installed: pytest finds it via
# `pythonpath = ["src"]` (pyproject.toml), so the read-only checkout is what is tested.
set -eu

script_root="$(cd "$(dirname "$0")/.." && pwd)"

checkout="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$checkout" ] || [ ! -f "$checkout/pyproject.toml" ]; then
    checkout="$script_root"
fi

fingerprint="$(cat "$script_root/scripts/test.sh" "$checkout/pyproject.toml" | sha256sum | cut -c1-16)"
image="extf-test:$fingerprint"

build() {
    context="$(mktemp -d)"
    cp "$checkout/pyproject.toml" "$context/"
    cat > "$context/Dockerfile" <<'DOCKERFILE'
FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /tmp/pyproject.toml
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
p = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]
print("\n".join(p.get("dependencies", []) + p.get("optional-dependencies", {}).get("dev", [])))
PY
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /tmp/requirements.txt
DOCKERFILE
    docker build --quiet "$@" --tag "$image" "$context" >/dev/null
    rm -rf "$context"
}

if [ -n "${TEST_IMAGE_REBUILD:-}" ]; then
    build --pull --no-cache
elif ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "test.sh: building the dependency image $image" >&2
    build
fi

echo "test.sh: testing $checkout" >&2

# A local .env (tokens) must not reach the tests — CI has none either.
hide_env=""
if [ -e "$checkout/.env" ]; then
    hide_env="--volume /dev/null:/src/.env:ro"
fi

tty=""
if [ -t 1 ]; then
    tty="--tty"
fi

# shellcheck disable=SC2086  # $tty and $hide_env are optional flags, split on purpose
exec docker run --rm --init $tty \
    --user "$(id -u):$(id -g)" \
    --volume "$checkout:/src:ro" \
    $hide_env \
    --workdir /src \
    --env HOME=/tmp \
    --env PYTHONPYCACHEPREFIX=/tmp/pycache \
    --env PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache" \
    "$image" sh -c 'python -m compileall -q src tests && python -m pytest "$@"' gate "$@"
