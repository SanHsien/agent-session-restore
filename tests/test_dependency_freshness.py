"""Contract tests for tools/check_dependency_freshness.py.

Each test names the property it protects. None of them touches the network: the
registry lookups are injected.
"""

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_dependency_freshness", ROOT / "tools" / "check_dependency_freshness.py"
)
assert _SPEC is not None and _SPEC.loader is not None
freshness = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = freshness
_SPEC.loader.exec_module(freshness)

DEPENDABOT_NAMES = {"pypi": {"pip", "uv"}, "npm": {"npm"}, "cargo": {"cargo"}, "go": {"gomod"}}


def pinned(text: str = "actions/checkout@" + "a" * 40 + " # v7.0.1") -> list[dict[str, str]]:
    return freshness.parse_workflow_actions(f"    steps:\n      - uses: {text}\n", "ci.yml")


class VersionPrecisionTests(unittest.TestCase):
    def test_floor_is_compared_only_at_the_precision_it_states(self) -> None:
        # A monthly report that flags 10.4.0 against ">=10" cries wolf until nobody reads it.
        self.assertFalse(freshness.is_newer_version("10.4.0", "10"))
        self.assertTrue(freshness.is_newer_version("11.0.0", "10"))
        self.assertTrue(freshness.is_newer_version("7.0.2", "7.0.1"))
        self.assertFalse(freshness.is_newer_version("7.0.1", "7.0.1"))

    def test_unparsable_versions_never_read_as_outdated(self) -> None:
        self.assertFalse(freshness.is_newer_version("codeql-bundle-v2.27.0", "4.37.9"))
        self.assertFalse(freshness.is_newer_version("5", ""))

    def test_caret_and_tilde_ranges_compare_on_the_series_they_commit_to(self) -> None:
        floor = freshness.range_floor
        self.assertEqual(floor("^5.6.1", False), "5")
        self.assertEqual(floor("^0.3.2", False), "0.3")  # caret on 0.x pins the minor
        self.assertEqual(floor("~5.8.3", False), "5.8")
        self.assertEqual(floor(">=1.2.3", False), "1.2.3")
        self.assertEqual(floor("2.1.0", False), "2.1.0")  # bare npm version is an exact pin
        self.assertEqual(floor("1.10", True), "1")  # bare Cargo version is a caret range
        self.assertEqual(floor("0.12", True), "0.12")

    def test_ranges_without_a_version_have_no_floor(self) -> None:
        for spec in ("", "*", "latest", "workspace:*", "npm:other@1.2.3", "file:../x", "<2"):
            self.assertEqual(freshness.range_floor(spec, False), "", spec)


class DeclarationParsingTests(unittest.TestCase):
    def test_sha_pinned_action_takes_its_version_from_the_trailing_comment(self) -> None:
        (package,) = pinned()
        self.assertEqual(package["name"], "actions/checkout")
        self.assertEqual(package["minimum"], "7.0.1")

    def test_floating_tag_carries_its_version_in_the_ref(self) -> None:
        (package,) = pinned("actions/checkout@v4")
        self.assertEqual(package["minimum"], "4")

    def test_a_comment_on_a_later_line_is_not_read_as_the_pin_version(self) -> None:
        # `\s*#` crosses newlines: a comment two lines down became the "version" of the pin.
        text = "      - uses: actions/checkout@v4\n\n      # download every build artifact\n"
        (package,) = freshness.parse_workflow_actions(text, "release.yml")
        self.assertEqual((package["minimum"], package["hold"]), ("4", ""))
        self.assertEqual(package["requirement"], "actions/checkout@v4")

    def test_workspace_siblings_are_not_registry_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"@x/local": "workspace:*", "left-pad": "^1.3.0"}}),
                encoding="utf-8",
            )
            old = freshness.NPM_MANIFESTS
            freshness.NPM_MANIFESTS = ("package.json",)
            try:
                names = [p["name"] for p in freshness.load_npm(root)]
            finally:
                freshness.NPM_MANIFESTS = old
        self.assertEqual(names, ["left-pad"])

    def test_branch_ref_and_local_actions_are_not_compared(self) -> None:
        (branch,) = pinned("dtolnay/rust-toolchain@stable")
        self.assertEqual(branch["minimum"], "")
        self.assertEqual(pinned("./.github/actions/local"), [])

    def test_action_subpath_is_kept_but_resolved_to_its_repository(self) -> None:
        (package,) = pinned("github/codeql-action/init@" + "b" * 40 + " # v4.37.9")
        self.assertEqual(package["name"], "github/codeql-action/init")
        self.assertEqual(freshness.action_repository(package["name"]), "github/codeql-action")

    def test_hold_comment_on_a_pin_is_carried_and_suppresses_the_floor(self) -> None:
        (package,) = pinned("actions/checkout@v4 # freshness-hold: runner image needs v4")
        self.assertEqual(package["hold"], "runner image needs v4")

    def test_requirements_line_yields_name_floor_and_hold(self) -> None:
        text = "pytest>=9.0.0  # freshness-hold: pytest 9 needs Python 3.10\nruff>=0.4.0\n-r x.txt\n"
        pytest_row, ruff_row = freshness.parse_requirements(text, "requirements.txt")
        self.assertEqual((pytest_row["name"], pytest_row["minimum"]), ("pytest", "9.0.0"))
        self.assertEqual(pytest_row["hold"], "pytest 9 needs Python 3.10")
        self.assertEqual((ruff_row["minimum"], ruff_row["hold"]), ("0.4.0", ""))

    def test_hold_marker_is_read_from_toml_and_go_mod_lines(self) -> None:
        text = 'tauri = "2" # freshness-hold: v3 breaks the tray API\n'
        text += '    "rich>=13.0.0", # freshness-hold: rich 14 drops 3.9\n'
        text += "\tgithub.com/x/y v1.2.3 // freshness-hold: pinned by the vendor\n"
        holds = freshness.scan_holds(text)
        self.assertEqual(holds["tauri"], "v3 breaks the tray API")
        self.assertEqual(holds["rich"], "rich 14 drops 3.9")
        self.assertEqual(holds["github.com/x/y"], "pinned by the vendor")

    def test_go_module_paths_are_case_escaped_for_the_proxy(self) -> None:
        self.assertEqual(freshness._go_escape("github.com/BurntSushi/toml"),
                         "github.com/!burnt!sushi/toml")

    def test_go_mod_indirect_and_pseudo_versions_are_not_compared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text(
                "module m\n\ngo 1.22\n\nrequire (\n"
                "\tgithub.com/a/b v1.4.2\n"
                "\tgithub.com/c/d v0.0.0-20240101120000-abcdef123456\n"
                "\tgithub.com/e/f v2.0.0 // indirect\n)\n",
                encoding="utf-8",
            )
            old = freshness.GO_MANIFESTS
            freshness.GO_MANIFESTS = ("go.mod",)
            try:
                rows = {p["name"]: p["minimum"] for p in freshness.load_go(root)}
            finally:
                freshness.GO_MANIFESTS = old
        self.assertEqual(rows, {"github.com/a/b": "1.4", "github.com/c/d": ""})


