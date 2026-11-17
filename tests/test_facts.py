import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from _load import load

facts = load("roast-repo", "facts")

# Built by concatenation so this test file never contains a secret-shaped literal.
FAKE_AWS = "AKIA" + "Q7X2M9TZ4LWE8RNB"
FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class FactsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "demo"
        self.repo.mkdir()
        self.write("README.md", "# Demo\n\nDoes things.\n")
        self.write("app/main.py", "import os\n# TODO: handle errors\nx = 1  # FIXME later\n"
                                  "PATTERN = 'TODO|FIXME'\n"
                                  "# a comment that only mentions TODO markers in prose\n"
                                  "MARK = re.compile(r'(#|//) TODO')\n")
        self.write("app/big.py", "y = 2\n" * 300)
        self.write("app/config.py", f'API_KEY = "{FAKE_AWS}"\ntoken = "{FAKE_GH}"\n'
                                    'password = "changeme-placeholder"\n')
        self.write("tests/test_main.py", "def test_x():\n    assert True\n")
        self.write("web/app.test.ts", "it('works', () => {})\n")
        self.write("node_modules/lib/index.js", "// TODO vendored, should be ignored\n")
        self.write("requirements.txt", "requests==2.32.0\nflask\n# comment\n-r other.txt\n")
        self.write("package.json", json.dumps({"dependencies": {"a": "1"}, "devDependencies": {"b": "2"},
                                               "scripts": {"test": "echo \"Error: no test specified\""}}))
        self.write(".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: pytest\n")
        self.write(".env", "SECRET=1\n")
        self.write(".env.example", "SECRET=\n")
        (self.repo / "logo.bin").write_bytes(b"\x00\x01\x02" * 100)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class GatherTests(FactsCase):
    def test_counts_and_languages(self):
        f = facts.gather(self.repo)
        self.assertEqual(f["repo"], "demo")
        self.assertFalse(f["is_git_repo"])
        self.assertEqual(f["languages"]["Python"], 4)
        self.assertEqual(f["languages"]["TypeScript"], 1)
        self.assertIsNone(f["git"])

    def test_skips_dependency_dirs(self):
        f = facts.gather(self.repo)
        self.assertFalse(any("node_modules" in loc for loc in f["markers"]["locations"]))

    def test_markers(self):
        f = facts.gather(self.repo)
        self.assertEqual(f["markers"]["counts"], {"TODO": 1, "FIXME": 1})
        self.assertIn("app/main.py:2", f["markers"]["locations"])
        self.assertIn("app/main.py:3", f["markers"]["locations"])

    def test_largest_files_sorted(self):
        f = facts.gather(self.repo)
        self.assertEqual(f["largest_source_files"][0], {"path": "app/big.py", "lines": 300})

    def test_tests_detected(self):
        t = facts.gather(self.repo)["tests"]
        self.assertEqual(t["test_files"], 2)
        self.assertEqual(t["source_files_excluding_tests"], 3)

    def test_ci_detected(self):
        ci = facts.gather(self.repo)["ci"]
        self.assertEqual(ci["files"], [".github/workflows/ci.yml"])
        self.assertTrue(ci["runs_tests"])

    def test_travis_default_script_counts_as_running_tests(self):
        import tempfile
        for travis, expected in [('language: node_js\nnode_js:\n - "6"\n', True),
                                 ('language: java\njdk:\n - oraclejdk8\n', True),
                                 ('language: node_js\nscript: echo hi\n', False)]:
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / ".travis.yml").write_text(travis, encoding="utf-8")
                (Path(d) / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
                self.assertEqual(facts.gather(Path(d))["ci"]["runs_tests"], expected, travis)

    def test_docs(self):
        d = facts.gather(self.repo)["docs"]
        self.assertEqual(d["readme"], "README.md")
        self.assertIsNone(d["license"])
        self.assertEqual(d["env_example"], ".env.example")

    def test_dependencies(self):
        deps = facts.gather(self.repo)["dependencies"]
        by_type = {m["type"]: m for m in deps["manifests"]}
        self.assertEqual(by_type["pip"]["dependencies"], 2)
        self.assertEqual(by_type["pip"]["unpinned"], 1)
        self.assertEqual(by_type["npm"]["dependencies"], 1)
        self.assertFalse(by_type["npm"]["has_real_test_script"])
        self.assertFalse(deps["has_lockfile"])

    def test_secrets_found_and_redacted(self):
        s = facts.gather(self.repo)["secrets"]
        kinds = {h["kind"] for h in s["suspects"]}
        self.assertIn("aws-access-key-id", kinds)
        self.assertIn("github-token", kinds)
        dump = json.dumps(s)
        self.assertNotIn(FAKE_AWS, dump)
        self.assertNotIn(FAKE_GH, dump)
        self.assertIn("[redacted]", dump)
        self.assertTrue(all(h["location"].startswith("app/config.py:") for h in s["suspects"]))

    def test_placeholder_not_flagged(self):
        s = facts.gather(self.repo)["secrets"]
        self.assertFalse(any(h["location"] == "app/config.py:3" for h in s["suspects"]))

    def test_sensitive_files(self):
        s = facts.gather(self.repo)["secrets"]
        self.assertIn(".env", s["sensitive_files_tracked"])
        self.assertNotIn(".env.example", s["sensitive_files_tracked"])


class HelperTests(unittest.TestCase):
    def test_is_test_file(self):
        for p in ["tests/a.py", "src/test_x.py", "pkg/x_test.go", "ui/a.spec.tsx", "spec/models/user_spec.rb",
                  "src/FooTest.java", "test.js", "tests.py", "lib/spec.ts"]:
            self.assertTrue(facts.is_test_file(p), p)
        for p in ["src/app.py", "src/contest.py", "lib/testing_utils_impl.go", "latest.js", "attest.py"]:
            self.assertFalse(facts.is_test_file(p), p)

    def test_requirements_parser(self):
        r = facts.parse_requirements("a==1\nb>=2\nc @ git+https://x\n\n-e .\n")
        self.assertEqual(r, {"dependencies": 3, "unpinned": 1})

    def test_go_mod_parser(self):
        text = "module x\n\nrequire (\n\tgithub.com/a/b v1.0.0\n\tgithub.com/c/d v2.0.0\n)\nrequire e/f v1\n"
        self.assertEqual(facts.parse_go_mod(text)["dependencies"], 3)

    def test_bad_package_json(self):
        self.assertIn("error", facts.parse_package_json("{nope"))


class OutputTests(FactsCase):
    def test_markdown_summary(self):
        md = facts.to_markdown(facts.gather(self.repo))
        self.assertIn("# Facts for demo", md)
        self.assertIn("LICENSE: MISSING", md)
        self.assertIn("Secret-looking strings: 2", md)

    def test_cli_writes_json(self):
        out = Path(self._tmp.name) / "out" / "facts.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(facts.main([str(self.repo), "--out", str(out)]), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["schema"], 1)

    def test_cli_rejects_missing_dir(self):
        self.assertEqual(facts.main([str(self.repo / "nope")]), 2)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitTests(FactsCase):
    def git(self, *args):
        env_args = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", *env_args, *args], cwd=self.repo, check=True, capture_output=True)

    def test_git_stats_without_names(self):
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "wip")
        self.write("app/main.py", "changed\n")
        self.git("commit", "-q", "-am", "Add a proper message")
        f = facts.gather(self.repo)
        self.assertTrue(f["is_git_repo"])
        g = f["git"]
        self.assertEqual(g["commits"], 2)
        self.assertEqual(g["contributors"], 1)
        self.assertEqual(g["lazy_commit_messages"]["count"], 1)
        self.assertNotIn("Test", json.dumps(g))
        self.assertEqual(g["churn_hotspots"][0]["path"], "app/main.py")
        # node_modules was committed; it is counted, not scanned
        self.assertGreaterEqual(f["files"]["tracked_dependency_files"], 1)

    def test_git_stats_on_subfolder(self):
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Initial commit")
        self.write("web/app.test.ts", "it('still works', () => {})\n")
        self.git("commit", "-q", "-am", "Update web test")
        self.write("app/main.py", "changed\n")
        self.git("commit", "-q", "-am", "Update app main")
        f = facts.gather(self.repo / "app")
        self.assertEqual(f["repo"], "app")
        self.assertTrue(f["is_git_repo"])
        g = f["git"]
        self.assertEqual(g["commits"], 2)
        self.assertEqual(g["contributors"], 1)
        self.assertEqual(g["churn_hotspots"][0]["path"], "main.py")


if __name__ == "__main__":
    unittest.main()
