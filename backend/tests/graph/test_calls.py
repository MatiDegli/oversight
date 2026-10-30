"""Call and import resolution (AGENTS.md, "Graph rules"; docs/proposal.md, section 4.3)."""

from pathlib import Path

from graph_helpers import scan, summary


def test_calls_in_nested_scopes_belong_to_the_enclosing_function(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def a(): ...
                def b(): ...
                def c(): ...
                def d(): ...

                def outer():
                    def inner():
                        return a()
                    run = lambda: b()
                    values = [c() for _ in range(2)]
                    total = sum(d() for _ in range(2))
                    return inner, run, values, total

                class K:
                    def method(self):
                        return [a() for _ in range(2)]
            """
        },
    )
    exact = {"m:a": "exact", "m:b": "exact", "m:c": "exact", "m:d": "exact"}
    assert summary(snap, "m:outer") == exact
    assert summary(snap, "m:K.method") == {"m:a": "exact"}


def test_names_resolve_in_the_nested_scope_first(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def price(): ...
                def tax(): ...
                def ship(): ...
                def fee(): ...

                def by_parameter(price):
                    return price()

                def by_comprehension(handlers):
                    return [tax() for tax in handlers]

                def by_local():
                    ship = lambda: 0
                    return ship()

                def by_lambda_parameter():
                    return (lambda fee: fee())(print)

                def by_inner_parameter():
                    def inner(price):
                        return price()
                    return inner
            """
        },
    )
    for src in ("by_parameter", "by_comprehension", "by_local", "by_lambda_parameter"):
        assert summary(snap, f"m:{src}") == {}, src
    assert summary(snap, "m:by_inner_parameter") == {}


def test_a_class_body_is_not_visible_from_its_methods(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def helper(): ...

                class K:
                    def helper(self): ...

                    def method(self):
                        return helper()
            """
        },
    )
    assert summary(snap, "m:K.method") == {"m:helper": "exact"}


def test_a_global_declaration_binds_the_module_name(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def hook(): ...

                def caller():
                    global hook
                    hook = hook or None
                    return hook()

                def other():
                    return hook()
            """
        },
    )
    assert summary(snap, "m:caller") == {"m:hook": "rebound_name"}
    assert summary(snap, "m:other") == {"m:hook": "rebound_name"}


def test_exactness_follows_resolution_only(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def rare(): ...

                def caller(flag):
                    if flag:
                        try:
                            return rare()
                        except ValueError:
                            pass
            """
        },
    )
    assert summary(snap, "m:caller") == {"m:rare": "exact"}


def test_module_level_calls_belong_to_the_module_and_class_body_calls_to_the_class(
    tmp_path: Path,
) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def make(): ...
                def deco(f): return f

                VALUE = make()

                class Config:
                    default = make()

                    @deco
                    def method(self, value=make()): ...

                if __name__ == "__main__":
                    deco(None)
            """
        },
    )
    assert summary(snap, "m") == {"m:make": "exact", "m:deco": "exact"}
    assert summary(snap, "m:Config") == {"m:make": "exact", "m:deco": "exact"}
    assert summary(snap, "m:Config.method") == {}


