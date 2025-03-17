"""
Test suite for wool.
"""

import grp
import os
import pwd
import random
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
    Symlink,
    SymbolicPermissions,
    shell,
    shell_output,
    checksum,
    checksum_bytes,
    file_needs_update,
)
from wool.wool import Path as WoolPath

TEST_FILE_SRC = "Hello world from src!\n"
TEST_FILE_CONTENTS = "Hello world from contents!\n"


def uniq() -> str:
    return "".join(random.sample(string.ascii_lowercase, 14))


class WoolFileSystemTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        cls.root = Path(cls.tmpdir.name)
        cls.timestamp = datetime.now().strftime("%Y%m%d")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()


class TestWoolMetaclasses(unittest.TestCase):
    def test_metaclass_ensures_required_by_init(self):
        with self.assertRaises(TypeError) as context:

            class BadResource(Resource):
                def __init__(self) -> None:
                    self.name = uniq()
                    self.state = "init"

                def create(self) -> None:
                    self.state = "created"

                def destroy(self) -> None:
                    self.state = "destroyed"

            b = BadResource()  # pylint: disable=unused-variable

        assert "must be defined with 'ensures' kwarg" in str(context.exception)

    def test_metaclass_for_resource(self):
        class GoodResource(Resource):
            def __init__(self, ensures: str = "present") -> None:
                self.ensures = ensures
                self.state = "init"

            def create(self) -> None:
                self.state = "created"

            def destroy(self) -> None:
                self.state = "destroyed"

        g = GoodResource()
        g.apply()
        assert g.state == "created"

        g = GoodResource(ensures="absent")
        g.apply()
        assert g.state == "destroyed"

    def test_metaclass_for_simple_resource(self):
        class GoodSimpleResource(SimpleResource):
            def __init__(self) -> None:
                self.state = "init"

            def apply(self) -> None:
                self.state = "applied"

        g = GoodSimpleResource()
        assert g.state == "init"
        g.apply()
        assert g.state == "applied"

    def test_subclass_error_missing_ensures(self):
        class ResourceMissingEnsures(Resource):
            def __init__(self, ensures: str = "present"):
                self.enshoors = ensures  # typo...
                self.state = "init"

            def create(self) -> None:
                self.state = "created"

            def destroy(self) -> None:
                self.state = "destroyed"

        with self.assertRaises(AttributeError) as context:
            r = ResourceMissingEnsures()
            r.apply()
        assert "subclass must have 'ensures' attribute" in str(context.exception)

    def test_subclass_missing_create_destroy(self):
        class ResourceMissingCreateDestroy(Resource):  # pylint: disable=abstract-method
            def __init__(self, ensures: str = "present"):
                self.ensures = ensures
                self.state = "init"

        with self.assertRaises(NotImplementedError):
            r = ResourceMissingCreateDestroy(ensures="present")
            r.apply()
        with self.assertRaises(NotImplementedError):
            r = ResourceMissingCreateDestroy(ensures="absent")
            r.apply()

    def test_subclass_bad_ensures_value(self):
        class ResourceBadEnsuresValue(Resource):
            def __init__(self, ensures: str = "based"):
                self.ensures = ensures
                self.state = "init"

            def create(self) -> None:
                self.state = "created"

            def destroy(self) -> None:
                self.state = "destroyed"

        with self.assertRaises(ValueError) as context:
            r = ResourceBadEnsuresValue()
            r.apply()
        assert "Unsupported value" in str(context.exception)

    def test_simple_resource_subclass_errors(self):
        class SimpleResourceMissingApply(SimpleResource):  # pylint: disable=abstract-method
            def __init__(self) -> None:
                self.state = "init"

        with self.assertRaises(NotImplementedError):
            r = SimpleResourceMissingApply()
            r.apply()
        with self.assertRaises(RuntimeError):
            r = SimpleResourceMissingApply()
            r.create()
        with self.assertRaises(RuntimeError):
            r = SimpleResourceMissingApply()
            r.destroy()


