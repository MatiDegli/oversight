"""Method receivers, inheritance, overrides and super lookup."""

from pathlib import Path

from graph_helpers import scan, summary


def test_methods_a_framework_calls_by_name_are_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "tests/conftest.py": """
                import pytest

                @pytest.fixture
                def widget(): ...

                @pytest.fixture()
                def stock(widget): ...
            """,
            "tests/test_shop.py": """
                class Handler:
                    def on_save(self): ...

                def test_saves(widget, stock, tmp_path):
                    getattr(Handler(), "on_save")()

                class TestGroup:
                    def test_method(self, widget): ...
            """,
        },
    )
    assert summary(snap, "tests.test_shop:test_saves") == {
        "tests.conftest:widget": "dynamic_dispatch",
        "tests.conftest:stock": "dynamic_dispatch",
        "tests.test_shop:Handler.on_save": "dynamic_dispatch",
        "tests.test_shop:Handler": "exact",
    }
    assert summary(snap, "tests.conftest:stock") == {"tests.conftest:widget": "dynamic_dispatch"}
    assert summary(snap, "tests.test_shop:TestGroup.test_method") == {
        "tests.conftest:widget": "dynamic_dispatch"
    }


def test_self_calls_are_exact_unless_a_subclass_overrides_the_method(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class Base:
                    def rate(self): ...
                    def fee(self): ...

                    def quote(self):
                        return self.rate() + self.fee() + self.data.get()

                    @classmethod
                    def build(cls):
                        return cls.fee()

                    @staticmethod
                    def pure(self):
                        return self.fee()

                class Premium(Base):
                    def rate(self):
                        return super().rate()

                    def total(this):
                        return this.fee()
            """
        },
    )
    assert summary(snap, "m:Base.quote") == {
        "m:Base.rate": "several_candidates",
        "m:Premium.rate": "several_candidates",
        "m:Base.fee": "exact",
    }
    assert summary(snap, "m:Base.build") == {"m:Base.fee": "exact"}
    assert summary(snap, "m:Base.pure") == {"m:Base.fee": "unknown_receiver"}
    assert summary(snap, "m:Premium.rate") == {"m:Base.rate": "exact"}
    assert summary(snap, "m:Premium.total") == {"m:Base.fee": "exact"}


def test_calls_through_a_class_name_look_up_its_bases(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class Base:
                    def label(self): ...

                class Child(Base): ...

                def use(item):
                    return Child.label(item)
            """
        },
    )
    assert summary(snap, "m:use") == {"m:Base.label": "exact"}


def test_edges_include_inheritance_and_merge_calls_by_strength(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "a.py": "class Base:\n    def run(self): ...\n",
            "b.py": """
                from a import Base

                class Child(Base):
                    def go(self, other):
                        other.run()
                        return Base.run(self)
            """,
        },
    )
    assert {e.kind for e in snap.edges} == {"imports", "calls", "inherits"}
    assert summary(snap, "b:Child.go") == {"a:Base.run": "exact"}


def test_a_diamond_takes_every_definition_no_subclass_overrides(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class A:
                    def f(self): ...

                class B(A):
                    def go(self):
                        return self.f()

                class C(A):
                    def f(self): ...

                class D(B, C):
                    def run(self):
                        return self.f()
            """
        },
    )
    # For a D, B.go reaches C.f: D's order is D, B, C, A.
    assert summary(snap, "m:B.go") == {"m:A.f": "several_candidates", "m:C.f": "several_candidates"}
    assert summary(snap, "m:D.run") == {"m:C.f": "exact"}


def test_super_takes_the_classes_a_subclass_puts_after_the_class(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class A:
                    def f(self): ...

                class B(A):
                    def go(self):
                        return super().f()

                class C(A):
                    def f(self): ...

                class D(B, C): ...
            """
        },
    )
    assert summary(snap, "m:B.go") == {"m:A.f": "several_candidates", "m:C.f": "several_candidates"}


def test_a_base_with_several_definitions_or_rebound_is_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                import sys

                if sys.platform == "win32":
                    class Base:
                        def f(self): ...
                else:
                    class Base:
                        def g(self): ...

                class Plain:
                    def f(self): ...

                Plain = sys.intern(Plain)

                class D(Base):
                    def run(self):
                        return self.f()

                class E(Plain):
                    def run(self):
                        return self.f()
            """
        },
    )
    assert summary(snap, "m:D.run") == {"m:Base.f": "several_candidates"}
    assert summary(snap, "m:E.run") == {"m:Plain.f": "rebound_name"}


def test_an_attribute_assigned_on_self_may_replace_the_method(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def replacement(): ...

                class A:
                    def f(self): ...
                    def g(self): ...

                    def setup(self):
                        self.f = replacement

                    def run(self):
                        return self.f(), self.g()
            """
        },
    )
    assert summary(snap, "m:A.run") == {"m:A.f": "rebound_name", "m:A.g": "exact"}


def test_a_self_call_takes_the_lookup_from_every_subclass(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class A:
                    def f(self): ...

                class B(A):
                    def go(self):
                        return self.f()

                    @classmethod
                    def make(cls):
                        return cls.f()

                class C(A):
                    def f(self): ...

                class D(B, C): ...
            """
        },
    )
    # The receiver may be a D, whose order (D, B, C, A) finds C.f before A.f.
    both = {"m:A.f": "several_candidates", "m:C.f": "several_candidates"}
    assert summary(snap, "m:B.go") == both
    assert summary(snap, "m:B.make") == both


def test_alternative_bases_drop_no_definition(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class A:
                    def f(self): ...

                if flag:
                    class Base(A): ...
                else:
                    class Base(A):
                        def f(self): ...

                class D(Base):
                    def run(self):
                        return self.f()

                    def again(self):
                        return super().f()
            """
        },
    )
    # The first Base reaches A.f, so the second Base's override cannot drop it.
    both = {"m:A.f": "several_candidates", "m:Base.f": "several_candidates"}
    assert summary(snap, "m:D.run") == both
    assert summary(snap, "m:D.again") == both


def test_a_receiver_with_several_values_is_followed_on_each(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "a.py": "def load(): ...\n",
            "b.py": "def load(): ...\n",
            "c.py": "def save(): ...\n",
            "d.py": "class Box:\n    def load(self): ...\n",
            "app.py": """
                if fast:
                    import a as engine
                    import a as tool
                else:
                    import c as engine
                    from b import load as tool

                def run(flag):
                    if flag:
                        import a as lib
                    else:
                        import b as lib
                    return lib.load()

                def store():
                    return engine.save()

                def pick():
                    return tool.load()

                class Holder:
                    if fast:
                        import a as lib
                    else:
                        import b as lib

                    def go(self):
                        return self.lib.load()
            """,
        },
    )
    both = {"a:load": "several_candidates", "b:load": "several_candidates"}
    assert summary(snap, "app:run") == both
    assert summary(snap, "app:Holder.go") == both
    # Only one of the two modules defines save, but the receiver may be the other one.
    assert summary(snap, "app:store") == {"c:save": "several_candidates"}
    # One value is a function: an attribute on it has a receiver of unknown type.
    assert summary(snap, "app:pick") == {
        "a:load": "several_candidates",
        "d:Box.load": "unknown_receiver",
    }
