"""Tests for mirror.py. Run: python3 -m unittest -v"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mirror

R = mirror.REGISTRY
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
MINOR_LANES = {"matchFileNames": ["images/*.txt"], "matchDepNames": ["alpine/k8s"], "matchUpdateTypes": ["minor"],
               "dependencyDashboardApproval": True}
RENOVATE = json.dumps({
    "packageRules": [*mirror.LANE_RULES, MINOR_LANES],
    "customManagers": [{
        "customType": "regex",
        "managerFilePatterns": [mirror.RENOVATE_FILES],
        "matchStrings": [mirror.RENOVATE_MATCH],
        "datasourceTemplate": "docker",
    }],
})


def text(*lines: str) -> str:
    return "".join(line + "\n" for line in lines)


def check(lists: dict, renovate: str = RENOVATE) -> list:
    files = {path: text(*lines) for path, lines in lists.items()}
    files["renovate.json"] = renovate
    return [str(problem) for problem in mirror.check(files)]


class ParseTest(unittest.TestCase):
    def parse(self, path: str, *lines: str):
        return mirror.parse({path: text(*lines)})

    def test_the_destination_tag_is_the_source_tag(self):
        images, problems = self.parse("images/nginx.txt", "nginx:1.31.10-alpine")
        self.assertEqual(problems, [])
        self.assertEqual([(i.source, i.destination, i.frozen) for i in images],
                         [("nginx:1.31.10-alpine", f"{R}/nginx:1.31.10-alpine", False)])

    def test_a_prefix_goes_before_the_tag(self):
        images, _ = self.parse("images/otel.txt", "otel/autoinstrumentation-java:2.34.0 otel:autoinstrumentation-java")
        self.assertEqual(images[0].destination, f"{R}/otel:autoinstrumentation-java_2.34.0")

    def test_a_line_names_its_repository(self):
        images, problems = self.parse("images/gitea.txt", "bitnamilegacy/valkey:8.1.3-debian-12-r3 valkey",
                                      "gitea/act_runner:0.6.1 act-runner", "docker:27-dind", "foo/x:1 otel:x")
        self.assertEqual(problems, [])
        self.assertEqual([i.destination for i in images], [f"{R}/valkey:8.1.3-debian-12-r3", f"{R}/act-runner:0.6.1",
                                                           f"{R}/gitea:27-dind", f"{R}/otel:x_1"])

    def test_registries_and_deep_paths(self):
        images, problems = self.parse("images/otel.txt",
                                      "ghcr.io/open-telemetry/opentelemetry-operator/opentelemetry-operator:v0.163.0 otel:x")
        self.assertEqual(problems, [])
        self.assertEqual(images[0].name, "ghcr.io/open-telemetry/opentelemetry-operator/opentelemetry-operator")

    def test_frozen_lists(self):
        images, _ = self.parse("images/frozen/flink.txt", "apache/flink:1.20.5")
        self.assertEqual((images[0].destination, images[0].frozen), (f"{R}/flink:1.20.5", True))

    def test_comments_blank_lines_and_whitespace(self):
        images, problems = self.parse("images/busybox.txt", "# busybox", "", "   ", "  # note", "busybox:1.38  ",
                                      "busybox:1.37\tbusybox:bb\t")
        self.assertEqual(problems, [])
        self.assertEqual([(i.line, i.destination) for i in images],
                         [(5, f"{R}/busybox:1.38"), (6, f"{R}/busybox:bb_1.37")])

    def test_lines_that_are_not_images(self):
        for line in ["nginx", "nginx:", "Nginx:1", " nginx:1", "nginx:1 a b", "nginx:1 # note", 'nginx:1: "x"',
                     "nginx:/^1.+/", "localhost:5000/nginx:1", "nginx@sha256:abc", "nginx:1 A", "nginx:1 a/b",
                     "nginx:1 :b", "nginx:1 a:", "nginx:1 a:b:c"]:
            with self.subTest(line=line):
                images, problems = self.parse("images/nginx.txt", line)
                self.assertEqual(images, [])
                self.assertEqual([str(p) for p in problems],
                                 ["images/nginx.txt:1: expected <image>:<tag>, optionally followed by <repository> "
                                  "or <repository>:<prefix>"])

    def test_file_names(self):
        for path in ["images/Nginx.txt", "images/nginx.yaml", "images/a/nginx.txt", "images/frozen/a/nginx.txt"]:
            with self.subTest(path=path):
                images, problems = self.parse(path, "nginx:1")
                self.assertEqual((images, [p.line for p in problems]), ([], [0]))
                self.assertIn("not an image list", problems[0].message)

    def test_other_files_are_ignored(self):
        images, problems = mirror.parse({"README.md": "nginx", "images/.DS_Store": "\0", "images/.gitkeep": ""})
        self.assertEqual((images, problems), ([], []))


class CheckTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(check({"images/nginx.txt": ["nginx:1.31.10-alpine"],
                                "images/frozen/legacy.txt": ["nginx:1.23.3 nginx", "nginx:latest nginx"]}), [])

    def test_no_image_lists(self):
        self.assertEqual(check({}), ["images/: no image lists"])

    def test_duplicates(self):
        self.assertEqual(check({"images/nginx.txt": ["nginx:1", "busybox:1 bb", "nginx:1"]}),
                         ["images/nginx.txt:3: duplicate of images/nginx.txt:1"])

    def test_the_same_source_in_two_repositories(self):
        self.assertEqual(check({"images/nginx.txt": ["nginx:1"], "images/web.txt": ["nginx:1"]}), [])

    def test_two_sources_for_one_destination(self):
        self.assertEqual(check({"images/app.txt": ["foo/app:1 app:app", "bar/app:1 app:app"]}),
                         [f"images/app.txt:2: {R}/app:app_1 is also the destination of foo/app:1 (images/app.txt:1)"])
        self.assertEqual(check({"images/x.txt": ["a:1"], "images/frozen/x.txt": ["b:1"]}),
                         [f"images/x.txt:1: {R}/x:1 is also the destination of b:1 (images/frozen/x.txt:1)"])

    def test_a_group_spans_repositories(self):
        self.assertEqual(check({"images/gitea.txt": ["docker:27-dind docker"], "images/docker.txt": ["docker:27-dind"]}),
                         ["images/gitea.txt:1: duplicate of images/docker.txt:1"])
        self.assertEqual(check({"images/gitea.txt": ["node:24.1.0 node"], "images/node.txt": ["node:24.2.0"]}), [
            "images/node.txt:1: Renovate would update this line and images/gitea.txt:1 to the same tag, both are in "
            'lane 24; keep one line per lane, or add "node" to the minor-lane rule in renovate.json if each of its '
            "minor versions is a release line of its own"])

    def test_a_destination_tag_longer_than_128_characters(self):
        problems = check({"images/a.txt": ["a:1 a:" + "p" * 127]})
        self.assertEqual(problems, ["images/a.txt:1: the destination tag is longer than 128 characters"])

    def test_lanes(self):
        image = lambda tag, name="nginx", frozen=False: mirror.Image("images/x.txt", 1, name, tag, None, R, frozen)
        for tag, name, expected in [("26.13.0-alpine3.24", "node", "26"), ("27-dind", "docker", "27"),
                                    ("v0.65.3.7", "metabase/metabase", "0.65"), ("0.162.0", "otel/collector", "0.162"),
                                    ("1.37.2", "alpine/k8s", "1.37"), ("3.9-eclipse-temurin-21", "maven", "3")]:
            with self.subTest(tag=tag):
                self.assertEqual(mirror.lane(image(tag, name), {"alpine/k8s"})[1], expected)
        for tag in ["latest", "act-22.04", "jdk11u-centos-nightly-slim", "1.0rc1"]:
            with self.subTest(tag=tag):
                self.assertIsNone(mirror.lane(image(tag), set()))
        self.assertIsNone(mirror.lane(image("1.23.3", frozen=True), set()))

    def test_one_line_per_major(self):
        self.assertEqual(check({"images/node.txt": ["node:26.13.0-alpine3.24", "node:24.22.3-alpine3.24",
                                                     "node:22.23.5-alpine3.24", "node:26.13.0-bookworm-slim"],
                                "images/postgres.txt": ["postgres:17.6-alpine", "postgres:18.1-alpine"]}), [])

    def test_two_lines_in_one_lane(self):
        self.assertEqual(check({"images/node.txt": ["node:24.1.0-alpine", "node:24.2.0-alpine"]}), [
            "images/node.txt:2: Renovate would update this line and images/node.txt:1 to the same tag, both are in "
            "lane 24; "
            'keep one line per lane, or add "node" to the minor-lane rule in renovate.json if each of its minor '
            "versions is a release line of its own"])

    def test_each_minor_of_a_0x_version_is_a_lane(self):
        self.assertEqual(check({"images/otel.txt": ["otel/collector:0.161.0", "otel/collector:0.162.0"]}), [])
        self.assertEqual(check({"images/otel.txt": ["otel/collector:0.162.0", "otel/collector:0.162.1"]}), [
            "images/otel.txt:2: Renovate would update this line and images/otel.txt:1 to the same tag, both are in "
            "lane 0.162; "
            "keep one line per lane"])

    def test_minor_lanes(self):
        lines = {"images/alpine-k8s.txt": ["alpine/k8s:1.36.5", "alpine/k8s:1.37.2"]}
        self.assertEqual(check(lines), [])
        without = json.loads(RENOVATE)
        without["packageRules"].remove(MINOR_LANES)
        self.assertEqual(len(check(lines, json.dumps(without))), 1)

    def test_lines_renovate_keeps_apart(self):
        self.assertEqual(check({
            "images/bitnami.txt": ["bitnamilegacy/mysql:8.3.0-debian-11-r0 bitnami:mysql",
                                   "bitnamilegacy/mysql:8.4.0-debian-12-r0 bitnami:mysql"],
            "images/busybox.txt": ["busybox:1.38", "busybox:1.37.0"],
            "images/otel.txt": ["otel/collector:0.1.0 otel:a", "otel/collector:0.1.1 otel:b"],
            "images/ci.txt": ["catthehacker/ubuntu:act-22.04", "catthehacker/ubuntu:act-24.04"],
            "images/frozen/old.txt": ["node:14.16.0", "node:14.17.3"],
        }), [])

    def test_renovate_json(self):
        lists = {"images/nginx.txt": ["nginx:1"]}
        self.assertEqual(check(lists, "{"), ["renovate.json: not valid JSON: Expecting property name enclosed in "
                                             "double quotes: line 1 column 2 (char 1)"])
        wrong = json.loads(RENOVATE)
        wrong["customManagers"][0]["matchStrings"] = ["(?<depName>.*):(?<currentValue>.*)"]
        problems = check(lists, json.dumps(wrong))
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("renovate.json: needs one custom manager"))
        no_lanes = json.loads(RENOVATE)
        no_lanes["packageRules"] = [dict(rule, description="x") for rule in no_lanes["packageRules"][1:]]
        self.assertEqual(check(lists, json.dumps(no_lanes)), [
            "renovate.json: needs the packageRules LANE_RULES (see mirror.py), which keep every line in its lane"])
        files = {"images/nginx.txt": "nginx:1\n"}
        self.assertEqual([str(p) for p in mirror.check(files)], ["renovate.json: missing"])

    def test_the_repository_renovate_json_uses_the_grammar(self):
        minor_lanes, problems = mirror.check_renovate((mirror.ROOT / "renovate.json").read_text())
        self.assertEqual(problems, [])
        self.assertEqual(minor_lanes, {"alpine/k8s", "mongo"})

    def test_renovate_reads_the_same_files_and_lines(self):
        files = re.compile(mirror.RENOVATE_FILES.strip("/"))
        self.assertTrue(files.search("images/nginx.txt"))
        self.assertFalse(files.search("images/frozen/flink.txt"))
        match = re.compile(mirror.RENOVATE_MATCH.replace("(?<", "(?P<"))
        for line in ["nginx:1.31.10-alpine", "otel/java:2.34.0 otel:java", "valkey:8 valkey", "a:1\t", "nginx",
                     " nginx:1", "nginx:1 # x", "nginx:1 a b", "nginx:1 A", "nginx:1 a:b:c"]:
            with self.subTest(line=line):
                self.assertEqual(bool(match.search(line)), bool(mirror.LINE.fullmatch(line)))


class FakeRegistry:
    """Plays regctl against an in-memory registry."""

    def __init__(self):
        self.manifests = {}  # "repo:tag" or "repo@digest" -> (descriptor, body)
        self.blobs = {}  # (repo, digest) -> bytes
        self.copies = []
        self.failures = {}  # ref -> how many more reads of it fail with a server error
        self.reads = []

    def push(self, repo: str, body: dict, tag: str = None, media_type: str = None) -> dict:
        data = json.dumps(body).encode()
        descriptor = {"mediaType": media_type or body["mediaType"], "digest": digest(data), "size": len(data)}
        self.manifests[f"{repo}@{descriptor['digest']}"] = (descriptor, data)
        if tag:
            self.manifests[f"{repo}:{tag}"] = (descriptor, data)
        return descriptor

    def image(self, repo: str, os_name: str, architecture: str, variant: str = None, tag: str = None) -> dict:
        config = {"os": os_name, "architecture": architecture, **({"variant": variant} if variant else {})}
        config_data = json.dumps(config).encode()
        self.blobs[(repo, digest(config_data))] = config_data
        body = {"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest(config_data),
                           "size": len(config_data)}, "layers": []}
        descriptor = self.push(repo, body, tag)
        descriptor["platform"] = config
        return descriptor

    def index(self, repo: str, tag: str, descriptors: list) -> dict:
        return self.push(repo, {"schemaVersion": 2, "mediaType": mirror.OCI_INDEX, "manifests": descriptors}, tag)

    def __call__(self, *args: str, input: bytes = None) -> bytes:
        command, ref = args[:2], args[2]
        if command == ("manifest", "get"):
            self.reads.append(ref)
            if self.failures.get(ref):
                self.failures[ref] -= 1
                raise mirror.SyncError(f"failed to get manifest {ref}: request failed: unknown [http 500]")
        if ref not in self.manifests and command in {("manifest", "head"), ("manifest", "get"), ("image", "copy")}:
            raise mirror.SyncError(f"failed to request manifest {ref}: not found")
        if command == ("manifest", "head"):
            return json.dumps(self.manifests[ref][0]).encode()
        if command == ("manifest", "get"):
            return self.manifests[ref][1]
        if command == ("blob", "get"):
            return self.blobs[(ref, args[3])]
        if command == ("image", "copy"):
            self.copies.append((ref, args[3]))
            self.manifests[args[3]] = self.manifests[ref]
            return b""
        if command == ("manifest", "put"):
            self.push(ref.rsplit(":", 1)[0], json.loads(input), ref.rsplit(":", 1)[1], args[4])
            return b""
        raise AssertionError(f"unexpected regctl {args}")

    def body(self, ref: str) -> dict:
        return json.loads(self.manifests[ref][1])


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def image(source: str, prefix: str = None, repository: str = "busybox") -> mirror.Image:
    name, tag = source.rsplit(":", 1)
    return mirror.Image("images/x.txt", 1, name, tag, prefix, f"{R}/{repository}", False)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        r = self.registry
        self.amd64 = r.image("busybox", "linux", "amd64")
        self.arm64 = r.image("busybox", "linux", "arm64", "v8")
        attestation = {"mediaType": OCI_MANIFEST, "digest": digest(b"a"), "size": 1,
                       "platform": {"os": "unknown", "architecture": "unknown"},
                       "annotations": {"vnd.docker.reference.type": "attestation-manifest"}}
        self.source = r.index("busybox", "1.38", [self.amd64, attestation, r.image("busybox", "linux", "arm", "v7"),
                                                  self.arm64])

    def test_an_index_keeps_only_the_mirrored_platforms(self):
        copy = mirror.sync_image(image("busybox:1.38"), self.registry)
        self.assertEqual(copy, mirror.Copy("new", ["linux/amd64", "linux/arm64/v8"]))
        self.assertEqual(self.registry.copies, [
            (f"busybox@{self.amd64['digest']}", f"{R}/busybox@{self.amd64['digest']}"),
            (f"busybox@{self.arm64['digest']}", f"{R}/busybox@{self.arm64['digest']}")])
        self.assertEqual(self.registry.body(f"{R}/busybox:1.38"), {
            "schemaVersion": 2,
            "mediaType": mirror.OCI_INDEX,
            "manifests": [self.amd64, self.arm64],
            "annotations": {mirror.SOURCE_NAME: "busybox:1.38", mirror.SOURCE_DIGEST: self.source["digest"]},
        })

    def test_a_current_destination_is_left_alone(self):
        mirror.sync_image(image("busybox:1.38"), self.registry)
        self.registry.copies.clear()
        self.assertIsNone(mirror.sync_image(image("busybox:1.38"), self.registry))
        self.assertEqual(self.registry.copies, [])

    def test_a_moved_source_tag_is_copied_again(self):
        mirror.sync_image(image("busybox:1.38"), self.registry)
        moved = self.registry.index("busybox", "1.38", [self.arm64])
        self.assertEqual(mirror.sync_image(image("busybox:1.38"), self.registry),
                         mirror.Copy("source changed", ["linux/arm64/v8"]))
        self.assertEqual(self.registry.body(f"{R}/busybox:1.38")["annotations"][mirror.SOURCE_DIGEST],
                         moved["digest"])

    def test_a_destination_built_some_other_way_is_rebuilt(self):
        self.registry.manifests[f"{R}/busybox:1.38"] = self.registry.manifests["busybox:1.38"]
        self.assertEqual(mirror.sync_image(image("busybox:1.38"), self.registry),
                         mirror.Copy("not built by sync", ["linux/amd64", "linux/arm64/v8"]))

    def test_a_prefixed_destination(self):
        mirror.sync_image(image("busybox:1.38", "bb", "legacy"), self.registry)
        self.assertIn(f"{R}/legacy:bb_1.38", self.registry.manifests)

    def test_a_single_manifest_is_wrapped_in_an_index(self):
        single = self.registry.image("tool", "linux", "amd64", tag="1")
        del single["platform"]
        self.assertEqual(mirror.sync_image(image("tool:1", repository="tool"), self.registry).platforms, ["linux/amd64"])
        self.assertEqual(self.registry.body(f"{R}/tool:1")["manifests"],
                         [dict(single, platform={"os": "linux", "architecture": "amd64"})])

    def test_no_mirrored_platform(self):
        self.registry.image("s390", "linux", "s390x", tag="1")
        self.registry.index("multi", "1", [self.registry.image("multi", "linux", "ppc64le")])
        for source in ["s390:1", "multi:1"]:
            with self.subTest(source=source), self.assertRaisesRegex(mirror.SyncError, "no linux/amd64 or linux/arm64"):
                mirror.sync_image(image(source), self.registry)

    def test_a_missing_destination_is_read_once(self):
        mirror.sync_image(image("busybox:1.38"), self.registry)
        self.assertEqual(self.registry.reads.count(f"{R}/busybox:1.38"), 1)

    def test_a_failed_destination_read_is_retried(self):
        mirror.sync_image(image("busybox:1.38"), self.registry)
        self.registry.copies.clear()
        self.registry.failures[f"{R}/busybox:1.38"] = 1
        self.assertIsNone(mirror.sync_image(image("busybox:1.38"), self.registry))
        self.assertEqual(self.registry.copies, [])

    def test_an_unreadable_destination_is_copied_and_says_why(self):
        mirror.sync_image(image("busybox:1.38"), self.registry)
        self.registry.failures[f"{R}/busybox:1.38"] = 2
        copy = mirror.sync_image(image("busybox:1.38"), self.registry)
        self.assertEqual(copy.reason, f"destination unreadable: failed to get manifest {R}/busybox:1.38: "
                                      "request failed: unknown [http 500]")
        self.assertEqual(self.registry.body(f"{R}/busybox:1.38")["annotations"][mirror.SOURCE_DIGEST],
                         self.source["digest"])

    def test_a_missing_source(self):
        with self.assertRaisesRegex(mirror.SyncError, "not found"):
            mirror.sync_image(image("busybox:9"), self.registry)


class PlanTest(unittest.TestCase):
    def test_duplicates_sync_once_and_shared_destinations_not_at_all(self):
        images, _ = mirror.parse({"images/a.txt": text("nginx:1", "nginx:1", "foo/x:1 a:x", "bar/x:1 a:x", "busybox:2")})
        todo, problems = mirror.plan(images)
        self.assertEqual([i.source for i in todo], ["nginx:1", "busybox:2"])
        self.assertEqual([str(p) for p in problems], [
            f"images/a.txt:3: not synced: {R}/a:x_1 has 2 sources, bar/x:1, foo/x:1",
            f"images/a.txt:4: not synced: {R}/a:x_1 has 2 sources, bar/x:1, foo/x:1"])


class LoginTest(unittest.TestCase):
    def test_logs_in_where_credentials_are_set(self):
        calls = []
        env = {"REGISTRY_USERNAME": "u", "REGISTRY_PASSWORD": "p", "DOCKERHUB_USERNAME": "", "DOCKERHUB_TOKEN": "t"}
        with mock.patch.dict(os.environ, env):
            mirror.login(lambda *args, input=None: calls.append((args, input)))
        self.assertEqual(calls, [(("registry", "login", "registry.cn-shanghai.aliyuncs.com", "-u", "u",
                                   "--pass-stdin"), b"p")])


@unittest.skipUnless(shutil.which("git"), "needs git")
class PullRequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git("init", "-q")
        (self.repo / "renovate.json").write_text(RENOVATE)
        self.base = self.commit({"images/busybox.txt": "busybox:1.38\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, files: dict, parent: str = None) -> str:
        if parent:
            self.git("checkout", "-q", "--detach", parent)
        for path, content in files.items():
            target = self.repo / path
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "change")
        return self.git("rev-parse", "HEAD")

    def test_a_clean_pull_request(self):
        head = self.commit({"images/curl.txt": "curlimages/curl:8.23.0\n"}, self.base)
        self.assertEqual(mirror.pr_state(self.repo, self.base, head),
                         ("success", "images/ and renovate.json are valid"))

    def test_the_merge_result_is_checked(self):
        # Each side is valid on its own; merged, busybox:1.38 has two lines.
        main = self.commit({"images/frozen/busybox.txt": "busybox:1.38\n"}, self.base)
        head = self.commit({"images/curl.txt": "curlimages/curl:8.23.0\n"}, self.base)
        self.assertEqual(mirror.pr_state(self.repo, self.base, head)[0], "success")
        self.assertEqual(mirror.pr_state(self.repo, main, head), (
            "failure", "1 problem: images/frozen/busybox.txt:1: duplicate of images/busybox.txt:1"))

    def test_moving_a_line_to_another_lane(self):
        base = self.commit({"images/docker.txt": "docker:27-dind\n"}, self.base)
        self.assertEqual(mirror.pr_state(self.repo, base, self.commit({"images/docker.txt": "docker:27-dind\n"}, base)),
                         ("success", "images/ and renovate.json are valid"))
        across = self.commit({"images/docker.txt": "docker:29-dind\n"}, base)
        self.assertEqual(mirror.pr_state(self.repo, base, across), (
            "failure", "1 problem: images/docker.txt:1: moves docker:27-dind out of lane 27; "
                       "add a line for the new lane instead"))
        added = self.commit({"images/docker.txt": "docker:27-dind\ndocker:29-dind\n"}, base)
        self.assertEqual(mirror.pr_state(self.repo, base, added)[0], "success")

    def test_a_conflict(self):
        main = self.commit({"images/busybox.txt": "busybox:1.37\n"}, self.base)
        head = self.commit({"images/busybox.txt": "busybox:1.36\n"}, self.base)
        self.assertEqual(mirror.pr_state(self.repo, main, head), ("failure", "Merge conflict with main"))

    def test_a_tree_without_image_lists(self):
        head = self.commit({"images/busybox.txt": None}, self.base)
        self.assertEqual(mirror.pr_state(self.repo, self.base, head),
                         ("failure", "2 problems: images/: no image lists"))

    def test_post_status(self):
        calls = []
        mirror.post_status("o/r", "abc", "failure", "x" * 200, "https://example.com/run",
                           run=lambda args, **kwargs: calls.append(args))
        self.assertEqual(calls, [["gh", "api", "--method", "POST", "repos/o/r/statuses/abc", "-f", "state=failure",
                                  "-f", "context=mirror/check", "-f", "description=" + "x" * 140,
                                  "-f", "target_url=https://example.com/run"]])

    @unittest.skipUnless(shutil.which("gh"), "needs gh")
    def test_gh_accepts_the_status_command(self):
        # A fake token makes the request fail at authentication, so nothing is
        # posted; a usage error would mean the command itself is wrong.
        with mock.patch.dict(os.environ, {"GH_TOKEN": "not-a-real-token"}), \
                self.assertRaises(subprocess.CalledProcessError) as raised:
            mirror.post_status("o/r", "0" * 40, "success", "ok", None,
                               run=lambda args, **kwargs: subprocess.run(args, capture_output=True, text=True,
                                                                         check=True))
        self.assertNotRegex(raised.exception.stderr, "unknown flag|unknown shorthand|Usage:")
        self.assertTrue(raised.exception.stderr)


if __name__ == "__main__":
    unittest.main()
