"""The frozen bench documents the product's expanded probe reach."""

from pathlib import Path


def test_bench_readme_documents_constructor_arguments_and_variable_receivers() -> None:
    readme = Path(__file__).resolve().parents[1] / "eval" / "behavior" / "README.md"
    text = " ".join(readme.read_text(encoding="utf-8").split())
    assert (
        "The product now also builds project objects from constructor and single-return fixture "
        "arguments, and follows method calls on variables assigned from a literal constructor call,"
        " so a case flagged unreachable may be probed."
    ) in text