class TestWoolUtils(WoolFileSystemTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path1 = cls.root / "file1.txt"
        cls.path2 = cls.root / "file2.txt"
        cls.path3 = cls.root / "file3.txt"

        cls.path1.write_text(TEST_FILE_SRC)
        cls.path2.write_text(TEST_FILE_SRC)
        cls.path3.write_text("")

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
        assert file_needs_update(self.path1, self.root / "this-doesnt-exist")
        assert not file_needs_update(self.path1, self.path2)
        assert file_needs_update(self.path1, self.path3)


class TestWoolDirectory(WoolFileSystemTestCase):
    def test_dir_create_and_destroy(self):
        dirname = self.root / "bah"
        d = Directory(dirname)
        assert not d.path.is_dir()
        d.apply()
        assert d.path.is_dir()
        d = Directory(dirname, ensures="absent")
        d.apply()
        assert not d.path.is_dir()

    def test_dir_destroy_existing(self):
        dir_for_destroying = self.root / "foo"
        dir_for_destroying.mkdir()
        d = Directory(dir_for_destroying, ensures="absent")
        assert d.path.is_dir()
        d.apply()
        assert not d.path.is_dir()

    def test_dir_skip_destroy_when_not_exists(self):
        dir_that_does_not_exist = self.root / "the-dir-that-never-was"
        d = Directory(dir_that_does_not_exist, ensures="absent")
        with patch("wool.wool.shell") as mock_shell:
            d.apply()
            assert not mock_shell.called

    def test_dir_skip_create_when_exists(self):
        dir_that_already_exists = self.root / "the-dir-that-already-exists"
        dir_that_already_exists.mkdir()
        d = Directory(dir_that_already_exists, ensures="present")
        with patch("wool.wool.shell") as mock_shell:
            d.apply()
            assert not mock_shell.called


class TestWoolFile(WoolFileSystemTestCase):
    def test_file_contents_create_destroy(self):
        destpath = self.root / "haha.txt"
        f = File(destpath, contents=TEST_FILE_CONTENTS)
        assert not f.path.is_file()
        f.apply()
        assert f.path.is_file()
        contents_on_disk = f.path.read_text("utf8")
        assert contents_on_disk == TEST_FILE_CONTENTS
        f = File(destpath, ensures="absent")
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()

    def test_file_src_create_destroy(self):
        existing_file_for_src = self.root / "baz.txt"
        existing_file_for_src.write_text(TEST_FILE_SRC)
        destpath = self.root / "qux.txt"
        f = File(destpath, src=existing_file_for_src)
        assert not f.path.is_file()
        f.apply()
        assert f.path.is_file()
        contents_on_disk = f.path.read_text("utf8")
        assert contents_on_disk == TEST_FILE_SRC
        f = File(destpath, ensures="absent")
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()

    def test_existing_file_destroy(self):
        existing_file_for_destroy = self.root / "bar.txt"
        existing_file_for_destroy.write_text("This will be deleted very soon... It's not read anywhere.")
        f = File(existing_file_for_destroy, ensures="absent")
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()


class TestWoolResources(WoolFileSystemTestCase):

    def test_user_create_destroy(self):
        name = f"test-{self.timestamp}-{uniq()}"
        u = User(
            name,
            system=True,
            shell_bin="/sbin/nologin",
            home=self.root / "home",
            group="floppy",
            groups=["cdrom"],
        )
        assert not u.exists()
        u.apply()
        assert u.exists()
        u = User(name, ensures="absent")
        assert u.exists()
        u.apply()
        assert not u.exists()

    def test_group_create_destroy(self):
        name = f"tgrp-{self.timestamp}-{uniq()}"
        g = Group(name, system=True)
        assert not g.exists()
        g.apply()
        assert g.exists()
        g = Group(name, ensures="absent")
        assert g.exists()
        g.apply()
        assert not g.exists()

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
        p = AptPackage("boo-pkg", ensures="absent")
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
        c = Command(["/bin/bash", "-c", f'echo "{contents}" | tee -a {dest}'])
        assert not dest.is_file()
        c.apply()
        assert dest.is_file()
        contents_on_disk = dest.read_text("utf8")
        assert contents + "\n" == contents_on_disk

    def test_command_skip_when_provides_exists(self):
        dest = self.root / "test-command-output-skip.txt"
        contents = "This file exists already."
        dest.write_text(contents)
        with patch("wool.wool.shell") as mock_shell:
            c = Command(["/bin/bash", "-c", f'echo "hey whats up" | tee -a {dest}'], provides=dest)
            assert dest.is_file()
            c.apply()
            assert not mock_shell.called, "shell call should have been skipped because provides file already exists"
            assert dest.read_text("utf8") == contents


class TestWoolOwner(WoolFileSystemTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.current_user = pwd.getpwuid(os.getuid()).pw_name
        cls.current_group = grp.getgrgid(os.getgid()).gr_name
        cls.test_dir = cls.root / "test-ownership-dir"
        cls.test_dir.mkdir()
        cls.test_file = cls.root / "test-ownership-file.txt"
        cls.test_file.write_text("This file is for testing ownership changes.")

    def test_no_change_when_ownership_correct(self):
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user=self.current_user, group=self.current_group)
            o.apply()
            assert not mock_shell.called, "ownership is already correct, shell call should have been skipped"

    def test_change_user_and_group(self):
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user="root", group="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]
            assert "0:0" in mock_shell.call_args.args[0]

    def test_change_user_only(self):
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]

    def test_change_group_only(self):
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, group="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]

    def test_recursive_ownership(self):
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_dir, user="root", group="root", recursive=True)
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]
            assert "-R" in mock_shell.call_args.args[0]

    def test_nonexistent_file(self):
        with self.assertRaises(RuntimeError):
            o = Owner(self.root / "nonexistent-file.txt", user=self.current_user)
            o.apply()

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            Owner(self.test_file).apply()
        with self.assertRaises(KeyError):
            Owner(self.test_file, user="baduser12345").apply()
        with self.assertRaises(KeyError):
            Owner(self.test_file, group="badgroup12345").apply()


