"""Absolute paths inside event strings: recorded logs go public, so none may appear."""

import re
from typing import cast

# What counts as an absolute path, found anywhere inside a string:
#  - a "/" that starts a word: not glued to a word, ".", "/", ")", "]", "}", "<" or "*" before it
#    (so "a/b", "./a", "../a", "</div>", "*/" and "(x)/y" are not), and followed by a name
#    character (so "a / b" is not). A URL's "//" is followed by "/", and its path follows a host.
#  - "~/", for the home directory, and "file:///", for a path written as a URL.
#  - a Windows drive: a letter, ":" and a separator, not glued to a word ("https://" is not).
#  - a UNC path: two backslashes, a host name and a backslash separator, or the device
#    prefixes "\\?\" and "\\.\"; also their source-escaped forms with every backslash doubled.
#    Neither form may follow a backslash. Escapes such as "\\n" have no host separator.
#  - a double-slash POSIX path, except a URL's scheme separator.
_ABSOLUTE = re.compile(
    r"(?<![\w.~/\\\)\]}<*])/(?=[\w.~@$%+-])"
    r"|(?<![\w.:/\\])(?:/{2,}(?=[\w.~@$%+-])|\\{2}(?:[\w.~@$%+-]+\\|[?.]\\)"
    r"|\\{4}(?:[\w.~@$%+-]+\\{2}|[?.]\\{2}))"
    r"|(?<![\w.~/\\])~/"
    r"|\bfile:///"
    r"|(?<!\w)[A-Za-z]:[\\/]"
)


def absolute_paths(value: object) -> list[str]:
    """Every string in a decoded event, keys included, that is or holds an absolute path."""
    if isinstance(value, str):
        return [value[:80]] if _ABSOLUTE.search(value) else []
    if isinstance(value, dict):
        return [
            found
            for key, item in cast(dict[object, object], value).items()
            for found in absolute_paths(key) + absolute_paths(item)
        ]
    if isinstance(value, list):
        return [found for item in cast(list[object], value) for found in absolute_paths(item)]
    return []
