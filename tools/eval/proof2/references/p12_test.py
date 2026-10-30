"""Pure path transformations with independently specified components."""

from importlib import import_module

import pytest

augpath = import_module("boltons.pathutils").augpath


@pytest.mark.parametrize(
    "path,options,expected",
    [
        ("cfg/.profile", {"suffix": "_old"}, "cfg/.profile_old"),
        ("pkg/data.tar.gz", {"suffix": "_v2"}, "pkg/data.tar_v2.gz"),
        ("pkg/data.tar.gz", {"ext": ".zip"}, "pkg/data.tar.zip"),
        ("pkg/data.tar.gz", {"suffix": "_v2", "multidot": True}, "pkg/data_v2.tar.gz"),
        ("pkg/item.txt", {"dpath": ""}, "item.txt"),
        ("pkg/item.txt", {"base": "", "ext": "", "prefix": "x"}, "pkg/x"),
        ("", {"prefix": "new"}, "new"),
    ],
)
def test_augpath_components(path, options, expected):
    assert augpath(path, **options) == expected
