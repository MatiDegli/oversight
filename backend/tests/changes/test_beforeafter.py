"""pytest 8.4.2 JUnit captures plus adversarial reports and the comparison matrix.

Captures: python -m pytest -q -p no:cacheprovider -o junit_family=xunit{1,2}
--junitxml=report.xml, over a scratch tests/test_sample.py. Only XML whitespace
was formatted; pytest 8 was already cached, with no additional installation.
"""

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from oversight.changes._beforeafter import (
    MAX_TESTS,
    MAX_XML_BYTES,
    FactKind,
    Outcome,
    RunProblem,
    affected_tests,
    facts_line,
    parse_junit,
)
from oversight.changes._beforeafter import (
    TestFact as Fact,
)
from oversight.changes._beforeafter import (
    TestRun as Run,
)

XUNIT1 = """<testsuites name="pytest tests">
  <testsuite
      name="pytest"
      errors="0"
      failures="0"
      skipped="0"
      tests="4"
      time="0.008"
      timestamp="2026-10-27T16:09:14.593627+00:00"
      hostname="DESKTOP-23G7J80">
    <testcase
      classname="tests.test_sample"
      name="test_param[a.b::c]"
      file="tests/test_sample.py"
      line="40"
      time="0.000" />
    <testcase
      classname="tests.test_sample"
      name="test_param[a&amp;&lt;b&gt;]"
      file="tests/test_sample.py"
      line="40"
      time="0.000" />
    <testcase
      classname="tests.test_sample.TestGroup"
      name="test_method"
      file="tests/test_sample.py"
      line="46"
      time="0.000" />
    <testcase
      classname="tests.test_sample.TestGroup.TestNested"
      name="test_nested"
      file="tests/test_sample.py"
      line="50"
      time="0.000" />
  </testsuite>
</testsuites>"""

XUNIT2 = """<testsuites name="pytest tests">
  <testsuite
      name="pytest"
      errors="3"
      failures="3"
      skipped="2"
      tests="14"
      time="0.019"
      timestamp="2026-10-27T16:06:23.196413+00:00"
      hostname="DESKTOP-23G7J80">
    <testcase classname="tests.test_sample" name="test_pass" time="0.000" />
    <testcase classname="tests.test_sample" name="test_fail" time="0.000">
      <failure message="assert False">def test_fail():
&gt;       assert False
E       assert False

tests/test_sample.py:9: AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_sample" name="test_error" time="0.000">
      <error message="failed on setup with &quot;RuntimeError: setup broke&quot;">@pytest.fixture
    def broken():
&gt;       raise RuntimeError('setup broke')
E       RuntimeError: setup broke

tests/test_sample.py:14: RuntimeError</error>
    </testcase>
    <testcase classname="tests.test_sample" name="test_skip" time="0.000">
      <skipped
      type="pytest.skip"
      message="not available">
/tmp/oversight-beforeafter-samples/tests/test_sample.py:21: not available</skipped>
    </testcase>
    <testcase classname="tests.test_sample" name="test_xfail" time="0.000">
      <skipped type="pytest.xfail" message="known issue" />
    </testcase>
    <testcase classname="tests.test_sample" name="test_xpass" time="0.000" />
    <testcase classname="tests.test_sample" name="test_strict_xpass" time="0.000">
      <failure message="[XPASS(strict)] known issue">[XPASS(strict)] known issue</failure>
    </testcase>
    <testcase classname="tests.test_sample" name="test_param[a.b::c]" time="0.000" />
    <testcase classname="tests.test_sample" name="test_param[a&amp;&lt;b&gt;]" time="0.000" />
    <testcase classname="tests.test_sample.TestGroup" name="test_method" time="0.000" />
    <testcase classname="tests.test_sample.TestGroup.TestNested" name="test_nested" time="0.000" />
    <testcase classname="tests.test_sample" name="test_double_failure" time="0.000">
      <failure message="assert False">teardown_error = None

    def test_double_failure(teardown_error):
&gt;       assert False
E       assert False

tests/test_sample.py:62: AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_sample" name="test_double_failure" time="0.000">
      <error
      message="failed on teardown with &quot;RuntimeError: teardown broke&quot;">@pytest.fixture
    def teardown_error():
        yield
&gt;       raise RuntimeError('teardown broke')
E       RuntimeError: teardown broke

tests/test_sample.py:58: RuntimeError</error>
    </testcase>
    <testcase classname="tests.test_sample" name="test_pass_with_teardown_error" time="0.000">
      <error
      message="failed on teardown with &quot;RuntimeError: teardown broke&quot;">@pytest.fixture
    def teardown_error():
        yield
&gt;       raise RuntimeError('teardown broke')
E       RuntimeError: teardown broke

tests/test_sample.py:58: RuntimeError</error>
    </testcase>
  </testsuite>
</testsuites>"""


