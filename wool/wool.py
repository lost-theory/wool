"""
Wool: Pure Python Configuration Management.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import grp
import pwd
import stat
from textwrap import dedent
from pathlib import Path

## utils ######################################################################


def shell(cmd, **kw):
    """Runs `cmd`, raising an error if it fails."""
    print("Running: {} with {}".format(cmd, kw))
    subprocess.check_call(cmd, **kw)


def shell_output(cmd, **kw):
    """
    Runs `cmd`, returning (returncode, stdout, stderr). Does not raise an error
    on command failure.
    """
    print("Getting output from: {} with {}".format(cmd, kw))
    result = subprocess.run(cmd, **kw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return (result.returncode, result.stdout, result.stderr)


def checksum(path):
    checksum, _ = subprocess.check_output(["sha256sum", path], text=True).strip().split()
    return checksum


def checksum_bytes(b):
    return hashlib.sha256(b).hexdigest()


def file_needs_update(src, dst):
    if not os.path.exists(dst):
        return True
    if checksum(src) != checksum(dst):
        return True
    return False


def make_remote_task_dir(task_name):
    return Path(f"/tmp/wool-task-{task_name}/")


def apt_pkg_install(name):
    shell(["sudo", "apt-get", "install", "-y", name])


def apt_pkg_remove(name):
    shell(["sudo", "apt-get", "remove", "-y", name])


def apt_pkg_is_installed(name):
    (status, out, err) = shell_output(["dpkg-query", "-W", "-f='${Status}'", name])
    if "ok installed" in out:
        return True
    elif "ok not-installed" in out:
        return False
    elif status != 0 and "no packages found matching" in err:
        return False
    else:
        raise RuntimeError(f"Unable to parse dkpg-query result: {[status, out, err]}.")


## resource base classes ######################################################


class ResourceMeta(type):
    """Metaclass to validate Resource subclasses."""

    def __new__(mcs, name, bases, attrs):
        # Skip validation for the baseclasses.
        if name == "Resource" or name == "SimpleResource":
            return super().__new__(mcs, name, bases, attrs)

        # Check that subclasses of Resource (but not SimpleResource) have 'ensures' kwarg for __init__.
        if Resource in bases and SimpleResource not in bases:
            init = attrs.get("__init__")
            if init:
                import inspect

                sig = inspect.signature(init)
                if "ensures" not in sig.parameters:
                    raise TypeError(f"Class {name}.__init__ must be defined with 'ensures' kwarg.")

        return super().__new__(mcs, name, bases, attrs)


class Resource(metaclass=ResourceMeta):
    ENSURES_VALUES = ["present", "absent"]

    def apply(self):
        """Apply the resource based on its ensures value."""
        if self.ensures == "present":
            self.create()
        elif self.ensures == "absent":
            self.destroy()
        else:
            raise ValueError(f"Unsupported value for `ensures`: {self.ensures!r}. Valid values are: {self.VALID_ENSURES!r}")

    def create(self):
        """Create/enable/etc. the resource when ensures='present'."""
        raise NotImplementedError()

    def destroy(self):
        """Destroy/remove/disable/etc. the resource when ensures='absent'."""
        raise NotImplementedError()


class SimpleResource(Resource):
    def apply(self):
        raise NotImplementedError()

    def create(self):
        raise RuntimeError("SimpleResources cannot be created.")

    def destroy(self):
        raise RuntimeError("SimpleResources cannot be destroyed.")


## resources ##################################################################


class Directory(Resource):
    def __init__(self, path, ensures="present"):
        self.path = Path(path).expanduser()
        self.ensures = ensures

    def create(self):
        if self.path.is_dir():
            print(f"Skipping directory creation of {self.path} because it already exists.")
        else:
            shell(["mkdir", "-p", self.path])

    def destroy(self):
        if not self.path.is_dir():
            print(f"Skipping directory removal of {self.path} because it doesn't exist.")
        else:
            shell(["rm", "-rf", self.path])


class File(Resource):
    def __init__(self, path, src=None, contents=None, ensures="present"):
        if not src and not contents:
            contents = ""

        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser() if src else None
        self.contents = contents
        self.ensures = ensures

    def create(self):
        if self.src:
            if file_needs_update(self.src, self.path):
                shutil.copy(self.src, self.path)
            else:
                print(f"Skipping copy of {self.src!r} to {self.path!r} because files match.")
        else:
            is_bytes = isinstance(self.contents, bytes)
            needs_update = True
            if self.path.exists():
                needs_update = checksum(self.path) != checksum_bytes(self.contents if is_bytes else self.contents.encode())
            if needs_update:
                with open(self.path, "wb" if is_bytes else "w") as f:
                    f.write(self.contents)
            else:
                print(f"Skipping writing of {self.path!r} because checksum matches contents.")

    def destroy(self):
        if self.path.is_file():
            os.unlink(self.path)
        else:
            print(f"Skipping removal of {self.path!r} beacause it does not exist.")


class User(Resource):
    def __init__(self, username, group=None, groups=None, shell=None, home=None, system=False, ensures="present"):
        self.username = username
        self.primary_group = group
        self.wanted_groups = groups or []
        self.shell = shell
        self.home = home
        self.system = system
        self.ensures = ensures

    def exists(self):
        try:
            u = pwd.getpwnam(self.username)
            return True
        except KeyError:
            return False

    def get_current_groups(self):
        """Get list of groups user belongs to."""
        return set(g.gr_name for g in grp.getgrall() if self.username in g.gr_mem)

    def create(self):
        if self.exists():
            print(f"Skipping user creation for {self.username!r} because user already exists.")
        else:
            cmd = ["sudo", "useradd"]
            if self.system:
                cmd.append("--system")
            if self.shell:
                cmd.extend(["--shell", self.shell])
            if self.home:
                cmd.extend(["--home-dir", self.home])
            if self.primary_group:
                cmd.extend(["-g", self.primary_group])
            if self.wanted_groups:
                cmd.extend(["--groups", ",".join(self.wanted_groups)])
            cmd.append(self.username)
            shell(cmd)

        if self.wanted_groups:
            current_groups = self.get_current_groups()
            groups_to_add = set(self.wanted_groups) - set(current_groups)
            groups_to_remove = set(current_groups) - set(self.wanted_groups)

            for group in groups_to_add:
                shell(["sudo", "usermod", "-a", "-G", group, self.username])
            for group in groups_to_remove:
                shell(["sudo", "gpasswd", "-d", self.username, group])

    def destroy(self):
        if self.exists():
            shell(["sudo", "userdel", "-r", self.username])
        else:
            print(f"Skipping user deletion for {self.username!r} because user doesn't exist.")


class Group(Resource):
    def __init__(self, groupname, system=False, ensures="present"):
        self.groupname = groupname
        self.system = system
        self.ensures = ensures

    def exists(self):
        try:
            g = grp.getgrnam(self.groupname)
            return True
        except KeyError:
            return False

    def create(self):
        if self.exists():
            print(f"Skipping group creation for {self.groupname!r} because group already exists.")
            return
        cmd = ["sudo", "groupadd"]
        if self.system:
            cmd.append("--system")
        cmd.append(self.groupname)
        shell(cmd)

    def destroy(self):
        if self.exists():
            shell(["sudo", "groupdel", self.groupname])
        else:
            print(f"Skipping group deletion for {self.groupname!r} because group doesn't exist.")


class Download(SimpleResource):
    def __init__(self, url, provides):
        self.url = url
        self.provides = Path(provides).expanduser()

    def apply(self):
        if self.provides.is_file():
            print(f"Skipping download {self.url} because {self.provides} exists.")
            return
        shell(["curl", "-L", "-o", self.provides, self.url])


class AptPackage(Resource):
    def __init__(self, name, provides=None, ensures="present"):
        self.name = name
        self.provides = Path(provides).expanduser() if provides else None
        self.ensures = ensures

    def is_installed(self):
        return apt_pkg_is_installed(self.name)

    def create(self):
        needs_install = False
        if self.provides and not self.provides.is_file():
            needs_install = True
        elif not self.is_installed():
            needs_install = True

        if needs_install:
            apt_pkg_install(self.name)
        else:
            print(f"Skipping package install of {self.name} because it's already installed.")

    def destroy(self):
        if self.is_installed():
            apt_pkg_remove(self.name)
        else:
            print(f"Skipping package removal of {self.name} because it's not installed.")


class Virtualenv(SimpleResource):
    def __init__(self, python_bin, path):
        self.python_bin = Path(python_bin).expanduser()
        self.path = Path(path).expanduser()
        self.pip_path = self.path / "bin/pip"

    def apply(self):
        if self.path.exists():
            print(f"Skipping venv creation of {self.path} because it already exists.")
            return
        shell([self.python_bin, "-mvenv", self.path])


class Command(SimpleResource):
    def __init__(self, *args, provides=None):
        self.args = list(args)
        self.provides = Path(provides).expanduser() if provides else None

    def apply(self):
        if self.provides and self.provides.exists():
            print(f"Skipping command because {self.provides} already exists.")
            return
        shell(self.args)


class Owner(SimpleResource):
    def __init__(self, path, user=None, group=None):
        self.path = Path(path).expanduser()
        self.user = user
        self.group = group

        if not self.user and not self.group:
            raise ValueError("At least one of `user` or `group` must be specified.")

    def apply(self):
        if not self.path.exists():
            raise RuntimeError(f"Cannot change ownership of {self.path!r} because it doesn't exist.")

        # Get current ownership
        stat_info = self.path.stat()
        current_uid, current_gid = stat_info.st_uid, stat_info.st_gid
        uid, gid = current_uid, current_gid

        if self.user:
            try:
                uid = pwd.getpwnam(self.user).pw_uid
            except KeyError:
                raise ValueError(f"User {self.user!r} does not exist.")

        if self.group:
            try:
                gid = grp.getgrnam(self.group).gr_gid
            except KeyError:
                raise ValueError(f"Group {self.group!r} does not exist.")

        # Check if change is needed
        if uid == current_uid and gid == current_gid:
            print(f"Skipping ownership change for {self.path!r} because it already has the correct ownership.")
            return

        # Change ownership
        shell(["sudo", "chown", f"{uid}:{gid}", self.path])


class Perms(SimpleResource):
    def __init__(self, path, mode):
        self.path = Path(path).expanduser()
        if isinstance(mode, str):
            mode = int("0o" + mode, 8)
        self.mode = mode

    def apply(self):
        if not self.path.exists():
            raise RuntimeError(f"Cannot change permissions of {self.path!r} because it doesn't exist.")

        # Get current permissions
        current_mode = stat.S_IMODE(self.path.stat().st_mode)

        # Check if change is needed
        if current_mode == self.mode:
            print(f"Skipping permission change for {self.path!r} because it already has mode {oct(self.mode)}.")
            return

        # Change permissions
        self.path.chmod(self.mode)
        print(f"Changed permissions of {self.path!r} to {oct(self.mode)}.")


class Symlink(Resource):
    def __init__(self, path, src, ensures="present"):
        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser()
        self.ensures = ensures

    def create(self):
        if not self.src.exists():
            print(f"Warning! Source {self.src!r} does not exist, but creating symlink anyway.")

        if self.path.exists() and not self.path.is_symlink():
            raise RuntimeError(f"Cannot create symlink at {self.path!r} because a file or directory already exists there.")

        if self.path.is_symlink():
            current_src = Path(os.readlink(self.path))
            if current_src == self.src:
                print(f"Skipping symlink creation for {self.path!r} because it already points to {self.src!r}.")
                return
            else:
                print(f"Updating symlink {self.path!r} to point to {self.src!r} instead of {current_src!r}.")
                self.path.unlink()

        # Create the symlink
        os.symlink(src=self.src, dst=self.path)
        print(f"Created symlink from {self.path!r} to {self.src!r}.")

    def destroy(self):
        if self.path.is_symlink():
            self.path.unlink()
            print(f"Removed symlink {self.path!r}.")
        else:
            print(f"Skipping removal of symlink {self.path!r} because it doesn't exist.")


## main #######################################################################


def wool_apply(task_name, tasks):
    task_func = tasks[task_name]
    task_dir = make_remote_task_dir(task_name)
    if not task_dir.is_dir():
        task_dir = None
    return task_func(task_dir)


def wool_push(remote, task_name):
    # Remote python version check
    (_, output, _) = shell_output(["ssh", "-A", remote, "python3 -c 'import platform; print(tuple(map(int, platform.python_version_tuple())) >= (3, 6, 0))'"])
    assert output.strip() == "True", "Invalid remote python version. Need >=3.6.0."

    # Rsync up task directory if it exists
    task_dir = Path(".") / task_name
    remote_task_dir = make_remote_task_dir(task_name)
    if task_dir.is_dir():
        shell(["rsync", "-pthrvz", "--delete", f"{task_dir}/", f"{remote}:{remote_task_dir}"])

    # Push and run
    shell(["scp", __file__, "{}:/tmp/wool_push.py".format(remote)])
    shell(["ssh", "-A", remote, f"python3 -u /tmp/wool_push.py --apply --task={task_name}"])


def wool_main(tasks):
    parser = argparse.ArgumentParser(description="Simple pure python config management")
    parser.add_argument("--push", type=str, metavar="USER@HOST", help="Push and apply on remote user@host via SSH.")
    parser.add_argument("--apply", action="store_true", help="Used internally by --push. Or you can run it yourself on a local machine.")
    parser.add_argument("--task", type=str, metavar="NAME", help="The name of the task to apply.")

    args = parser.parse_args()

    if args.task:
        assert args.task.isidentifier(), f"Invalid task name: {args.task!r}"

    if args.push and args.task:
        wool_push(args.push, args.task)
    elif args.apply and args.task:
        wool_apply(args.task, tasks)
    else:
        parser.print_help()