def test_calling_a_class_is_a_call_to_the_class_node(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class Order:
                    def __init__(self): ...

                def build():
                    return Order()
            """
        },
    )
    assert summary(snap, "m:build") == {"m:Order": "exact"}


def test_relative_imports_differ_for_a_package_init_and_a_plain_module(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "shop/__init__.py": """
                from .pricing import compute

                def init_total():
                    return compute()
            """,
            "shop/pricing.py": "def compute(): ...\n",
            "shop/orders.py": """
                from . import pricing
                from .pricing import compute as price

                def total():
                    return pricing.compute() + price()
            """,
            "shop/sub/__init__.py": "from ..pricing import compute\n",
            "shop/sub/deep.py": """
                from .. import pricing

                def deep():
                    return pricing.compute()
            """,
        },
    )
    assert summary(snap, "shop:init_total") == {"shop.pricing:compute": "exact"}
    assert summary(snap, "shop.orders:total") == {"shop.pricing:compute": "exact"}
    assert summary(snap, "shop.sub.deep:deep") == {"shop.pricing:compute": "exact"}
    imports = {e.id for e in snap.edges if e.kind == "imports"}
    assert imports == {
        "imports:shop->shop.pricing",
        "imports:shop.orders->shop.pricing",
        "imports:shop.sub->shop.pricing",
        "imports:shop.sub.deep->shop.pricing",
    }


def test_import_a_dot_b_binds_a(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/pricing.py": "def compute(): ...\n",
            "client.py": """
                import shop.pricing
                import shop.pricing as sp

                def full():
                    return shop.pricing.compute()

                def aliased():
                    return sp.compute()

                def short():
                    return pricing.compute()
            """,
        },
    )
    assert summary(snap, "client:full") == {"shop.pricing:compute": "exact"}
    assert summary(snap, "client:aliased") == {"shop.pricing:compute": "exact"}
    assert summary(snap, "client:short") == {}
    assert [e.id for e in snap.edges if e.kind == "imports"] == ["imports:client->shop.pricing"]


def test_imports_follow_re_exports_and_local_imports(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "shop/__init__.py": "from .cart import Cart\n",
            "shop/cart.py": "class Cart: ...\n",
            "app.py": """
                from shop import Cart
                import json

                def make():
                    return Cart(), json.dumps({})

                def lazy():
                    from shop.cart import Cart as C
                    return C()
            """,
        },
    )
    assert summary(snap, "app:make") == {"shop.cart:Cart": "exact"}
    assert summary(snap, "app:lazy") == {"shop.cart:Cart": "exact"}


def test_star_imports_are_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "lib.py": "__all__ = ['public']\ndef public(): ...\ndef hidden(): ...\n",
            "app.py": """
                from lib import *

                def use():
                    return public(), hidden()
            """,
        },
    )
    assert summary(snap, "app:use") == {"lib:public": "star_import"}


def test_rebound_names_are_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def handler(): ...

                def use():
                    return handler()

                handler = staticmethod(handler)
            """
        },
    )
    assert summary(snap, "m:use") == {"m:handler": "rebound_name"}


def test_a_name_with_several_definitions_is_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                import sys

                if sys.platform == "win32":
                    def path(): ...
                else:
                    def path(): ...

                def use():
                    return path()
            """
        },
    )
    assert summary(snap, "m:use") == {
        "m:path": "several_candidates",
        "m:path#2": "several_candidates",
    }


def test_attribute_calls_on_unknown_receivers_are_possible(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class Inventory:
                    def reserve(self): ...

                class Shelf:
                    def reserve(self): ...

                def total(): ...

                def place(inventory, rows):
                    inventory.reserve()
                    rows.append(1)
                    make().reserve()
                    inventory.total()
            """
        },
    )
    assert summary(snap, "m:place") == {
        "m:Inventory.reserve": "unknown_receiver",
        "m:Shelf.reserve": "unknown_receiver",
    }


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


def test_a_local_import_keeps_every_target(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "fast.py": "def load(): ...\n",
            "slow.py": "def load(): ...\n",
            "app.py": """
                def run(quick):
                    if quick:
                        from fast import load
                    else:
                        from slow import load
                    return load()
            """,
        },
    )
    assert summary(snap, "app:run") == {
        "fast:load": "several_candidates",
        "slow:load": "several_candidates",
    }


def test_a_star_import_may_replace_an_earlier_definition(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "lib.py": "def f(): ...\n",
            "app.py": """
                def f(): ...

                from lib import *

                def use():
                    return f()
            """,
        },
    )
    assert summary(snap, "app:use") == {"app:f": "star_import", "lib:f": "star_import"}


def test_a_nonlocal_assignment_rebinds_the_outer_name(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "lib.py": "def f(): ...\n",
            "app.py": """
                def outer(other):
                    from lib import f

                    def swap():
                        nonlocal f
                        f = other

                    swap()
                    return f()
            """,
        },
    )
    assert summary(snap, "app:outer") == {"lib:f": "rebound_name"}


def test_a_class_body_sees_only_the_names_bound_before(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def f(): ...

                class A:
                    early = f()

                    def f(self): ...

                    late = f(None)
            """
        },
    )
    assert summary(snap, "m:A") == {"m:f": "exact", "m:A.f": "exact"}


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


def test_a_walrus_in_a_keyword_only_default_binds_in_the_enclosing_scope(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def helper(): ...

                def outer():
                    def inner(*, key=(helper := print)): ...
                    return helper()
            """
        },
    )
    assert summary(snap, "m:outer") == {}


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


def test_a_match_guard_sees_the_names_its_pattern_binds(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def f(): ...

                class A:
                    match value:
                        case f if f():
                            pass

                def use(value):
                    match value:
                        case f if f():
                            pass
            """
        },
    )
    assert summary(snap, "m:A") == {}
    assert summary(snap, "m:use") == {}