@pytest.mark.parametrize("xml", [XUNIT1, XUNIT2])
def test_real_pytest_8_outcomes_and_exact_node_ids(xml: str) -> None:
    run = parse_junit(xml)
    expected: dict[str, Outcome] = {
        "test_pass": "passed",
        "test_fail": "failed",
        "test_error": "error",
        "test_skip": "skipped",
        "test_xfail": "skipped",
        "test_xpass": "passed",
        "test_strict_xpass": "failed",
        "test_param[a.b::c]": "passed",
        "test_param[a&<b>]": "passed",
        "TestGroup::test_method": "passed",
        "TestGroup::TestNested::test_nested": "passed",
        "test_double_failure": "error",
        "test_pass_with_teardown_error": "error",
    }
    if xml == XUNIT1:
        expected = {
            key: value
            for key, value in expected.items()
            if key.startswith(("test_param[", "TestGroup::"))
        }
    assert run.problems == ()
    assert dict(run.outcomes) == {
        f"tests/test_sample.py::{key}": value for key, value in expected.items()
    }


def report(case: str) -> str:
    return f"<testsuites><testsuite>{case}</testsuite></testsuites>"


def case(body: str = "", **attrs: str) -> str:
    attributes = {"classname": "tests.test_core", "name": "test_one", **attrs}
    joined = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    return f"<testcase {joined}>{body}</testcase>"


@pytest.mark.parametrize("xml", ["", "<", "<testsuite>", "<testsuite></x>", "\x00", "\ud800"])
def test_invalid_xml_is_a_problem_without_partial_results(xml: str) -> None:
    assert parse_junit(xml) == Run({}, (RunProblem("invalid_xml"),))


@pytest.mark.parametrize(
    "declaration",
    [
        "<!DOCTYPE testsuite>",
        '<!DOCTYPE testsuite SYSTEM "file:///does-not-exist">',
        '<!DOCTYPE testsuite [<!ENTITY x "expanded">]>',
        '<!ENTITY x SYSTEM "https://example.invalid/entity">',
        "<!doctype testsuite>",
        "<! DOCTYPE testsuite>",
        '<!-- <!ENTITY x "hidden"> -->',
    ],
)
def test_refuses_declarations_before_parsing(declaration: str) -> None:
    assert parse_junit(declaration + "<testsuite/>") == Run(
        {}, (RunProblem("forbidden_declaration"),)
    )


