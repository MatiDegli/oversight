"""Descriptor snapshots detect leaks without depending on unrelated closes."""

import os
import re
import sys
from collections import Counter

import pytest

from oversight.workspace.isolation import open_pidfd

from .preview_connector_support import assert_no_new_descriptors, fd_table, new_descriptors

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/self/fd")


def test_pipe_leak_is_reported_until_both_ends_close() -> None:
    before = fd_table()
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(AssertionError, match="pipe:"):
            assert_no_new_descriptors(before)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert_no_new_descriptors(before)


def test_closing_a_preexisting_pipe_is_not_a_leak() -> None:
    read_fd, write_fd = os.pipe()
    try:
        before = fd_table()
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert_no_new_descriptors(before)


def test_pidfd_dup_leak_is_counted_by_target() -> None:
    pidfd = open_pidfd(os.getpid())
    try:
        before = fd_table()
        duplicate = os.dup(pidfd)
        try:
            assert new_descriptors(before) == Counter({"anon_inode:[pidfd]": 1})
            with pytest.raises(AssertionError, match=re.escape("anon_inode:[pidfd]")):
                assert_no_new_descriptors(before)
        finally:
            os.close(duplicate)
    finally:
        os.close(pidfd)
    assert_no_new_descriptors(before)


def test_an_unrelated_close_cannot_cancel_a_pipe_leak() -> None:
    with open(os.devnull) as existing:
        before = fd_table()
        read_fd, write_fd = os.pipe()
        try:
            target = os.readlink(f"/proc/self/fd/{read_fd}")
            os.close(write_fd)
            existing.close()
            assert new_descriptors(before) == Counter({target: 1})
            with pytest.raises(AssertionError, match=re.escape(target)):
                assert_no_new_descriptors(before)
        finally:
            os.close(read_fd)
    assert_no_new_descriptors(before)
