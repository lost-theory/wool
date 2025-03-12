#!/usr/bin/env python

import os
import tempfile
import unittest
import random
import string
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from woolwork.wool import Directory, File, User, Download, AptPackage, Virtualenv, Command

TEST_DIR_EXISTS_NAME = "foo"
TEST_DIR_CREATE_NAME = "bah"
TEST_FILE_EXISTS_NAME = "bar.txt"
TEST_FILE_SRC_NAME = "baz.txt"
TEST_FILE_DEST_NAME = "qux.txt"
TEST_FILE_CONTENTS_NAME = "haha.txt"
TEST_FILE_SRC = "Hello world from src!\n"
TEST_FILE_CONTENTS = "Hello world from contents!\n"


class TestWoolwork(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmpdir.name)

        cls.existing_dir_for_destroy = cls.root / TEST_DIR_EXISTS_NAME
        os.mkdir(cls.existing_dir_for_destroy)

        cls.existing_file_for_src = cls.root / TEST_FILE_SRC_NAME
        with open(cls.existing_file_for_src, "w") as f:
            f.write(TEST_FILE_SRC)

        cls.existing_file_for_destroy = cls.root / TEST_FILE_EXISTS_NAME
        with open(cls.existing_file_for_destroy, "w") as f:
            f.write("This will be deleted very soon... It's not read anywhere.")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_dir_create_and_destroy(self):
        dirname = self.root / TEST_DIR_CREATE_NAME
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
        destpath = self.root / TEST_FILE_CONTENTS_NAME
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
        destpath = self.root / TEST_FILE_DEST_NAME
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
        timestamp = datetime.now().strftime("%Y%m%d")
        unique_name = "".join(random.sample(string.ascii_lowercase, 14))
        username = f"test-{timestamp}-{unique_name}"
        u = User(username)
        assert not u.is_present()
        u.apply()
        assert u.is_present()
        u = User(username, exists=False)
        assert u.is_present()
        u.apply()
        assert not u.is_present()

    @patch("woolwork.wool.apt_pkg_is_installed")
    @patch("woolwork.wool.apt_pkg_install")
    @patch("woolwork.wool.apt_pkg_remove")
    def test_apt_pkg_create(self, f_remove, f_install, f_installed):
        f_installed.return_value = False
        p = AptPackage("cool-pkg")
        assert not p.is_installed()
        assert f_installed.called
        p.apply()
        assert f_install.called and f_install.call_count == 1
        assert f_install.call_args.args[0] == "cool-pkg"
        assert not f_remove.called

    @patch("woolwork.wool.apt_pkg_is_installed")
    @patch("woolwork.wool.apt_pkg_install")
    @patch("woolwork.wool.apt_pkg_remove")
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
