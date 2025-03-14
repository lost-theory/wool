#!/usr/bin/env python

import grp
import os
import pwd
import random
import stat
import string
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from wool.wool import (
    Resource,
    SimpleResource,
    Directory,
    File,
    User,
    Group,
    Download,
    AptPackage,
    Virtualenv,
    Command,
    Owner,
    Perms,
    shell,
    shell_output,
    checksum,
    checksum_bytes,
    file_needs_update,
)
from wool.wool import Path as WoolPath

TEST_FILE_SRC = "Hello world from src!\n"
TEST_FILE_CONTENTS = "Hello world from contents!\n"


def uniq():
    return "".join(random.sample(string.ascii_lowercase, 14))


class TestWoolUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmpdir.name)
        cls.path1 = cls.root / "file1.txt"
        cls.path2 = cls.root / "file2.txt"
        cls.path3 = cls.root / "file3.txt"

        cls.path1.write_text(TEST_FILE_SRC)
        cls.path2.write_text(TEST_FILE_SRC)
        cls.path3.write_text("")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_shell_success(self):
        shell(["stat", self.path1])

    def test_shell_failure(self):
        with self.assertRaises(subprocess.CalledProcessError):
            shell(["stat", self.root / "this-doesnt-exist"])

    def test_shell_output_success(self):
        (status, out, err) = shell_output(["stat", self.path1])
        assert status == 0
        assert "File:" in out
        assert err == ""

    def test_shell_output_failure(self):
        (status, out, err) = shell_output(["stat", self.root / "this-doesnt-exist"])
        assert status != 0
        assert "File:" not in out
        assert "cannot stat" in err

    def test_checksum_path_vs_bytes(self):
        assert checksum(self.path1) == checksum_bytes(TEST_FILE_SRC.encode())

    def test_file_needs_update(self):
        assert file_needs_update(self.path1, self.root / "this-doesnt-exist") == True
        assert file_needs_update(self.path1, self.path2) == False
        assert file_needs_update(self.path1, self.path3) == True


class TestWoolResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmpdir.name)
        cls.timestamp = datetime.now().strftime("%Y%m%d")

        cls.existing_dir_for_destroy = cls.root / "foo"
        os.mkdir(cls.existing_dir_for_destroy)

        cls.existing_file_for_src = cls.root / "baz.txt"
        with open(cls.existing_file_for_src, "w") as f:
            f.write(TEST_FILE_SRC)

        cls.existing_file_for_destroy = cls.root / "bar.txt"
        with open(cls.existing_file_for_destroy, "w") as f:
            f.write("This will be deleted very soon... It's not read anywhere.")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_metaclass_exists_required_by_init(self):
        with self.assertRaises(TypeError) as context:

            class BadResource(Resource):
                def __init__(self):
                    self.foo = 1

        assert "must be defined with 'exists' kwarg" in str(context.exception)

    def test_metaclass_for_resource(self):
        class GoodResource(Resource):
            def __init__(self, exists=True):
                self.exists = exists
                self.state = "nothing"

            def create(self):
                self.state = "created"

            def destroy(self):
                self.state = "destroyed"

        g = GoodResource()
        g.apply()
        assert g.state == "created"

    def test_metaclass_for_simple_resource(self):
        class GoodSimpleResource(SimpleResource):
            def __init__(self):
                self.state = "nothing"

            def apply(self):
                self.state = "applied"

        g = GoodSimpleResource()
        g.apply()
        assert g.state == "applied"

    def test_dir_create_and_destroy(self):
        dirname = self.root / "bah"
        d = Directory(dirname)
        assert not d.path.is_dir()
        d.apply()
        assert d.path.is_dir()
        d = Directory(dirname, exists=False)
        d.apply()
        assert not d.path.is_dir()

    def test_existing_dir_destroy(self):
        d = Directory(self.existing_dir_for_destroy, exists=False)
        assert d.path.is_dir()
        d.apply()
        assert not d.path.is_dir()

    def test_file_contents_create_destroy(self):
        destpath = self.root / "haha.txt"
        f = File(destpath, contents=TEST_FILE_CONTENTS)
        assert not f.path.is_file()
        f.apply()
        assert f.path.is_file()
        with open(f.path) as test_file:
            contents_on_disk = test_file.read()
        assert contents_on_disk == TEST_FILE_CONTENTS
        f = File(destpath, exists=False)
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()

    def test_file_src_create_destroy(self):
        destpath = self.root / "qux.txt"
        f = File(destpath, src=self.existing_file_for_src)
        assert not f.path.is_file()
        f.apply()
        assert f.path.is_file()
        with open(f.path) as test_file:
            contents_on_disk = test_file.read()
        assert contents_on_disk == TEST_FILE_SRC
        f = File(destpath, exists=False)
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()

    def test_existing_file_destroy(self):
        f = File(self.existing_file_for_destroy, exists=False)
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()

    def test_user_create_destroy(self):
        name = f"test-{self.timestamp}-{uniq()}"
        u = User(
            name,
            system=True,
            shell="/sbin/nologin",
            home=self.root / "home",
            group="floppy",
            groups=["cdrom"],
        )
        assert not u.is_present()
        u.apply()
        assert u.is_present()
        u = User(name, exists=False)
        assert u.is_present()
        u.apply()
        assert not u.is_present()

    def test_group_create_destroy(self):
        name = f"tgrp-{self.timestamp}-{uniq()}"
        g = Group(name, system=True)
        assert not g.is_present()
        g.apply()
        assert g.is_present()
        g = Group(name, exists=False)
        assert g.is_present()
        g.apply()
        assert not g.is_present()

    @patch("wool.wool.apt_pkg_is_installed")
    @patch("wool.wool.apt_pkg_install")
    @patch("wool.wool.apt_pkg_remove")
    def test_apt_pkg_create(self, f_remove, f_install, f_installed):
        f_installed.return_value = False
        p = AptPackage("cool-pkg")
        assert not p.is_installed()
        assert f_installed.called
        p.apply()
        assert f_install.called and f_install.call_count == 1
        assert f_install.call_args.args[0] == "cool-pkg"
        assert not f_remove.called

    @patch("wool.wool.apt_pkg_is_installed")
    @patch("wool.wool.apt_pkg_install")
    @patch("wool.wool.apt_pkg_remove")
    def test_apt_pkg_destroy(self, f_remove, f_install, f_installed):
        f_installed.return_value = True
        p = AptPackage("boo-pkg", exists=False)
        assert p.is_installed()
        assert f_installed.called
        p.apply()
        assert f_remove.called and f_remove.call_count == 1
        assert f_remove.call_args.args[0] == "boo-pkg"
        assert not f_install.called

    def test_download(self):
        dest = self.root / "robots.txt"
        d = Download("http://lost-theory.org/robots.txt", dest)
        assert not dest.is_file()
        d.apply()
        assert dest.is_file()

    def test_virtualenv(self):
        dest = self.root / "testing-env"
        v = Virtualenv(sys.executable, dest)
        assert not dest.is_dir()
        v.apply()
        assert dest.is_dir()
        assert v.pip_path.is_file()

    def test_command(self):
        dest = self.root / "test-command-output.txt"
        contents = "hey whats up"
        c = Command("/bin/bash", "-c", f'echo "{contents}" | tee -a {dest}')
        assert not dest.is_file()
        c.apply()
        assert dest.is_file()
        with open(dest) as f:
            contents_on_disk = f.read()
        assert contents + "\n" == contents_on_disk

    def test_ownership(self):
        # Create a test file
        test_file = self.root / "ownership-test.txt"
        test_file.write_text("Test file for ownership")

        current_user = pwd.getpwuid(os.getuid()).pw_name
        current_group = grp.getgrgid(os.getgid()).gr_name

        # Test with both user and group
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(test_file, user=current_user, group=current_group)
            o.apply()
            assert not mock_shell.called, "ownership is already correct, shell call should have been skipped"

        # Test with different user/group
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(test_file, user="root", group="root")
            o.apply()
            mock_shell.assert_called_once()
            assert mock_shell.call_args.args[0] == ["sudo", "chown", "0:0", test_file]

        # Test with only user
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(test_file, user="root")
            o.apply()
            mock_shell.assert_called_once()

        # Test with only group
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(test_file, group="root")
            o.apply()
            mock_shell.assert_called_once()

        # Verify exception raised when file doesn't exist
        with patch("wool.wool.shell") as mock_shell:
            with self.assertRaises(RuntimeError):
                o = Owner(self.root / "nonexistent-file.txt", user=current_user)
                o.apply()

        # Test with invalid arguments
        with self.assertRaises(ValueError):
            Owner(test_file).apply()
        with self.assertRaises(ValueError):
            Owner(test_file, user="baduser12345").apply()
        with self.assertRaises(ValueError):
            Owner(test_file, group="badgroup12345").apply()

    def test_permissions(self):
        test_file = self.root / "permissions-test.txt"
        test_file.write_text("Test file for permissions.")

        # set initial perms
        initial_mode = 0o644
        test_file.chmod(initial_mode)

        # no-op for same perms
        with patch.object(WoolPath, "chmod", new=MagicMock()) as mock_chmod:
            p = Perms(test_file, initial_mode)
            p.apply()
            assert not mock_chmod.called
            assert stat.S_IMODE(test_file.stat().st_mode) == initial_mode

        # change perms
        new_mode = 0o600
        p = Perms(test_file, new_mode)
        p.apply()
        assert stat.S_IMODE(test_file.stat().st_mode) == new_mode

        # verify exception raised when file doesn't exist
        with self.assertRaises(RuntimeError):
            p = Perms(self.root / "nonexistent-file", 0o644)
            p.apply()
