"""Run the template's Bash bodies with isolated homes and fake GitHub responses."""

import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


TEMPLATE = Path(__file__).resolve().parents[1] / "poof.yml"
BASH = shutil.which("bash")


def template_scripts():
    # Read only literal Bash blocks; this deliberately does not emulate Azure YAML.
    source = TEMPLATE.read_text()
    blocks = re.findall(r"^  - bash: \|\n((?:      [^\n]*\n|\n)+)", source, re.M)
    if len(blocks) != 2:
        raise AssertionError("Expected bootstrap and package Bash blocks in poof.yml")
    return ["\n".join(line[6:] for line in block.splitlines()) for block in blocks]


MOCK_COMMAND = r'''
import json, os, pathlib, shutil, sys
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if command == "uname":
    print(os.environ["TEST_OS"] if args == ["-s"] else os.environ["TEST_ARCH"])
elif command == "ldd":
    print(os.environ["TEST_LIBC"])
elif command == "curl":
    with open(os.environ["TEST_LOG"], "a") as log:
        log.write(json.dumps({"command": command, "args": args}) + "\n")
    if "-o" in args:
        if os.environ.get("TEST_DOWNLOAD_FAIL"):
            sys.exit(22)
        shutil.copyfile(os.environ["TEST_ARCHIVE"], args[args.index("-o") + 1])
    else:
        if os.environ.get("TEST_API_FAIL"):
            sys.exit(22)
        print(json.dumps({"tag_name": os.environ.get("TEST_RELEASE", "v9.8.7")}))
elif command == "poof":
    if args == ["--version"]:
        print("poof 9.8.7")
    else:
        with open(os.environ["TEST_LOG"], "a") as log:
            log.write(json.dumps({"command": command, "args": args,
                                  "token": os.environ.get("GITHUB_TOKEN")}) + "\n")
        if os.environ.get("TEST_PACKAGE_FAIL"):
            sys.exit(1)
else:
    sys.exit("Unexpected mock command: " + command)
'''


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="poof template tests ")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "mock-bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.log.touch()
        # Use the running Python interpreter, without relying on the runner's PATH.
        self.mock = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(
            "import sys; sys.argv = sys.argv[1:]; exec(" + repr(MOCK_COMMAND) + ")"
        ) + ' "$0" "$@"\n'
        for command in ("uname", "ldd", "curl", "poof"):
            executable = self.bin / command
            executable.write_text(self.mock)
            executable.chmod(0o755)
        self.archive = self.root / "release.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            data = self.mock.encode()
            info = tarfile.TarInfo("poof")
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "HOME": str(self.home),
            "LC_ALL": "C",
            "TMPDIR": str(self.root),
            "TEST_LOG": str(self.log),
            "TEST_ARCHIVE": str(self.archive),
            "TEST_OS": "Linux",
            "TEST_ARCH": "x86_64",
            "TEST_LIBC": "ldd (GNU libc) 2.39",
        }
        self.bootstrap, self.packages = template_scripts()

    def run_script(self, script, **env):
        self.log.write_text("")
        return subprocess.run(
            [BASH, "--noprofile", "--norc"], input=script, text=True,
            env={**self.env, **env}, cwd=self.root, capture_output=True,
        )

    def successful(self, script, **env):
        result = self.run_script(script, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_bash_syntax(self):
        for script in (self.bootstrap, self.packages):
            result = subprocess.run([BASH, "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_platform_targets(self):
        for system, suffix in (("Linux", "unknown-linux-gnu"), ("Darwin", "apple-darwin")):
            for arch, normalized in (("amd64", "x86_64"), ("x86_64", "x86_64"),
                                     ("arm64", "aarch64"), ("aarch64", "aarch64")):
                with self.subTest(system=system, arch=arch):
                    self.successful(self.bootstrap, TEST_OS=system, TEST_ARCH=arch)
                    expected = f"https://github.com/pirafrank/poof/releases/download/v9.8.7/poof-9.8.7-{normalized}-{suffix}.tar.gz"
                    self.assertIn(expected, self.calls()[1]["args"])
        for arch, normalized in (("amd64", "x86_64"), ("arm64", "aarch64")):
            with self.subTest(libc="musl", arch=arch):
                self.successful(self.bootstrap, TEST_ARCH=arch, TEST_LIBC="musl libc")
                self.assertTrue(any(f"{normalized}-unknown-linux-musl.tar.gz" in arg
                                    for arg in self.calls()[1]["args"]))

    def test_tokens(self):
        for token in (None, "", "$(GITHUB_TOKEN)", "$(CUSTOM_TOKEN)", "dummy-token"):
            with self.subTest(token=token):
                env = {} if token is None else {"GITHUB_TOKEN": token}
                self.successful(self.bootstrap, **env)
                args = self.calls()[0]["args"]
                headers = [args[i + 1] for i, value in enumerate(args) if value == "-H"]
                expected = ["Accept: application/vnd.github+json"]
                if token == "dummy-token":
                    expected.append("Authorization: Bearer dummy-token")
                self.assertEqual(headers, expected)
                self.successful(self.packages, POOF_PACKAGES="cli/cli", **env)
                self.assertEqual(self.calls()[0]["token"], token if token == "dummy-token" else
                                 ("" if token == "" else None))

    def test_package_inputs(self):
        cases = (
            ("cli/cli", [["install", "cli/cli"]]),
            ("cli/cli,ms-jpq/sad", [["install", "cli/cli"], ["install", "ms-jpq/sad"]]),
            ("cli/cli\nms-jpq/sad", [["install", "cli/cli"], ["install", "ms-jpq/sad"]]),
            (" \tcli/cli\r\n,\nms-jpq/sad@v1.2.3 \n", [["install", "cli/cli"],
                                                        ["install", "ms-jpq/sad", "--tag", "v1.2.3"]]),
            ("", []), (" \t,\r\n ", []),
            ("owner/repo@release's-tag", [["install", "owner/repo", "--tag", "release's-tag"]]),
            ("owner/repo@$(touch SHOULD_NOT_EXIST)",
             [["install", "owner/repo", "--tag", "$(touch SHOULD_NOT_EXIST)"]]),
        )
        for packages, expected in cases:
            with self.subTest(packages=packages):
                self.successful(self.packages, POOF_PACKAGES=packages)
                self.assertEqual([call["args"] for call in self.calls()], expected)
                self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_versions_replace_existing_binary(self):
        installed = self.home / ".local/bin/poof"
        for version in ("1.2.3", "v1.2.3", "latest"):
            with self.subTest(version=version):
                installed.parent.mkdir(parents=True, exist_ok=True)
                installed.write_text("#!/bin/sh\necho old-version\n")
                installed.chmod(0o755)
                result = self.successful(self.bootstrap, POOF_VERSION=version)
                self.assertEqual(installed.read_text(), self.mock)
                calls = self.calls()
                self.assertEqual(len(calls), 2 if version == "latest" else 1)
                tag = "v9.8.7" if version == "latest" else "v1.2.3"
                self.assertIn(f"/download/{tag}/", calls[-1]["args"][1])
                self.assertIn("poof 9.8.7", result.stdout)

    def test_path_logging(self):
        for system, xdg, expected in (
            ("Linux", "", self.home / ".local/share/poof/bin"),
            ("Linux", "relative", self.home / ".local/share/poof/bin"),
            ("Linux", str(self.root / "custom data"), self.root / "custom data/poof/bin"),
            ("Darwin", str(self.root / "ignored"), self.home / "Library/Application Support/poof/bin"),
        ):
            with self.subTest(system=system, xdg=xdg):
                result = self.successful(self.bootstrap, TEST_OS=system, XDG_DATA_HOME=xdg)
                paths = [line.removeprefix("##vso[task.prependpath]")
                         for line in result.stdout.splitlines() if line.startswith("##vso[task.prependpath]")]
                self.assertEqual(paths, [str(self.home / ".local/bin"), str(expected)])
                self.assertTrue(expected.is_dir())

    def test_unsupported_platforms_fail_before_download(self):
        for system, arch in (("Linux", "armv7l"), ("Linux", "i686"), ("Windows_NT", "amd64")):
            with self.subTest(system=system, arch=arch):
                result = self.run_script(self.bootstrap, TEST_OS=system, TEST_ARCH=arch)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsupported", result.stdout)
                self.assertEqual(self.calls(), [])

    def test_failures_propagate(self):
        for override in ({"TEST_API_FAIL": "1"}, {"TEST_DOWNLOAD_FAIL": "1"},
                         {"TEST_RELEASE": ""}, {"TEST_RELEASE": "null"}):
            with self.subTest(override=override):
                result = self.run_script(self.bootstrap, **override)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("##vso[task.prependpath]", result.stdout)
        result = self.run_script(self.packages, POOF_PACKAGES="cli/cli,ms-jpq/sad", TEST_PACKAGE_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
