"""Launcher contracts used by shell-capable agent ranks."""

from __future__ import annotations

import os

from ampi import launch


def test_default_bindir_finds_user_install_when_not_on_path(tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    user_base = tmp_path / "user"
    user_bin = user_base / "bin"
    user_bin.mkdir(parents=True)
    executable = user_bin / "ampi"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(launch.shutil, "which", lambda _: None)
    monkeypatch.setattr(launch.sys, "prefix", str(prefix))
    monkeypatch.setattr(launch.site, "USER_BASE", str(user_base))

    assert launch.default_bindir() == os.fspath(user_bin)