class StatusTests(unittest.TestCase):
    def package(self, minimum: str = "1.0", hold: str = "") -> dict[str, str]:
        return {
            "name": "demo", "minimum": minimum, "requirement": f"demo>={minimum}",
            "source": "x", "hold": hold, "kind": "pypi",
        }  # fmt: skip

    def test_declaration_without_a_floor_is_neither_failure_nor_lookup(self) -> None:
        def fetch(_name: str) -> str | None:
            raise AssertionError("no floor, so nothing to look up")

        (row,) = freshness.collect_status([self.package("")], fetch, {})
        self.assertFalse(row["check_failed"])
        self.assertFalse(freshness.needs_review(row))

    def test_unreadable_registry_fails_closed(self) -> None:
        # "Could not look" and "nothing to review" look identical in a green report.
        (row,) = freshness.collect_status([self.package()], lambda _n: None, {})
        self.assertTrue(row["check_failed"])

    def test_aged_floor_needs_review_and_a_current_one_does_not(self) -> None:
        (aged,) = freshness.collect_status([self.package("1.0")], lambda _n: "2.0", {})
        (current,) = freshness.collect_status([self.package("2.0")], lambda _n: "2.0", {})
        self.assertTrue(freshness.needs_review(aged))
        self.assertFalse(freshness.needs_review(current))

    def test_hold_silences_the_row_but_keeps_it_in_the_report(self) -> None:
        (row,) = freshness.collect_status([self.package("1.0", "policy")], lambda _n: "2.0", {})
        self.assertFalse(freshness.needs_review(row))
        self.assertIn("HELD: policy", freshness.render_markdown({"pypi": [row]}))

    def test_deferral_expires_once_the_registry_moves_past_it(self) -> None:
        deferrals = {"demo": ("2.0", "not this month")}
        (covered,) = freshness.collect_status([self.package()], lambda _n: "2.0", deferrals)
        (expired,) = freshness.collect_status([self.package()], lambda _n: "2.1", deferrals)
        self.assertFalse(freshness.needs_review(covered))
        self.assertTrue(freshness.needs_review(expired))

    def test_deferral_without_reason_or_release_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            path.write_text(
                json.dumps({"deferrals": {"a": {"reason": "x"}, "b": {"deferredLatest": "1"}}}),
                encoding="utf-8",
            )
            self.assertEqual(freshness.load_deferrals(path), {})


class RepositoryContractTests(unittest.TestCase):
    def test_every_checked_ecosystem_is_also_watched_by_dependabot(self) -> None:
        # The two lists drift apart silently: Dependabot proposes bumps for one set while
        # this report measures another.
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        watched = set(re.findall(r'package-ecosystem:\s*"?([\w-]+)"?', text))
        for kind in freshness.ECOSYSTEMS:
            self.assertTrue(DEPENDABOT_NAMES[kind] & watched, kind)

    def test_workflow_pins_are_read_from_the_real_workflows(self) -> None:
        self.assertTrue(freshness.load_workflow_actions(ROOT))

    def test_report_includes_every_configured_section(self) -> None:
        sections = {kind: [] for kind in (*freshness.ECOSYSTEMS, "github-action")}
        report = freshness.render_markdown(sections)
        for kind in sections:
            self.assertIn(freshness.SECTION_TITLES[kind], report)


if __name__ == "__main__":
    unittest.main()
