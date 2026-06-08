"""
Test suite for wool.
"""

# pylint: disable=unused-variable

import grp
import os
import pwd
import random
import string
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from wool.wool import (
    AptPackage,
    BlockInFile,
    Command,
    Directory,
    Download,
    File,
    Group,
    Hostkey,
    Owner,
    Perms,
    Resource,
    SimpleResource,
    SymbolicPermissions,
    Symlink,
    Touch,
    User,
    Virtualenv,
    checksum,
    checksum_bytes,
    file_needs_update,
    shell,
    shell_output,
)

TEST_FILE_SRC = "Hello world from src!\n"
TEST_FILE_CONTENTS = "Hello world from contents!\n"


def uniq() -> str:
    return "".join(random.sample(string.ascii_lowercase, 14))


class WoolFileSystemTestCase(unittest.TestCase):
    if TYPE_CHECKING:
        tmpdir: Any
        root: Path
        timestamp: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        cls.root = Path(cls.tmpdir.name)
        cls.timestamp = datetime.now().strftime("%Y%m%d")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()


class TestWoolMetaclasses(unittest.TestCase):
    def test_metaclass_ensures_required_by_init(self) -> None:
        with self.assertRaises(TypeError) as context:

            class BadResource(Resource):
                def __init__(self) -> None:
                    self.name = uniq()
                    self.state = "init"

                def create(self) -> None:
                    self.state = "created"

                def destroy(self) -> None:
                    self.state = "destroyed"

            b = BadResource()
            b.apply()

        assert "must be defined with 'ensures' kwarg" in str(context.exception)

    def test_metaclass_for_resource(self) -> None:
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

    def test_metaclass_for_simple_resource(self) -> None:
        class GoodSimpleResource(SimpleResource):
            def __init__(self) -> None:
                self.state = "init"

            def apply(self) -> None:
                self.state = "applied"

        g = GoodSimpleResource()
        assert g.state == "init"
        g.apply()
        assert g.state == "applied"

    def test_subclass_error_missing_ensures(self) -> None:
        class ResourceMissingEnsures(Resource):
            def __init__(self, ensures: str = "present") -> None:
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

    def test_subclass_missing_create_destroy(self) -> None:
        class ResourceMissingCreateDestroy(Resource):  # pylint: disable=abstract-method
            def __init__(self, ensures: str = "present") -> None:
                self.ensures = ensures
                self.state = "init"

        with self.assertRaises(NotImplementedError):
            r = ResourceMissingCreateDestroy(ensures="present")
            r.apply()
        with self.assertRaises(NotImplementedError):
            r = ResourceMissingCreateDestroy(ensures="absent")
            r.apply()

    def test_subclass_bad_ensures_value(self) -> None:
        class ResourceBadEnsuresValue(Resource):
            def __init__(self, ensures: str = "based") -> None:
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

    def test_simple_resource_subclass_errors(self) -> None:
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
    if TYPE_CHECKING:
        path1: Path
        path2: Path
        path3: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path1 = cls.root / "file1.txt"
        cls.path2 = cls.root / "file2.txt"
        cls.path3 = cls.root / "file3.txt"

        cls.path1.write_text(TEST_FILE_SRC)
        cls.path2.write_text(TEST_FILE_SRC)
        cls.path3.write_text("")

    def test_shell_success(self) -> None:
        shell(["stat", self.path1])

    def test_shell_failure(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            shell(["stat", self.root / "this-doesnt-exist"])

    def test_shell_output_success(self) -> None:
        status, out, err = shell_output(["stat", self.path1])
        assert status == 0
        assert "File:" in out
        assert err == ""

    def test_shell_output_failure(self) -> None:
        status, out, err = shell_output(["stat", self.root / "this-doesnt-exist"])
        assert status != 0
        assert "File:" not in out
        assert "cannot stat" in err

    def test_checksum_path_vs_bytes(self) -> None:
        assert checksum(self.path1) == checksum_bytes(TEST_FILE_SRC.encode())

    def test_file_needs_update(self) -> None:
        assert file_needs_update(self.path1, self.root / "this-doesnt-exist")
        assert not file_needs_update(self.path1, self.path2)
        assert file_needs_update(self.path1, self.path3)


class TestWoolDirectory(WoolFileSystemTestCase):
    def test_dir_create_and_destroy(self) -> None:
        dirname = self.root / "bah"
        d = Directory(dirname)
        assert not d.path.is_dir()
        d.apply()
        assert d.path.is_dir()
        d = Directory(dirname, ensures="absent")
        d.apply()
        assert not d.path.is_dir()

    def test_dir_destroy_existing(self) -> None:
        dir_for_destroying = self.root / "foo"
        dir_for_destroying.mkdir()
        d = Directory(dir_for_destroying, ensures="absent")
        assert d.path.is_dir()
        d.apply()
        assert not d.path.is_dir()

    def test_dir_skip_destroy_when_not_exists(self) -> None:
        dir_that_does_not_exist = self.root / "the-dir-that-never-was"
        d = Directory(dir_that_does_not_exist, ensures="absent")
        with patch("wool.wool.shell") as mock_shell:
            d.apply()
            assert not mock_shell.called

    def test_dir_skip_create_when_exists(self) -> None:
        dir_that_already_exists = self.root / "the-dir-that-already-exists"
        dir_that_already_exists.mkdir()
        d = Directory(dir_that_already_exists, ensures="present")
        with patch("wool.wool.shell") as mock_shell:
            d.apply()
            assert not mock_shell.called


class TestWoolFile(WoolFileSystemTestCase):
    def test_file_contents_create_destroy(self) -> None:
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

    def test_file_src_create_destroy(self) -> None:
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

    def test_existing_file_destroy(self) -> None:
        existing_file_for_destroy = self.root / "bar.txt"
        existing_file_for_destroy.write_text("This will be deleted very soon... It's not read anywhere.")
        f = File(existing_file_for_destroy, ensures="absent")
        assert f.path.is_file()
        f.apply()
        assert not f.path.is_file()


class TestWoolResources(WoolFileSystemTestCase):

    def test_user_create_destroy(self) -> None:
        # create
        name = f"test-{self.timestamp}-{uniq()}"
        create_args: Any = dict(system=True, shell_bin="/sbin/nologin", home=self.root / "home", group="floppy", groups=["cdrom"])
        u1 = User(name, **create_args)
        assert not u1.exists()
        u1.apply()
        assert u1.exists()

        # create again with same args to verify creation is skipped
        with self.assertLogs(level="INFO") as logs:
            u2 = User(name, **create_args)
            u2.apply()
        assert "Skipping user creation" in "\n".join(logs.output)
        assert "user already exists" in "\n".join(logs.output)

        # destroy
        u3 = User(name, ensures="absent")
        assert u3.exists()
        u3.apply()
        assert not u3.exists()

        # destroy again to verify destroy is skipped
        with self.assertLogs(level="INFO") as logs:
            u4 = User(name, ensures="absent")
            u4.apply()
        assert "Skipping user deletion" in "\n".join(logs.output)
        assert "user doesn't exist" in "\n".join(logs.output)

    def test_group_create_destroy(self) -> None:
        # create
        name = f"tgrp-{self.timestamp}-{uniq()}"
        g1 = Group(name, system=True)
        assert not g1.exists()
        g1.apply()
        assert g1.exists()

        # create again with same args to verify creation is skipped
        with self.assertLogs(level="INFO") as logs:
            g2 = Group(name, system=True)
            g2.apply()
        assert "Skipping group creation" in "\n".join(logs.output)
        assert "group already exists" in "\n".join(logs.output)

        # destroy
        g3 = Group(name, ensures="absent")
        assert g3.exists()
        g3.apply()
        assert not g3.exists()

        # destroy again to verify destroy is skipped
        with self.assertLogs(level="INFO") as logs:
            g4 = Group(name, ensures="absent")
            g4.apply()
        assert "Skipping group deletion" in "\n".join(logs.output)
        assert "group doesn't exist" in "\n".join(logs.output)

    @patch("wool.wool.apt_pkg_is_installed")
    @patch("wool.wool.apt_pkg_install")
    @patch("wool.wool.apt_pkg_remove")
    def test_apt_pkg_create(self, f_remove: Any, f_install: Any, f_installed: Any) -> None:
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
    def test_apt_pkg_destroy(self, f_remove: Any, f_install: Any, f_installed: Any) -> None:
        f_installed.return_value = True
        p = AptPackage("boo-pkg", ensures="absent")
        assert p.is_installed()
        assert f_installed.called
        p.apply()
        assert f_remove.called and f_remove.call_count == 1
        assert f_remove.call_args.args[0] == "boo-pkg"
        assert not f_install.called

    def test_download(self) -> None:
        dest = self.root / "robots.txt"
        url = "http://lost-theory.org/robots.txt"
        d1 = Download(url, dest)
        assert not dest.is_file()
        d1.apply()
        assert dest.is_file()

        # verify skipping when file already exists
        with self.assertLogs(level="INFO") as logs:
            d2 = Download(url, dest)
            d2.apply()
        assert "Skipping download" in "\n".join(logs.output)
        assert "already exists" in "\n".join(logs.output)

    def test_virtualenv(self) -> None:
        dest = self.root / "testing-env"
        v1 = Virtualenv(sys.executable, dest)
        assert not dest.is_dir()
        v1.apply()
        assert dest.is_dir()
        assert v1.pip_path.is_file()

        # verify skipping when virtualenv already exists
        with self.assertLogs(level="INFO") as logs:
            v2 = Virtualenv(sys.executable, dest)
            v2.apply()
        assert "Skipping venv creation" in "\n".join(logs.output)
        assert "already exists" in "\n".join(logs.output)

    def test_command(self) -> None:
        dest = self.root / "test-command-output.txt"
        contents = "hey whats up"
        c = Command(["/bin/bash", "-c", f'echo "{contents}" | tee -a {dest}'])
        assert not dest.is_file()
        c.apply()
        assert dest.is_file()
        contents_on_disk = dest.read_text("utf8")
        assert contents + "\n" == contents_on_disk

    def test_command_skip_when_provides_exists(self) -> None:
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
    if TYPE_CHECKING:
        current_user: str
        current_group: str
        test_dir: Path
        test_file: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.current_user = pwd.getpwuid(os.getuid()).pw_name
        cls.current_group = grp.getgrgid(os.getgid()).gr_name
        cls.test_dir = cls.root / "test-ownership-dir"
        cls.test_dir.mkdir()
        cls.test_file = cls.root / "test-ownership-file.txt"
        cls.test_file.write_text("This file is for testing ownership changes.")

    def test_no_change_when_ownership_correct(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user=self.current_user, group=self.current_group)
            o.apply()
            assert not mock_shell.called, "ownership is already correct, shell call should have been skipped"

    def test_change_user_and_group(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user="root", group="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]
            assert "0:0" in mock_shell.call_args.args[0]

    def test_change_user_only(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, user="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]

    def test_change_group_only(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_file, group="root")
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]

    def test_recursive_ownership(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            o = Owner(self.test_dir, user="root", group="root", recursive=True)
            o.apply()
            mock_shell.assert_called_once()
            assert "chown" in mock_shell.call_args.args[0]
            assert "-R" in mock_shell.call_args.args[0]

    def test_nonexistent_file(self) -> None:
        with self.assertRaises(RuntimeError):
            o = Owner(self.root / "nonexistent-file.txt", user=self.current_user)
            o.apply()

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            Owner(self.test_file).apply()
        with self.assertRaises(KeyError):
            Owner(self.test_file, user="baduser12345").apply()
        with self.assertRaises(KeyError):
            Owner(self.test_file, group="badgroup12345").apply()


class TestWoolPerms(WoolFileSystemTestCase):
    if TYPE_CHECKING:
        test_dir: Path
        test_subdir_file: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_dir = cls.root / "test-perms-dir"
        cls.test_subdir_file = cls.test_dir / "file1.txt"

        # create files/dirs on disk
        cls.test_dir.mkdir()
        cls.test_subdir_file.write_text("Test file 1")

        # perms
        cls.test_dir.chmod(0o744)
        cls.test_subdir_file.chmod(0o644)

    def test_symbolic_perms(self) -> None:
        s = SymbolicPermissions(0o644)
        expected_str = "u=rw-, g=r--, o=r--, u-s, g-s, o-t"
        assert s == expected_str and expected_str == s
        assert s == SymbolicPermissions(0o644) and SymbolicPermissions(0o644) == s
        assert s != SymbolicPermissions(0o755) and SymbolicPermissions(0o755) != s

    def test_no_change_when_perms_match(self) -> None:
        test_file_for_no_change = self.root / "perms-test-no-change.txt"
        test_file_for_no_change.write_text("Test file for perms (no change).")
        test_file_for_no_change.chmod(0o644)
        with patch.object(Path, "chmod", new=MagicMock()) as mock_chmod:
            p = Perms(test_file_for_no_change, 0o644)
            p.apply()
            assert not mock_chmod.called
            assert p.get_mode() == 0o644

    def test_change_perms_with_int_mode(self) -> None:
        test_file = self.root / "perms-int-mode.txt"
        test_file.write_text("Test file for changing perms with int value for mode.")
        test_file.chmod(0o600)
        p = Perms(test_file, 0o644)
        p.apply()
        assert p.get_mode() == 0o644

    def test_change_perms_with_str_mode(self) -> None:
        test_file = self.root / "perms-str-mode.txt"
        test_file.write_text("Test file for changing perms with string value for mode.")
        test_file.chmod(0o600)
        p = Perms(test_file, "644")
        p.apply()
        assert p.get_mode() == 0o644

    def test_recursive_perms_mocked(self) -> None:
        with patch("wool.wool.shell") as mock_shell:
            p = Perms(self.test_dir, 0o755, recursive=True)
            p.apply()
            mock_shell.assert_called_once()
            assert "chmod" in mock_shell.call_args.args[0]
            assert "-R" in mock_shell.call_args.args[0]
            assert "755" in mock_shell.call_args.args[0]

    def test_recursive_perms_real(self) -> None:
        modes = [0o744, 0o777, 0o744]
        for mode in modes:
            p = Perms(self.test_dir, mode, recursive=True)
            p.apply()
            assert p.get_mode() == mode

    def test_nonexistent_file(self) -> None:
        with self.assertRaises(RuntimeError):
            p = Perms(self.root / "nonexistent-file.txt", 0o644)
            p.apply()

    def test_mode_attrs(self) -> None:
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
    if TYPE_CHECKING:
        src_file: Path
        src_wrong: Path
        existing_file: Path
        src_nonexistent: Path
        link_path: Path
        link_path_for_change_target: Path
        nonexistent_link_path: Path

    @classmethod
    def setUpClass(cls) -> None:
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

    def test_create_symlink(self) -> None:
        s = Symlink(self.link_path, self.src_file)
        s.apply()

        assert self.link_path.is_symlink()
        assert self.link_path.readlink() == self.src_file
        assert self.link_path.read_bytes() == self.src_file.read_bytes()

    def test_no_change_when_symlink_already_exists(self) -> None:
        with patch("wool.Symlink.logger") as mock_logger:
            s = Symlink(self.link_path, self.src_file)
            s.apply()
            assert "Skipping" in mock_logger.info.call_args_list[0].kwargs["action"]
            assert "already points to" in mock_logger.info.call_args_list[0].kwargs["because"]

    def test_change_symlink_target(self) -> None:
        os.symlink(src=self.src_wrong, dst=self.link_path_for_change_target)
        assert self.link_path_for_change_target.is_symlink()
        assert self.link_path_for_change_target.readlink() == self.src_wrong
        s = Symlink(self.link_path_for_change_target, self.src_file)
        s.apply()
        assert self.link_path_for_change_target.is_symlink()
        assert self.link_path_for_change_target.readlink() == self.src_file

    def test_error_when_file_exists_at_symlink_path(self) -> None:
        s = Symlink(self.existing_file, self.src_file)
        with self.assertRaises(RuntimeError) as context:
            s.apply()
        assert "already exists" in str(context.exception)

    def test_create_symlink_for_nonexistent_target(self) -> None:
        s = Symlink(self.nonexistent_link_path, self.src_nonexistent)
        s.apply()
        assert self.nonexistent_link_path.is_symlink()
        assert self.nonexistent_link_path.readlink() == self.src_nonexistent

    def test_destroy_symlink(self) -> None:
        destroy_link_path = self.root / "symlink-to-destroy.txt"
        os.symlink(src=self.src_file, dst=destroy_link_path)
        assert destroy_link_path.is_symlink()

        s = Symlink(destroy_link_path, self.src_file, ensures="absent")
        s.apply()
        assert not destroy_link_path.exists()

    def test_no_change_when_destroying_nonexistent_symlink(self) -> None:
        destroy_link_path = self.root / "this-path-does-not-exist.txt"
        with patch("wool.Symlink.logger") as mock_logger:
            s = Symlink(destroy_link_path, self.src_file, ensures="absent")
            s.apply()
            assert "Skipping" in mock_logger.info.call_args_list[0].kwargs["action"]
            assert "does not exist" in mock_logger.info.call_args_list[0].kwargs["because"]


class TestWoolBlockInFile(WoolFileSystemTestCase):
    start_marker = "# {start}"
    end_marker = "# {end}"
    default_contents = "# This is a file where blocks will go for testing.\n"
    line1 = "This is a managed block line."
    line2 = "This is a modified block line."

    def test_create_block_in_nonexistent_file(self) -> None:
        block_file = self.root / "test1.txt"
        assert not block_file.exists()
        b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b.apply()
        contents = block_file.read_text()
        assert self.default_contents not in contents
        assert b.start in contents
        assert b.end in contents
        assert self.line1 in contents

    def test_create_block_in_existing_file(self) -> None:
        block_file = self.root / "test2.txt"
        block_file.write_text(self.default_contents)
        b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b.apply()
        contents = block_file.read_text()
        assert b.start in contents
        assert b.end in contents
        assert self.line1 in contents
        assert self.default_contents in contents

    def test_no_change_when_block_already_exists(self) -> None:
        block_file = self.root / "test3.txt"
        block_file.write_text(self.default_contents)
        b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b.apply()

        with patch("wool.BlockInFile.logger") as mock_logger:
            b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
            b.apply()
            assert "Skipping" in mock_logger.info.call_args_list[0].kwargs["action"]
            assert "block content already matches" in mock_logger.info.call_args_list[0].kwargs["because"]

    def test_update_block(self) -> None:
        block_file = self.root / "test4.txt"
        block_file.write_text(self.default_contents)
        b1 = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b1.apply()
        b2 = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line2)
        b2.apply()
        contents = block_file.read_text()
        assert self.default_contents in contents
        assert b1.start == b2.start
        assert b1.end == b2.end
        assert b2.start in contents
        assert b2.end in contents
        assert self.line1 not in contents
        assert self.line2 in contents

    def test_new_line_behavior(self) -> None:
        """
        Early on in implementation I was adding a bunch of extra new lines to
        the file when addding/removing blocks. This is a regression test that
        verifies the extra new lines aren't getting added back somehow. Adding
        and removing a block should only add <=1 extra new line to the file.
        """
        block_file = self.root / "test5.txt"
        block_file.write_text(self.default_contents)
        num_blocks = 10
        num_new_lines_per_block = 4  # 2 for start, 1 for contents, 1 for end
        for i in range(num_blocks):
            b = BlockInFile(block_file, name=f"block{i}", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
            b.apply()
        contents_after_adding = block_file.read_text()
        assert self.default_contents in contents_after_adding
        assert contents_after_adding.count("\n") <= num_blocks * num_new_lines_per_block + 1, "Too many new lines added to file while adding blocks..."
        for i in range(num_blocks):
            b = BlockInFile(block_file, name=f"block{i}", start_marker=self.start_marker, end_marker=self.end_marker, ensures="absent")
            b.apply()
        contents_after_removing = block_file.read_text()
        assert contents_after_removing.count("\n") <= 2, "Too many new lines added to file after removing blocks..."

    def test_different_markers(self) -> None:
        block_file = self.root / "test6.css"
        block_file.write_text("/* My CSS file with blocks */")
        css_marker_start = "/* for CSS maybe... {start} */"
        css_marker_end = "/* that's all folks! {end} */"
        b = BlockInFile(
            block_file,
            name="block1",
            start_marker=css_marker_start,
            end_marker=css_marker_end,
            contents="""
            body { font-size: 10px; }
            h1 {
                color: chartreuse;
                font-size: 14px;
            }
        """,
        )
        b.apply()
        contents = block_file.read_text()
        assert "My CSS file with blocks" in contents
        assert "/* for CSS maybe..." in contents
        assert "/* that's all folks!" in contents
        assert "font-size" in contents
        assert "chartreuse" in contents

        b = BlockInFile(block_file, name="block1", start_marker=css_marker_start, end_marker=css_marker_end, ensures="absent")
        b.apply()
        assert "chartreuse" not in block_file.read_text()

    def test_init_validation(self) -> None:
        block_file = self.root / "test7.txt"
        with self.assertRaises(AssertionError):
            BlockInFile(block_file, name="block1", start_marker="# foo", end_marker=self.end_marker, contents=self.line1)
        with self.assertRaises(AssertionError):
            BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker="# bar", contents=self.line1)
        with self.assertRaises(AssertionError):
            BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker)
        with self.assertRaises(AssertionError):
            BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents="foo", ensures="absent")
        with self.assertRaises(AssertionError):
            BlockInFile(block_file, name="My Funky Block Name!", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)

    def test_block_removal_skipped_when_already_absent(self) -> None:
        block_file = self.root / "test8.txt"
        block_file.write_text(self.default_contents)

        with patch("wool.BlockInFile.logger") as mock_logger:
            b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, ensures="absent")
            b.apply()
            assert "Skipping" in mock_logger.info.call_args_list[0].kwargs["action"]
            assert "block does not exist" in mock_logger.info.call_args_list[0].kwargs["because"]
        assert block_file.read_text() == self.default_contents

    def test_block_removal(self) -> None:
        block_file = self.root / "test9.txt"
        block_file.write_text(self.default_contents)
        b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b.apply()
        assert self.line1 in block_file.read_text()
        b = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, ensures="absent")
        b.apply()
        assert self.line1 not in block_file.read_text()

    def test_multiple_blocks(self) -> None:
        block_file = self.root / "test10.txt"
        block_file.write_text(self.default_contents)
        b1 = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line1)
        b1.apply()
        b2 = BlockInFile(block_file, name="block2", start_marker=self.start_marker, end_marker=self.end_marker, contents=self.line2)
        b2.apply()
        contents = block_file.read_text()
        assert self.line1 in contents
        assert self.line2 in contents
        b3 = BlockInFile(block_file, name="block1", start_marker=self.start_marker, end_marker=self.end_marker, ensures="absent")
        b3.apply()
        contents = block_file.read_text()
        assert self.line1 not in contents
        assert self.line2 in contents
        b4 = BlockInFile(block_file, name="block2", start_marker=self.start_marker, end_marker=self.end_marker, ensures="absent")
        b4.apply()
        contents = block_file.read_text()
        assert self.line1 not in contents
        assert self.line2 not in contents


