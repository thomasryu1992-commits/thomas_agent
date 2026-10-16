"""The actor's closed command templates: every argv it can ever run, and nothing a request can shape.

CR-12 (T3, conditional) holds the trust anchor only while these conditions stand:

1. no request value reaches a mount, volume, image, user or privilege argument — the only
   variable parts of any argv here are an image id the actor **measured** (``sha256:`` + 64 hex),
   a tag computed from a PR **number**, a container name from the fixed list below, and the
   runner's own tree path computed from that number;
2. the set of templates is closed and tested (``tests/test_mvp_runtime_deploy_runner_gate_actor.py``
   checks every element of every argv against an allowlist);
3. there is no ``docker run``, ``exec``, ``cp``, mount or environment option anywhere; ``build``
   uses only the runner's own checked-out tree.

Each builder validates its inputs and raises ``ARGUMENT_INVALID`` rather than passing an
unexpected value through. Nothing here executes anything: the actor hands these lists to its
injected ``run``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .reasons import RunnerRefused

IMAGE_REPOSITORY = "thomas-agent-runtime"
# The eight runtime services (docker-compose.yml). hermes runs its own image and is not the runner's.
RUNTIME_CONTAINERS = ("thomas-operator", "thomas-scheduler", "thomas-scheduler-maint", "thomas-read-bridge",
                      "thomas-dispatch-bridge", "thomas-pipeline-worker", "thomas-knowledge-bridge",
                      "thomas-switch-bridge")

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}\Z")
CONTAINER_FORMAT = ("{{.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} "
                    "{{index .Config.Labels \"com.docker.compose.project\"}}")
IMAGE_FORMAT = "{{.Id}}"


def _pr(pr: object) -> int:
    if not isinstance(pr, int) or isinstance(pr, bool) or not 1 <= pr <= 999_999:
        raise RunnerRefused("ARGUMENT_INVALID")
    return pr


def _image_id(value: object) -> str:
    if not isinstance(value, str) or not _IMAGE_ID.match(value):
        raise RunnerRefused("ARGUMENT_INVALID")
    return value


def candidate_ref(pr: int) -> str:
    return f"{IMAGE_REPOSITORY}:candidate-{_pr(pr)}"


def rollback_ref(pr: int) -> str:
    return f"{IMAGE_REPOSITORY}:rollback-pre-{_pr(pr)}"


def latest_ref() -> str:
    return f"{IMAGE_REPOSITORY}:latest"


def tree_path(trees_root: Path, pr: int) -> Path:
    """The runner's own tree for ``pr``: ``<trees_root>/<pr>``, never a path from a request."""
    root = Path(trees_root)
    if not root.is_absolute():
        raise RunnerRefused("ARGUMENT_INVALID")
    return root / str(_pr(pr))


def container_inspect(docker: Path, name: str) -> list[str]:
    if name not in RUNTIME_CONTAINERS:
        raise RunnerRefused("ARGUMENT_INVALID")
    return [str(docker), "container", "inspect", "--format", CONTAINER_FORMAT, name]


def image_inspect(docker: Path, ref: str) -> list[str]:
    allowed = re.compile(rf"^{IMAGE_REPOSITORY}:(?:latest|candidate-[1-9][0-9]{{0,5}}|rollback-pre-[1-9][0-9]{{0,5}})\Z")
    if not isinstance(ref, str) or not (allowed.match(ref) or _IMAGE_ID.match(ref)):
        raise RunnerRefused("ARGUMENT_INVALID")
    return [str(docker), "image", "inspect", "--format", IMAGE_FORMAT, ref]


def tag_rollback_point(docker: Path, running_image_id: str, pr: int) -> list[str]:
    """Tag the measured running image as this PR's rollback point — by id, never by a tag."""
    return [str(docker), "image", "tag", _image_id(running_image_id), rollback_ref(pr)]


def build_candidate(docker: Path, trees_root: Path, pr: int) -> list[str]:
    """Build the runner's own tree into this PR's candidate tag. No build args, no secrets, no pull."""
    return [str(docker), "image", "build", "--pull=false", "--tag", candidate_ref(pr),
            str(tree_path(trees_root, pr))]


def git_head(git: Path, trees_root: Path, pr: int) -> list[str]:
    return [str(git), "-C", str(tree_path(trees_root, pr)), "rev-parse", "HEAD"]


def git_origin_main(git: Path, trees_root: Path, pr: int) -> list[str]:
    return [str(git), "-C", str(tree_path(trees_root, pr)), "rev-parse", "refs/remotes/origin/main"]


def git_status(git: Path, trees_root: Path, pr: int) -> list[str]:
    return [str(git), "-C", str(tree_path(trees_root, pr)), "status", "--porcelain", "--untracked-files=all"]