class TestWoolPerms(WoolFileSystemTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_dir = cls.root / "test-perms-dir"
        cls.test_subdir_file = cls.test_dir / "file1.txt"

        # create files/dirs on disk
        cls.test_dir.mkdir()
        cls.test_subdir_file.write_text("Test file 1")

        # perms
        cls.test_dir.chmod(0o744)
        cls.test_subdir_file.chmod(0o644)

    def test_symbolic_perms(self):
        s = SymbolicPermissions(0o644)
        expected_str = "u=rw-, g=r--, o=r--, u-s, g-s, o-t"
        assert s == expected_str and expected_str == s
        assert s == SymbolicPermissions(0o644) and SymbolicPermissions(0o644) == s
        assert s != SymbolicPermissions(0o755) and SymbolicPermissions(0o755) != s

    def test_no_change_when_perms_match(self):
        test_file_for_no_change = self.root / "perms-test-no-change.txt"
        test_file_for_no_change.write_text("Test file for perms (no change).")
        test_file_for_no_change.chmod(0o644)
        with patch.object(WoolPath, "chmod", new=MagicMock()) as mock_chmod:
            p = Perms(test_file_for_no_change, 0o644)
            p.apply()
            assert not mock_chmod.called
            assert p.get_mode() == 0o644

    def test_change_perms_with_int_mode(self):
        test_file = self.root / "perms-int-mode.txt"
        test_file.write_text("Test file for changing perms with int value for mode.")
        test_file.chmod(0o600)
        p = Perms(test_file, 0o644)
        p.apply()
        assert p.get_mode() == 0o644

    def test_change_perms_with_str_mode(self):
        test_file = self.root / "perms-str-mode.txt"
        test_file.write_text("Test file for changing perms with string value for mode.")
        test_file.chmod(0o600)
        p = Perms(test_file, "644")
        p.apply()
        assert p.get_mode() == 0o644

    def test_recursive_perms_mocked(self):
        with patch("wool.wool.shell") as mock_shell:
            p = Perms(self.test_dir, 0o755, recursive=True)
            p.apply()
            mock_shell.assert_called_once()
            assert "chmod" in mock_shell.call_args.args[0]
            assert "-R" in mock_shell.call_args.args[0]
            assert "755" in mock_shell.call_args.args[0]

    def test_recursive_perms_real(self):
        modes = [0o744, 0o777, 0o744]
        for mode in modes:
            p = Perms(self.test_dir, mode, recursive=True)
            p.apply()
            assert p.get_mode() == mode

    def test_nonexistent_file(self):
        with self.assertRaises(RuntimeError):
            p = Perms(self.root / "nonexistent-file.txt", 0o644)
            p.apply()

    def test_mode_attrs(self):
        test_file = self.root / "perms-mode-attrs-tests.txt"
        test_file.write_text("Test file for reading perms mode attrs.")
        test_file.chmod(0o640)

        p = Perms(test_file, 0o777)
        assert p.get_full_mode() == 0o100640
        assert p.get_full_mode_str() == "100640"
        assert p.get_mode() == 0o640
        assert p.get_mode_str() == "640"
        sym = p.get_symbolic()
        assert sym == "u=rw-, g=r--, o=---, u-s, g-s, o-t"
        assert [sym.ur, sym.uw, sym.ux] == [True, True, False]
        assert [sym.gr, sym.gw, sym.gx] == [True, False, False]
        assert [sym.othr, sym.othw, sym.othx] == [False, False, False]
        assert [sym.setuid, sym.setgid, sym.sticky] == [False, False, False]
        p.apply()
        assert p.get_full_mode() == 0o100777
        assert p.get_full_mode_str() == "100777"
        assert p.get_mode() == 0o777
        assert p.get_mode_str() == "777"
        sym = p.get_symbolic()
        assert sym == "u=rwx, g=rwx, o=rwx, u-s, g-s, o-t"
        assert [sym.ur, sym.uw, sym.ux] == [True, True, True]
        assert [sym.gr, sym.gw, sym.gx] == [True, True, True]
        assert [sym.othr, sym.othw, sym.othx] == [True, True, True]
        assert [sym.setuid, sym.setgid, sym.sticky] == [False, False, False]


class TestWoolSymlink(WoolFileSystemTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src_file = cls.root / "symlink-src.txt"
        cls.src_file.write_text("Target file for symlink tests.")
        cls.src_wrong = cls.root / "symlink-src-wrong.txt"
        cls.src_wrong.write_text("This is the wrong target.")
        cls.existing_file = cls.root / "existing-file.txt"
        cls.existing_file.write_text("This is an existing file")
        cls.src_nonexistent = cls.root / "this-src-file-does-not-exist.txt"
        cls.link_path = cls.root / "symlink-test.txt"
        cls.link_path_for_change_target = cls.root / "symlink-that-will-have-its-target-changed.txt"
        cls.nonexistent_link_path = cls.root / "nonexistent-symlink.txt"

    def test_create_symlink(self):
        s = Symlink(self.link_path, self.src_file)
        s.apply()

        assert self.link_path.is_symlink()
        assert self.link_path.readlink() == self.src_file
        assert self.link_path.read_bytes() == self.src_file.read_bytes()

    def test_no_change_when_symlink_already_exists(self):
        with patch("builtins.print") as mock_print:
            s = Symlink(self.link_path, self.src_file)
            s.apply()
            assert "Skipping symlink creation" in repr(mock_print.call_args_list)

    def test_change_symlink_target(self):
        os.symlink(src=self.src_wrong, dst=self.link_path_for_change_target)
        assert self.link_path_for_change_target.is_symlink()
        assert self.link_path_for_change_target.readlink() == self.src_wrong
        s = Symlink(self.link_path_for_change_target, self.src_file)
        s.apply()
        assert self.link_path_for_change_target.is_symlink()
        assert self.link_path_for_change_target.readlink() == self.src_file

    def test_error_when_file_exists_at_symlink_path(self):
        s = Symlink(self.existing_file, self.src_file)
        with self.assertRaises(RuntimeError) as context:
            s.apply()
        assert "already exists" in str(context.exception)

    def test_create_symlink_for_nonexistent_target(self):
        s = Symlink(self.nonexistent_link_path, self.src_nonexistent)
        s.apply()
        assert self.nonexistent_link_path.is_symlink()
        assert self.nonexistent_link_path.readlink() == self.src_nonexistent

    def test_destroy_symlink(self):
        destroy_link_path = self.root / "symlink-to-destroy.txt"
        os.symlink(src=self.src_file, dst=destroy_link_path)
        assert destroy_link_path.is_symlink()

        s = Symlink(destroy_link_path, self.src_file, ensures="absent")
        s.apply()
        assert not destroy_link_path.exists()

    def test_no_change_when_destroying_nonexistent_symlink(self):
        destroy_link_path = self.root / "this-path-does-not-exist.txt"
        with patch("builtins.print") as mock_print:
            s = Symlink(destroy_link_path, self.src_file, ensures="absent")
            s.apply()
            assert "Skipping removal of symlink" in repr(mock_print.call_args_list)