def test_size_cap_is_inclusive_and_counts_utf8_bytes() -> None:
    envelope = "<testsuite><!--" + "--></testsuite>"
    for unit in ("x", "é"):
        available = MAX_XML_BYTES - len(envelope)
        text = unit * (available // len(unit.encode())) + "x" * (available % len(unit.encode()))
        xml = f"<testsuite><!--{text}--></testsuite>"
        assert len(xml.encode()) == MAX_XML_BYTES
        assert parse_junit(xml) == Run({})
        assert parse_junit(xml + " ") == Run({}, (RunProblem("input_too_large"),))
    assert parse_junit(" " * (MAX_XML_BYTES + 1)).problems == (RunProblem("input_too_large"),)


def test_testcase_cap_is_inclusive_and_counts_duplicate_elements() -> None:
    cases = "".join(case(name=f"test_{i}") for i in range(MAX_TESTS))
    at_limit = parse_junit(report(cases))
    assert at_limit.problems == () and len(at_limit.outcomes) == MAX_TESTS
    assert parse_junit(report(cases + case())) == Run({}, (RunProblem("too_many_tests"),))
    assert parse_junit(report(case() * (MAX_TESTS + 1))).problems == (RunProblem("too_many_tests"),)


@pytest.mark.parametrize(
    "xml",
    [
        "<unknown/>",
        "<testcase/>",
        "<testsuites><testcase/></testsuites>",
        "<testsuite><wrapper><testcase/></wrapper></testsuite>",
        report(case("<typo/>")),
        report(case("<system-out><testcase/></system-out>")),
    ],
)
def test_invalid_structure_cannot_silently_pass(xml: str) -> None:
    assert parse_junit(xml) == Run({}, (RunProblem("invalid_structure"),))


@pytest.mark.parametrize(
    "attrs",
    [
        {"name": ""},
        {"classname": ""},
        {"classname": "tests.custom.Checks"},
        {"classname": "tests.test_core.UnconventionalClass"},
        {"file": "../tests/test_core.py"},
        {"file": "/tests/test_core.py"},
        {"file": r"tests\test_core.py"},
        {"file": "tests/other.py"},
        {"file": "tests/test_core.py", "classname": "prefix.tests.test_core"},
    ],
)
def test_unrecoverable_identity_is_reported(attrs: dict[str, str]) -> None:
    assert parse_junit(report(case(**attrs))) == Run({}, (RunProblem("ambiguous_test_id"),))


@pytest.mark.parametrize(
    "path",
    [
        "../tests/test_core.py",
        "/tests/test_core.py",
        r"tests\test_core.py",
        "C:/tests/test_core.py",
        "tests/./test_core.py",
        "tests/../test_core.py",
        "tests//test_core.py",
        "tests/test_core.txt",
    ],
)
@pytest.mark.parametrize("collection", [False, True])
def test_invalid_paths_are_rejected_even_when_module_matches(path: str, collection: bool) -> None:
    module = path[:-3].replace("/", ".")
    attrs = {"classname": "", "name": module} if collection else {"classname": module}
    xml = case("<error/>" if collection else "", file=path, **attrs)
    assert parse_junit(report(xml)) == Run({}, (RunProblem("ambiguous_test_id"),))


@pytest.mark.parametrize("tag", ["error", "skipped"])
def test_collection_file_must_match_dotted_name(tag: str) -> None:
    xml = case(f"<{tag}/>", classname="", name="tests.test_other", file="tests/test_core.py")
    assert parse_junit(report(xml)) == Run({}, (RunProblem("ambiguous_test_id"),))


def test_xunit1_preserves_dotted_paths_and_custom_classes() -> None:
    xml = case(file="odd.dir/spec.v2.py", classname="odd.dir.spec.v2.Checks.Nested")
    assert parse_junit(report(xml)) == Run(
        {"odd.dir/spec.v2.py::Checks::Nested::test_one": "passed"}
    )


def test_xunit2_supports_suffix_modules_and_ignores_metadata() -> None:
    xml = case(
        '<properties><property name="x" value="y"/></properties>'
        "<system-out>failure &amp; skipped &#233;</system-out><system-err>error</system-err>",
        classname="test_package.unit_test.TestA",
        name="test_one[&#233;]",
    )
    assert parse_junit(report(xml)) == Run(
        {"test_package/unit_test.py::TestA::test_one[é]": "passed"}
    )


@pytest.mark.parametrize("tag,outcome", [("error", "error"), ("skipped", "skipped")])
@pytest.mark.parametrize("attrs", [{}, {"file": "tests/test_broken.py"}])
def test_collection_reports_keep_the_file_node_id(
    tag: str, outcome: Outcome, attrs: dict[str, str]
) -> None:
    # pytest 8 emits an empty classname and a dotted name for collection reports.
    xml = case(f"<{tag}/>", classname="", name="tests.test_broken", **attrs)
    assert parse_junit(report(xml)) == Run({"tests/test_broken.py": outcome})


@pytest.mark.parametrize("reverse", [False, True])
def test_call_failure_and_teardown_error_merge_in_either_order(reverse: bool) -> None:
    cases = [case("<failure/>"), case("<error/>")]
    assert parse_junit(report("".join(cases[::-1] if reverse else cases))) == Run({ID: "error"})
    assert parse_junit(report(case("<skipped/><error/>"))) == Run({ID: "error"})
    assert parse_junit(report(case("<skipped/><failure/>"))) == Run({ID: "failed"})


@pytest.mark.parametrize("body", ["<failure/><error/>", "<error/><failure/>"])
def test_error_takes_priority_over_failure_in_one_testcase(body: str) -> None:
    assert parse_junit(report(case(body))) == Run({ID: "error"})


def test_other_duplicates_are_reported_and_do_not_prove_a_pass() -> None:
    for xml in (case() * 2, case("<failure/>") + case(), case("<failure/>") + case("<error/>") * 2):
        run = parse_junit(report(xml))
        assert run.problems == (RunProblem("duplicate_test_id", ID),)
        assert affected_tests([ID], None, run)[0].reason == "after_report_problem"


ID = "tests/test_core.py::test_one"
STATES: tuple[Outcome | None, ...] = ("passed", "failed", "error", "skipped", None)


@pytest.mark.parametrize(
    "old,expected",
    [
        (
            "passed",
            (
                "passes_before_and_after",
                "fails_after",
                "fails_after",
                "skipped",
                "not_collected_after",
            ),
        ),
        (
            "failed",
            (
                "fails_before_passes_after",
                "fails_after",
                "fails_after",
                "skipped",
                "not_collected_after",
            ),
        ),
        (
            "error",
            (
                "unknown",
                "fails_after",
                "fails_after",
                "skipped",
                "not_collected_after",
            ),
        ),
        ("skipped", ("skipped", "fails_after", "fails_after", "skipped", "not_collected_after")),
        (None, ("unknown", "fails_after", "fails_after", "skipped", "not_collected_after")),
    ],
)
def test_all_before_and_after_combinations(
    old: Outcome | None, expected: tuple[FactKind, ...]
) -> None:
    before = Run({ID: old} if old else {})
    for new, kind in zip(STATES, expected, strict=True):
        after = Run({ID: new} if new else {})
        reason = (
            ("before_setup_or_import_error" if old == "error" else "before_test_missing")
            if kind == "unknown"
            else None
        )
        assert affected_tests([ID], before, after) == (Fact(ID, kind, old, new, reason),)


def test_missing_before_run_differs_from_uncollected_test_in_a_new_file() -> None:
    test_id = "tests/test_new_file.py::test_added"
    after = Run({test_id: "passed"})
    assert affected_tests([test_id], None, after) == (
        Fact(test_id, "unknown", None, "passed", "before_run_missing"),
    )
    assert affected_tests([test_id], Run({}), after) == (
        Fact(test_id, "unknown", None, "passed", "before_test_missing"),
    )
    for new, kind in zip(
        STATES[1:], ("fails_after", "fails_after", "skipped", "not_collected_after"), strict=True
    ):
        assert affected_tests([ID], None, Run({ID: new} if new else {})) == (
            Fact(ID, kind, None, new),
        )


@pytest.mark.parametrize("old,new", tuple(product(STATES, repeat=2)))
def test_report_problems_never_produce_before_after_evidence(
    old: Outcome | None, new: Outcome | None
) -> None:
    before = Run({ID: old} if old else {}, (RunProblem("invalid_xml"),))
    after = Run({ID: new} if new else {}, (RunProblem("invalid_xml"),))
    assert affected_tests([ID], before, after) == (
        Fact(ID, "unknown", old, new, "after_report_problem"),
    )
    clean = Run(dict(after.outcomes))
    fact = affected_tests([ID], before, clean)[0]
    assert fact.kind == (
        "unknown"
        if new == "passed"
        else {
            "failed": "fails_after",
            "error": "fails_after",
            "skipped": "skipped",
            None: "not_collected_after",
        }[new]
    )
    assert fact.reason == ("before_report_problem" if new == "passed" else None)


def test_facts_are_deduplicated_sorted_and_inputs_unchanged() -> None:
    ids = [ID + "[b]", ID, ID + "[b]"]
    after = Run(dict.fromkeys(ids, "passed"))
    before = Run(dict.fromkeys(ids, "failed"))
    expected = tuple(
        Fact(key, "fails_before_passes_after", "failed", "passed") for key in sorted(set(ids))
    )
    assert affected_tests(iter(ids), before, after) == expected
    assert ids == [ID + "[b]", ID, ID + "[b]"]
    assert set(before.outcomes.values()) == {"failed"}
    assert set(after.outcomes.values()) == {"passed"}
    assert affected_tests([], None, after) == ()


def test_results_are_frozen_and_mapping_is_a_defensive_immutable_copy() -> None:
    outcomes: dict[str, Outcome] = {ID: "passed"}
    run = Run(outcomes)
    outcomes[ID] = "failed"
    assert run.outcomes[ID] == "passed"
    with pytest.raises(TypeError):
        run.outcomes[ID] = "error"  # type: ignore[index]
    for value, field in (
        (run, "problems"),
        (RunProblem("invalid_xml"), "code"),
        (Fact(ID, "skipped", None, "skipped"), "kind"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, None)


def test_neutral_summary_covers_every_kind_and_errors_separately() -> None:
    facts = (
        Fact("a", "fails_before_passes_after", "failed", "passed"),
        Fact("b", "fails_before_passes_after", "error", "passed"),
        Fact("c", "passes_before_and_after", "passed", "passed"),
        Fact("d", "fails_after", "passed", "error"),
        Fact("e", "not_collected_after", "passed", None),
        Fact("f", "skipped", "skipped", "passed"),
        Fact("g", "unknown", None, "passed", "before_run_missing"),
    )
    assert facts_line(iter(facts)) == (
        "2 tests fail before and pass after; 1 test passes before too; 1 test fails after; "
        "1 test not collected after; 1 test skipped before or after; "
        "1 test has unknown comparison; "
        "1 error before; 1 error after"
    )
    assert facts_line(reversed(facts)) == facts_line(facts)
    assert facts_line(()) == "No added or modified tests"
    assert "\n" not in facts_line(facts)


def test_summary_plural_and_singular_forms() -> None:
    for kind in (
        "fails_before_passes_after",
        "passes_before_and_after",
        "fails_after",
        "not_collected_after",
        "skipped",
        "unknown",
    ):
        fact = Fact(ID, kind, "error", "error")
        assert facts_line([fact]).startswith("1 test ")
        assert facts_line([fact, fact]).startswith("2 tests ")
        assert facts_line([fact, fact]).endswith("2 errors before; 2 errors after")