class TestWoolHostkey(WoolFileSystemTestCase):
    line1 = "github.com ssh-rsa AAAAB3Nzfoo...wsjk="
    line2 = "github.com ssh-rsa AAAAB3Nzbar...wsjk="

    def test_hostkey_create_destroy_with_contents(self) -> None:
        hostkey_file = self.root / "known_hosts"
        h = Hostkey(hostkey_file, host="github.com", contents=self.line1)
        assert not hostkey_file.exists()
        h.apply()
        assert hostkey_file.exists()
        assert self.line1 in hostkey_file.read_text()

        h = Hostkey(hostkey_file, host="github.com", ensures="absent")
        h.apply()
        assert self.line1 not in hostkey_file.read_text()

    @patch("wool.wool.fetch_host_keys")
    def test_hostkey_fetch_remote(self, mock_fetch: Any) -> None:
        mock_fetch.return_value = "github.com ssh-rsa AAAAB3Nzbaz...key=\n"

        hostkey_file = self.root / "known_hosts_fetch"
        h = Hostkey(hostkey_file, host="github.com")
        h.apply()

        mock_fetch.assert_called_once_with("github.com")

        assert hostkey_file.exists()
        assert "baz" in hostkey_file.read_text()

    def test_hostkey_fetch_keys_failure(self) -> None:
        hostkey_file = self.root / "known_hosts_fetch_fail"
        bad_host = f"{uniq()}.invalid"
        h = Hostkey(hostkey_file, host=bad_host)

        with self.assertRaises(RuntimeError) as context:
            h.apply()

        assert "failed for host" in str(context.exception)
        assert bad_host in str(context.exception)

    def test_hostkey_update_contents_without_force(self) -> None:
        hostkey_file = self.root / "known_hosts_force_false"
        h1 = Hostkey(hostkey_file, host="github.com", contents=self.line1)
        h1.apply()
        h2 = Hostkey(hostkey_file, host="github.com", contents=self.line2)
        with self.assertRaises(RuntimeError) as context:
            h2.apply()
        assert "force=False" in str(context.exception)
        assert "Hostkey changes should be manually verified" in str(context.exception)
        assert self.line1 in hostkey_file.read_text()
        assert self.line2 not in hostkey_file.read_text()

    @patch("subprocess.run")
    def test_hostkey_fetch_then_force_update(self, mock_run: Any) -> None:
        mock_run.return_value = MagicMock(stdout="github.com ssh-rsa AAAAB3Nzqux...key=\n", stderr="", returncode=0)
        hostkey_file = self.root / "known_hosts_fetch_then_update"

        h1 = Hostkey(hostkey_file, host="github.com")
        h1.apply()
        assert "qux" in hostkey_file.read_text()
        assert self.line1 not in hostkey_file.read_text()

        h2 = Hostkey(hostkey_file, host="github.com", contents=self.line1, force=True)
        h2.apply()
        assert "qux" not in hostkey_file.read_text()
        assert self.line1 in hostkey_file.read_text()

    def test_hostkey_update_contents_with_force(self) -> None:
        hostkey_file = self.root / "known_hosts_force_true"
        h1 = Hostkey(hostkey_file, host="github.com", contents=self.line1)
        h1.apply()
        h2 = Hostkey(hostkey_file, host="github.com", contents=self.line2, force=True)
        h2.apply()
        assert self.line1 not in hostkey_file.read_text()
        assert self.line2 in hostkey_file.read_text()

    def test_hostkey_no_change_when_content_matches(self) -> None:
        hostkey_file = self.root / "known_hosts_skip"
        h1 = Hostkey(hostkey_file, host="github.com", contents=self.line1)
        h1.apply()

        with patch("wool.Hostkey.logger") as mock_logger:
            h2 = Hostkey(hostkey_file, host="github.com", contents=self.line1)
            h2.apply()
            assert "Skipping" in mock_logger.info.call_args_list[0].kwargs["action"]
            assert "blocks already match" in mock_logger.info.call_args_list[0].kwargs["because"]


class TestWoolTouch(WoolFileSystemTestCase):
    def test_touch_new_file(self) -> None:
        target = self.root / "new-touch-file"
        assert not target.exists()

        t = Touch(target)
        t.apply()

        assert target.exists() and target.is_file()
        assert target.stat().st_size == 0, "Touched file should be empty"

    def test_touch_existing_file(self) -> None:
        target = self.root / "existing-touch-file"
        target.write_text("existing content")
        original_mtime = target.stat().st_mtime
        time.sleep(0.1)  # wait a bit to ensure mtime would change if the file was touched

        with self.assertLogs(level="INFO") as logs:
            t = Touch(target)
            t.apply()

        assert target.exists() and target.is_file()
        assert "Skipping touch" in "\n".join(logs.output)
        assert str(target) in "\n".join(logs.output)
        assert target.stat().st_mtime == original_mtime, "mtime should not have changed since we skipped the touch"
