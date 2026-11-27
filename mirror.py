#!/usr/bin/env python3
"""Mirror upstream container images into Aliyun ACR.

Each file images/<repository>.txt lists upstream images, one per line:

    nginx:1.31.10-alpine
    otel/autoinstrumentation-java:2.34.0 autoinstrumentation-java

A line is <image>:<tag>, optionally followed by a tag prefix, and is mirrored to

    registry.cn-shanghai.aliyuncs.com/ahoo-mirror/<repository>:<tag>
    registry.cn-shanghai.aliyuncs.com/ahoo-mirror/<repository>:<prefix>_<tag>

Each line of images/*.txt is a lane: Renovate keeps its tag current within
the compatibility range the upstream promises, by semver a major version, or a
minor version for 0.x. A new lane is a new line. The files in images/frozen/
are read the same way, but their tags change only by hand.

Commands:
    check       validate images/ and renovate.json
    sync        make every destination match its source; needs regctl
    check-prs   check what each open Renovate pull request would merge into
                main, and post the result as a commit status; needs git, gh
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
REGISTRY = "registry.cn-shanghai.aliyuncs.com/ahoo-mirror"
PLATFORMS = (("linux", "amd64"), ("linux", "arm64"))

# The grammar of an image line. renovate.json must use RENOVATE_FILES and
# RENOVATE_MATCH verbatim, and check() enforces that, so Renovate can update
# every line that is mirrored.
_NAME = r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*"
IMAGE = rf"{_NAME}(?:/{_NAME})*"
TAG = r"[A-Za-z0-9][A-Za-z0-9._-]*"
LINE = re.compile(rf"(?P<image>{IMAGE}):(?P<tag>{TAG})(?:[ \t]+(?P<prefix>{TAG}))?[ \t]*")
FILE = re.compile(rf"images/(?P<frozen>frozen/)?(?P<repository>{_NAME})\.txt")
RENOVATE_FILES = r"/^images/[^/]+\.txt$/"
RENOVATE_MATCH = rf"(?m)^(?<depName>{IMAGE}):(?<currentValue>{TAG})(?:[ \t]+{TAG})?[ \t]*$"

# What Renovate's docker versioning treats as a version: numbers, then an
# optional -suffix. An update keeps the suffix and the number of components.
VERSION = re.compile(r"v?(?P<release>\d+(?:\.\d+)*)(?P<suffix>-.+)?")

# Lanes. Semver promises compatibility within a major version, and for 0.x
# only within a minor version, so that is the default lane. Renovate asks on
# the Dependency Dashboard before it would move a line to another lane, and
# renovate.json must contain these rules (descriptions aside). An image whose
# upstream starts a new release line at every minor version (Kubernetes) goes
# in a minor-lane rule: matchUpdateTypes ["minor"], dependencyDashboardApproval
# true and the image in matchDepNames.
LANE_RULES = [
    {"matchFileNames": ["images/*.txt"], "matchUpdateTypes": ["major"], "dependencyDashboardApproval": True},
    {"matchFileNames": ["images/*.txt"], "matchCurrentVersion": "/^v?0\\./", "matchUpdateTypes": ["minor"],
     "dependencyDashboardApproval": True},
]

# Every destination index records the source it was built from. A run copies
# an image again only when the source digest has changed.
SOURCE_NAME = "io.github.ahoo-wang.sync-image.source.name"
SOURCE_DIGEST = "io.github.ahoo-wang.sync-image.source.digest"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
INDEX_TYPES = {OCI_INDEX, "application/vnd.docker.distribution.manifest.list.v2+json"}

STATUS_CONTEXT = "mirror/check"

Files = Dict[str, str]  # repository-relative path -> text


@dataclass(frozen=True)
class Image:
    path: str
    line: int
    name: str
    tag: str
    prefix: Optional[str]
    repository: str
    frozen: bool

    @property
    def source(self) -> str:
        return f"{self.name}:{self.tag}"

    @property
    def destination_tag(self) -> str:
        return f"{self.prefix}_{self.tag}" if self.prefix else self.tag

    @property
    def destination(self) -> str:
        return f"{self.repository}:{self.destination_tag}"


@dataclass(frozen=True)
class Problem:
    path: str
    line: int  # 0 for the whole file
    message: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"


# --- configuration -----------------------------------------------------------


def load(root: Path = ROOT) -> Files:
    """Read images/ and renovate.json from a checkout."""
    files = {}
    for path in sorted((root / "images").rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text()
    renovate = root / "renovate.json"
    if renovate.is_file():
        files["renovate.json"] = renovate.read_text()
    return files


def parse(files: Files) -> Tuple[List[Image], List[Problem]]:
    """Read every image list in files."""
    images, problems = [], []
    for path in sorted(files):
        if not path.startswith("images/") or PurePosixPath(path).name.startswith("."):
            continue
        file = FILE.fullmatch(path)
        if not file:
            problems.append(Problem(path, 0, "not an image list: name it images/<repository>.txt "
                                             "or images/frozen/<repository>.txt, in lowercase"))
            continue
        repository = f"{REGISTRY}/{file['repository']}"
        for number, text in enumerate(files[path].splitlines(), 1):
            if not text.strip() or text.lstrip().startswith("#"):
                continue
            line = LINE.fullmatch(text)
            if not line:
                problems.append(Problem(path, number, "expected <image>:<tag> or <image>:<tag> <prefix>"))
                continue
            images.append(Image(path, number, line["image"], line["tag"], line["prefix"],
                                repository, bool(file["frozen"])))
    return images, problems


def check(files: Files) -> List[Problem]:
    """Everything that is wrong with the configuration in files."""
    images, problems = parse(files)
    if not any(FILE.fullmatch(path) for path in files):
        problems.append(Problem("images/", 0, "no image lists"))
    minor_lanes, renovate_problems = check_renovate(files.get("renovate.json"))
    problems += renovate_problems

    by_destination: Dict[str, Image] = {}
    by_lane: Dict[tuple, Image] = {}
    for image in images:
        if len(image.destination_tag) > 128:
            problems.append(Problem(image.path, image.line, "the destination tag is longer than 128 characters"))
        first = by_destination.setdefault(image.destination, image)
        if first is not image:
            if first.source == image.source:
                message = f"duplicate of {first.path}:{first.line}"
            else:
                message = f"{image.destination} is also the destination of {first.source} ({first.path}:{first.line})"
            problems.append(Problem(image.path, image.line, message))
            continue
        found = lane(image, minor_lanes)
        if not found:
            continue
        first = by_lane.setdefault(found[0], image)
        if first is not image:
            message = (f"Renovate would update this line and line {first.line} to the same tag, "
                       f"both are in lane {found[1]}; keep one line per lane")
            if image.name not in minor_lanes and "." not in found[1]:
                message += (f', or add "{image.name}" to the minor-lane rule in renovate.json if each of its '
                            "minor versions is a release line of its own")
            problems.append(Problem(image.path, image.line, message))
    return sorted(problems, key=lambda p: (p.path, p.line))


def lane(image: Image, minor_lanes: Set[str]) -> Optional[Tuple[tuple, str]]:
    """The lane of a line Renovate updates, as (key, name), e.g. "26" or "0.162".

    Renovate moves a line to the newest version with the same suffix and number
    of components inside its lane, so two lines with the same key end up as
    one tag. Frozen lines and tags that are not versions have no lane.
    """
    version = VERSION.fullmatch(image.tag)
    if image.frozen or not version:
        return None
    parts = version["release"].split(".")
    name = ".".join(parts[:2 if parts[0] == "0" or image.name in minor_lanes else 1])
    return (image.path, image.name, image.prefix, version["suffix"], len(parts), name), name


def lost_lanes(base: Files, merged: Files) -> List[Problem]:
    """The lanes in base that merged no longer has: a line moved to another lane."""
    minor_lanes = check_renovate(base.get("renovate.json"))[0]

    def lanes(files: Files) -> Dict[tuple, Tuple[Image, str]]:
        found = {}
        for image in parse(files)[0]:
            image_lane = lane(image, minor_lanes)
            if image_lane:
                found.setdefault(image_lane[0], (image, image_lane[1]))
        return found

    after = lanes(merged)
    return [Problem(image.path, image.line, f"moves {image.source} out of lane {name}; "
                                            "add a line for the new lane instead")
            for key, (image, name) in lanes(base).items() if key not in after]


def check_renovate(text: Optional[str]) -> Tuple[Set[str], List[Problem]]:
    """Return the images in minor-lane rules, and what is wrong with renovate.json."""
    if text is None:
        return set(), [Problem("renovate.json", 0, "missing")]
    try:
        config = json.loads(text)
    except ValueError as error:
        return set(), [Problem("renovate.json", 0, f"not valid JSON: {error}")]
    problems = []
    managers = [m for m in config.get("customManagers", []) if m.get("managerFilePatterns") == [RENOVATE_FILES]]
    if len(managers) != 1 or managers[0].get("matchStrings") != [RENOVATE_MATCH] \
            or managers[0].get("datasourceTemplate") != "docker":
        problems.append(Problem("renovate.json", 0, "needs one custom manager with the managerFilePatterns "
                                                    "RENOVATE_FILES, the matchStrings RENOVATE_MATCH (see "
                                                    "mirror.py) and the datasourceTemplate docker"))
    rules = [{key: value for key, value in rule.items() if key != "description"}
             for rule in config.get("packageRules", [])]
    if any(rule not in rules for rule in LANE_RULES):
        problems.append(Problem("renovate.json", 0, "needs the packageRules LANE_RULES (see mirror.py), "
                                                    "which keep every line in its lane"))
    minor_lanes = set()
    for rule in rules:
        if rule.get("matchUpdateTypes") == ["minor"] and rule.get("dependencyDashboardApproval") is True \
                and "matchCurrentVersion" not in rule:
            minor_lanes.update(rule.get("matchDepNames", []))
    return minor_lanes, problems


# --- sync --------------------------------------------------------------------


class SyncError(Exception):
    pass


Regctl = Callable[..., bytes]


def regctl(*args: str, input: Optional[bytes] = None) -> bytes:
    result = subprocess.run(["regctl", *args], input=input, capture_output=True)
    if result.returncode:
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        raise SyncError(lines[-1] if lines else f"regctl {' '.join(args[:2])} failed")
    return result.stdout


def plan(images: List[Image]) -> Tuple[List[Image], List[Problem]]:
    """One image per destination. A destination with two sources is not synced."""
    by_destination: Dict[str, List[Image]] = {}
    for image in images:
        by_destination.setdefault(image.destination, []).append(image)
    todo, problems = [], []
    for destination, group in by_destination.items():
        sources = sorted({image.source for image in group})
        if len(sources) == 1:
            todo.append(group[0])
            continue
        for image in group:
            problems.append(Problem(image.path, image.line,
                                    f"not synced: {destination} has {len(sources)} sources, {', '.join(sources)}"))
    return todo, problems


def select(descriptors: List[dict]) -> List[dict]:
    """The descriptors for PLATFORMS, in that order. Attestations have platform unknown/unknown."""
    chosen = []
    for os_name, architecture in PLATFORMS:
        for descriptor in descriptors:
            platform = descriptor.get("platform") or {}
            if platform.get("os") == os_name and platform.get("architecture") == architecture:
                chosen.append(descriptor)
                break
    return chosen


def platform_name(descriptor: dict) -> str:
    platform = descriptor["platform"]
    return "/".join(filter(None, (platform["os"], platform["architecture"], platform.get("variant"))))


def sync_image(image: Image, regctl: Regctl = regctl) -> Optional[List[str]]:
    """Make image.destination an index of the source's PLATFORMS manifests.

    Return the platforms copied, or None when the destination was already
    built from the current source digest. Aliyun ACR rejects OCI attestation
    manifests (unknown manifest class for application/vnd.oci.empty.v1+json),
    so the index is rebuilt without them rather than copied.
    """
    source = json.loads(regctl("manifest", "head", image.source, "--format", "{{json .GetDescriptor}}"))
    digest = source["digest"]
    try:
        current = json.loads(regctl("manifest", "get", image.destination, "--format", "raw-body"))
    except SyncError:
        current = {}
    if (current.get("annotations") or {}).get(SOURCE_DIGEST) == digest:
        return None

    # Read the manifest by digest, so every step sees the same version of the tag.
    manifest = json.loads(regctl("manifest", "get", f"{image.name}@{digest}", "--format", "raw-body"))
    if source["mediaType"] in INDEX_TYPES:
        descriptors = select(manifest["manifests"])
    else:
        config = json.loads(regctl("blob", "get", image.name, manifest["config"]["digest"]))
        platform = {key: config[key] for key in ("os", "architecture", "variant") if config.get(key)}
        descriptors = select([dict(source, platform=platform)])
    if not descriptors:
        raise SyncError(f"no {' or '.join('/'.join(p) for p in PLATFORMS)} image")

    for descriptor in descriptors:
        regctl("image", "copy", f"{image.name}@{descriptor['digest']}", f"{image.repository}@{descriptor['digest']}")
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": descriptors,
        "annotations": {SOURCE_NAME: image.source, SOURCE_DIGEST: digest},
    }
    regctl("manifest", "put", image.destination, "--content-type", OCI_INDEX,
           input=json.dumps(index, indent=2).encode())
    return [platform_name(descriptor) for descriptor in descriptors]


def login(regctl: Regctl = regctl) -> None:
    """Log in to the registries whose credentials are in the environment."""
    for registry, user, password in ((REGISTRY.split("/")[0], "REGISTRY_USERNAME", "REGISTRY_PASSWORD"),
                                     ("docker.io", "DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN")):
        if os.environ.get(user) and os.environ.get(password):
            regctl("registry", "login", registry, "-u", os.environ[user], "--pass-stdin",
                   input=os.environ[password].encode())


def sync(jobs: int) -> int:
    images, problems = parse(load())
    todo, refused = plan(images)
    problems += refused
    for problem in problems:
        annotate(problem)
    try:
        login()
    except SyncError as error:
        print(f"error: login failed: {error}")
        return 1

    current, synced, failed = 0, [], []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(sync_image, image): image for image in todo}
        for future in as_completed(futures):
            image = futures[future]
            try:
                platforms = future.result()
            except Exception as error:  # noqa: BLE001 - one image must not stop the others
                failed.append((image, str(error)))
                annotate(Problem(image.path, image.line, f"{image.source}: {error}"))
                continue
            if platforms is None:
                current += 1
                print(f"current  {image.source} -> {image.destination}")
            else:
                synced.append((image, ", ".join(platforms)))
                print(f"synced   {image.source} -> {image.destination} ({', '.join(platforms)})")

    summary = f"{len(todo)} images: {current} current, {len(synced)} synced, {len(failed)} failed"
    print(summary)
    write_summary(summary, synced, failed)
    return 1 if problems or failed else 0


def write_summary(summary: str, synced: list, failed: list) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    rows = [f"| synced | `{image.source}` | `{image.destination}` | {detail} |" for image, detail in synced]
    rows += [f"| **failed** | `{image.source}` | `{image.destination}` | {detail} |" for image, detail in failed]
    with open(path, "a") as out:
        out.write(f"### Sync\n\n{summary}\n\n")
        if rows:
            out.write("| | Source | Destination | |\n|---|---|---|---|\n" + "\n".join(rows) + "\n")


# --- Renovate pull requests --------------------------------------------------


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def tree_files(root: Path, tree: str) -> Files:
    """Read images/ and renovate.json from a git tree."""
    files = {}
    archive = git(root, "archive", "--format=tar", tree)
    if archive.returncode:
        return files
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        for member in tar:
            if member.isfile() and (member.name.startswith("images/") or member.name == "renovate.json"):
                files[member.name] = tar.extractfile(member).read().decode(errors="replace")
    return files


def pr_state(root: Path, base: str, head: str) -> Tuple[str, str]:
    """The commit status for head: check the configuration that merging it into base produces.

    Renovate only changes tags, so a lane of base that is missing after the
    merge means the pull request moves a line to another lane.
    """
    merge = git(root, "merge-tree", "--write-tree", base, head)
    if merge.returncode == 1:
        return "failure", "Merge conflict with main"
    if merge.returncode:
        return "error", "git merge-tree failed"
    merged = tree_files(root, merge.stdout.split()[0].decode())
    problems = check(merged) + lost_lanes(tree_files(root, base), merged)
    if problems:
        count = f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"
        return "failure", f"{count}: {problems[0]}"
    return "success", "images/ and renovate.json are valid"


def post_status(repo: str, sha: str, state: str, description: str, url: Optional[str],
                run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    # GitHub keeps at most 140 characters of a description.
    args = ["gh", "api", "--method", "POST", f"repos/{repo}/statuses/{sha}",
            "-f", f"state={state}", "-f", f"context={STATUS_CONTEXT}", "-f", f"description={description[:140]}"]
    if url:
        args += ["-f", f"target_url={url}"]
    run(args, check=True, stdout=subprocess.DEVNULL)


def check_prs(dry_run: bool) -> int:
    """Nothing from a pull request is executed: only its images/ and renovate.json are read."""
    repo = os.environ.get("GITHUB_REPOSITORY") or github_repo()
    base = "refs/remotes/origin/main"
    if git(ROOT, "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/main:{base}").returncode:
        print("error: could not fetch main")
        return 1
    listed = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", "open", "--author", "app/github-actions",
                             "--limit", "200", "--json", "number,headRefOid"], capture_output=True, text=True)
    if listed.returncode:
        print(f"error: could not list pull requests: {listed.stderr.strip()}")
        return 1
    pulls = json.loads(listed.stdout)
    if not pulls:
        print("no open Renovate pull requests")
    url = None
    if os.environ.get("GITHUB_RUN_ID"):
        url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"

    failed = 0
    for pull in pulls:
        head = pull["headRefOid"]
        if git(ROOT, "fetch", "--quiet", "--no-tags", "origin", head).returncode:
            state, description = "error", "Could not fetch the pull request head"
        else:
            state, description = pr_state(ROOT, base, head)
        print(f"#{pull['number']} {state}: {description}")
        if dry_run:
            continue
        try:
            post_status(repo, head, state, description, url)
        except subprocess.CalledProcessError:
            failed += 1
            print(f"error: could not post the status for #{pull['number']}")
    return 1 if failed else 0


def github_repo() -> str:
    url = git(ROOT, "remote", "get-url", "origin").stdout.decode().strip()
    return re.sub(r"\.git$", "", re.sub(r"^(?:https://[^/]+/|git@[^:]+:)", "", url))


# --- command line ------------------------------------------------------------


def annotate(problem: Problem) -> None:
    """Print a problem; on GitHub Actions also as an annotation on the file."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        where = f"file={problem.path}" + (f",line={problem.line}" if problem.line else "")
        text = str(problem).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error {where}::{text}")
    else:
        print(f"error: {problem}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate images/ and renovate.json")
    sync_parser = commands.add_parser("sync", help="make every destination match its source")
    sync_parser.add_argument("--jobs", type=int, default=4, help="images to sync at once (default: 4)")
    prs_parser = commands.add_parser("check-prs", help="post a commit status on each open Renovate pull request")
    prs_parser.add_argument("--dry-run", action="store_true", help="print the statuses instead of posting them")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)

    if args.command == "check":
        files = load()
        problems = check(files)
        for problem in problems:
            annotate(problem)
        if problems:
            return 1
        images, _ = parse(files)
        print(f"ok: {len(images)} images in {sum(1 for path in files if FILE.fullmatch(path))} lists")
        return 0
    if args.command == "sync":
        return sync(args.jobs)
    return check_prs(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
