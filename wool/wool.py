"""
Wool: Pure Python Configuration Management.
"""

import argparse
import grp
import hashlib
import inspect
import os
import pwd
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Union, Mapping, Any, Optional, Sequence

StrOrPath = Union[str, Path]

PROJECT = Path("/tmp/wool-run/")

STAT_MODE_MASKS = [
    stat.S_IRUSR,
    stat.S_IWUSR,
    stat.S_IXUSR,
    stat.S_IRGRP,
    stat.S_IWGRP,
    stat.S_IXGRP,
    stat.S_IROTH,
    stat.S_IWOTH,
    stat.S_IXOTH,
    stat.S_ISUID,
    stat.S_ISGID,
    stat.S_ISVTX,
]

## utils ######################################################################


def shell(cmd: Sequence[StrOrPath], **kw: Any) -> None:
    """Runs `cmd`, raising an error if it fails."""
    print(f"Running: {cmd} with {kw}")
    subprocess.check_call(cmd, **kw)


def shell_output(cmd: Sequence[StrOrPath], **kw: Any) -> tuple[int, str, str]:
    """
    Runs `cmd`, returning (returncode, stdout, stderr). Does not raise an error
    on command failure.
    """
    print(f"Getting output from: {cmd} with {kw}")
    result = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    return (result.returncode, result.stdout, result.stderr)


def checksum(path: StrOrPath) -> str:
    result, _ = subprocess.check_output(["sha256sum", path], text=True).strip().split()
    return result


def checksum_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_needs_update(src: StrOrPath, dst: StrOrPath) -> bool:
    if not os.path.exists(dst):
        return True
    if checksum(src) != checksum(dst):
        return True
    return False


def apt_pkg_install(name: str) -> None:
    shell(["sudo", "apt-get", "install", "-y", name])


def apt_pkg_remove(name: str) -> None:
    shell(["sudo", "apt-get", "remove", "-y", name])


def apt_pkg_is_installed(name: str) -> bool:
    (status, out, err) = shell_output(["dpkg-query", "-W", "-f='${Status}'", name])
    result = False
    if "ok installed" in out:
        result = True
    elif "ok not-installed" in out:
        result = False
    elif status != 0 and "no packages found matching" in err:
        result = False
    else:
        raise RuntimeError(f"Unable to parse dkpg-query result: {[status, out, err]}.")
    return result


@dataclass(frozen=True)
class SymbolicPermissions:  # pylint: disable=too-many-instance-attributes
    mode: int
    user_parts: list[bool]
    group_parts: list[bool]
    other_parts: list[bool]
    special_bits: list[bool]
    ur: bool
    uw: bool
    ux: bool
    gr: bool
    gw: bool
    gx: bool
    othr: bool
    othw: bool
    othx: bool
    setuid: bool
    setgid: bool
    sticky: bool

    def __init__(self, mode: int) -> None:  # pylint: disable=too-many-locals
        parts = [bool(mode & mask) for mask in STAT_MODE_MASKS]
        user_parts = parts[0:3]
        group_parts = parts[3:6]
        other_parts = parts[6:9]
        special_bits = parts[9:12]
        ur, uw, ux = user_parts
        gr, gw, gx = group_parts
        othr, othw, othx = other_parts
        setuid, setgid, sticky = special_bits
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "user_parts", user_parts)
        object.__setattr__(self, "group_parts", group_parts)
        object.__setattr__(self, "other_parts", other_parts)
        object.__setattr__(self, "special_bits", special_bits)
        object.__setattr__(self, "ur", ur)
        object.__setattr__(self, "uw", uw)
        object.__setattr__(self, "ux", ux)
        object.__setattr__(self, "gr", gr)
        object.__setattr__(self, "gw", gw)
        object.__setattr__(self, "gx", gx)
        object.__setattr__(self, "othr", othr)
        object.__setattr__(self, "othw", othw)
        object.__setattr__(self, "othx", othx)
        object.__setattr__(self, "setuid", setuid)
        object.__setattr__(self, "setgid", setgid)
        object.__setattr__(self, "sticky", sticky)

    def __str__(self) -> str:
        rwx_parts = self.user_parts + self.group_parts + self.other_parts
        ur, uw, ux, gr, gw, gx, othr, othw, othx = [symbol if is_set else "-" for (symbol, is_set) in zip("rwxrwxrwx", rwx_parts)]
        setuid = "u+s" if self.setuid else "u-s"
        setgid = "g+s" if self.setgid else "g-s"
        sticky = "o+t" if self.sticky else "o-t"
        return f"u={ur}{uw}{ux}, g={gr}{gw}{gx}, o={othr}{othw}{othx}, {setuid}, {setgid}, {sticky}"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, SymbolicPermissions):
            return self.mode == other.mode
        return str(self) == str(other)

    def __ne__(self, other: Any) -> bool:
        return not self == other


## resource base classes ######################################################


class ResourceMeta(type):
    """Metaclass to validate Resource subclasses."""

    def __new__(mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]) -> type:
        # Skip validation for the baseclasses.
        if name in ("Resource", "SimpleResource"):
            return super().__new__(mcs, name, bases, attrs)

        # Check that subclasses of Resource (but not SimpleResource) have 'ensures' kwarg for __init__.
        if Resource in bases and SimpleResource not in bases:
            init = attrs.get("__init__")
            if init:
                sig = inspect.signature(init)
                if "ensures" not in sig.parameters:
                    raise TypeError(f"Class {name}.__init__ must be defined with 'ensures' kwarg.")

        return super().__new__(mcs, name, bases, attrs)


class Resource(metaclass=ResourceMeta):
    ENSURES_VALUES = ["present", "absent"]

    def apply(self) -> None:
        """Apply the resource based on its ensures value."""
        if not hasattr(self, "ensures"):
            raise AttributeError("Resource subclass must have 'ensures' attribute.")

        ensures = self.ensures  # pylint: disable=no-member
        if ensures == "present":
            self.create()
        elif ensures == "absent":
            self.destroy()
        else:
            raise ValueError(f"Unsupported value for `ensures`: {ensures!r}. Valid values are: {self.ENSURES_VALUES!r}")

    def create(self) -> None:
        """Create/enable/etc. the resource when ensures='present'."""
        raise NotImplementedError()

    def destroy(self) -> None:
        """Destroy/remove/disable/etc. the resource when ensures='absent'."""
        raise NotImplementedError()


class SimpleResource(Resource):
    def apply(self) -> None:
        raise NotImplementedError()

    def create(self) -> None:
        raise RuntimeError("SimpleResources cannot be created.")

    def destroy(self) -> None:
        raise RuntimeError("SimpleResources cannot be destroyed.")


## resources ##################################################################


class Directory(Resource):
    def __init__(self, path: StrOrPath, ensures: str = "present") -> None:
        self.path = Path(path).expanduser()
        self.ensures = ensures

    def create(self) -> None:
        if self.path.is_dir():
            print(f"Skipping directory creation of {self.path} because it already exists.")
        else:
            shell(["mkdir", "-p", self.path])

    def destroy(self) -> None:
        if not self.path.is_dir():
            print(f"Skipping directory removal of {self.path} because it doesn't exist.")
        else:
            shell(["rm", "-rf", self.path])


class File(Resource):
    def __init__(self, path: StrOrPath, src: Optional[StrOrPath] = None, contents: Optional[str | bytes] = None, ensures: str = "present") -> None:
        if not src and not contents:
            contents = ""

        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser() if src else None
        self.contents = contents
        self.ensures = ensures

    def create(self) -> None:
        if self.src:
            if file_needs_update(self.src, self.path):
                shutil.copy(self.src, self.path)
            else:
                print(f"Skipping copy of {self.src!r} to {self.path!r} because files match.")
        elif self.contents:
            if isinstance(self.contents, bytes):
                new_contents = self.contents
            else:
                new_contents = self.contents.encode()
            needs_update = True
            if self.path.exists():
                needs_update = checksum(self.path) != checksum_bytes(new_contents)
            if needs_update:
                with open(self.path, "wb") as f:
                    f.write(new_contents)
            else:
                print(f"Skipping file write to {self.path!r} because checksum matches contents.")
        else:
            raise ValueError("At least one of `src` or `contents` must be specified.")

    def destroy(self) -> None:
        if self.path.is_file():
            os.unlink(self.path)
        else:
            print(f"Skipping removal of {self.path!r} beacause it does not exist.")


class User(Resource):
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        username: str,
        group: Optional[str] = None,
        groups: Optional[list[str]] = None,
        shell_bin: Optional[StrOrPath] = None,
        home: Optional[StrOrPath] = None,
        system: bool = False,
        ensures: str = "present",
    ):
        self.username = username
        self.primary_group = group
        self.wanted_groups = groups or []
        self.shell = Path(shell_bin).expanduser() if shell_bin else None
        self.home = Path(home).expanduser() if home else None
        self.system = system
        self.ensures = ensures

    def exists(self) -> bool:
        try:
            pwd.getpwnam(self.username)
            return True
        except KeyError:
            return False

    def get_current_groups(self) -> set[str]:
        """Get list of groups user belongs to."""
        return set(g.gr_name for g in grp.getgrall() if self.username in g.gr_mem)

    def create(self) -> None:
        if self.exists():
            print(f"Skipping user creation for {self.username!r} because user already exists.")
        else:
            cmd = ["sudo", "useradd"]
            if self.system:
                cmd.append("--system")
            if self.shell:
                cmd.extend(["--shell", str(self.shell)])
            if self.home:
                cmd.extend(["--home-dir", str(self.home)])
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

    def destroy(self) -> None:
        if self.exists():
            shell(["sudo", "userdel", "-r", self.username])
        else:
            print(f"Skipping user deletion for {self.username!r} because user doesn't exist.")


class Group(Resource):
    def __init__(self, groupname: str, system: bool = False, ensures: str = "present") -> None:
        self.groupname = groupname
        self.system = system
        self.ensures = ensures

    def exists(self) -> bool:
        try:
            grp.getgrnam(self.groupname)
            return True
        except KeyError:
            return False

    def create(self) -> None:
        if self.exists():
            print(f"Skipping group creation for {self.groupname!r} because group already exists.")
            return
        cmd = ["sudo", "groupadd"]
        if self.system:
            cmd.append("--system")
        cmd.append(self.groupname)
        shell(cmd)

    def destroy(self) -> None:
        if self.exists():
            shell(["sudo", "groupdel", self.groupname])
        else:
            print(f"Skipping group deletion for {self.groupname!r} because group doesn't exist.")


class Download(SimpleResource):
    def __init__(self, url: str, provides: StrOrPath):
        self.url = url
        self.provides = Path(provides).expanduser()

    def apply(self) -> None:
        if self.provides.is_file():
            print(f"Skipping download {self.url} because {self.provides} exists.")
            return
        shell(["curl", "-L", "-o", self.provides, self.url])


class AptPackage(Resource):
    def __init__(self, name: str, provides: Optional[StrOrPath] = None, ensures: str = "present") -> None:
        self.name = name
        self.provides = Path(provides).expanduser() if provides else None
        self.ensures = ensures

    def is_installed(self) -> bool:
        return apt_pkg_is_installed(self.name)

    def create(self) -> None:
        needs_install = False
        if self.provides and not self.provides.is_file():
            needs_install = True
        elif not self.is_installed():
            needs_install = True

        if needs_install:
            apt_pkg_install(self.name)
        else:
            print(f"Skipping package install of {self.name} because it's already installed.")

    def destroy(self) -> None:
        if self.is_installed():
            apt_pkg_remove(self.name)
        else:
            print(f"Skipping package removal of {self.name} because it's not installed.")


class Virtualenv(SimpleResource):
    def __init__(self, python_bin: StrOrPath, path: StrOrPath) -> None:
        self.python_bin = Path(python_bin).expanduser()
        self.path = Path(path).expanduser()
        self.pip_path = self.path / "bin/pip"

    def apply(self) -> None:
        if self.path.exists():
            print(f"Skipping venv creation of {self.path} because it already exists.")
            return
        shell([self.python_bin, "-mvenv", self.path])


class Command(SimpleResource):
    def __init__(self, cmd: list[StrOrPath], provides: Optional[StrOrPath] = None) -> None:
        self.cmd = list(cmd)
        self.provides = Path(provides).expanduser() if provides else None

    def apply(self) -> None:
        if self.provides and self.provides.exists():
            print(f"Skipping command {self.cmd} because {self.provides} already exists.")
            return
        shell(self.cmd)


class Owner(SimpleResource):
    def __init__(self, path: StrOrPath, user: Optional[str] = None, group: Optional[str] = None, recursive: bool = False) -> None:
        self.path = Path(path).expanduser()
        self.user = user
        self.group = group
        self.recursive = recursive

        if not self.user and not self.group:
            raise ValueError("At least one of `user` or `group` must be specified.")

    def apply(self) -> None:
        if not self.path.exists():
            raise RuntimeError(f"Cannot change ownership of {self.path!r} because it doesn't exist.")

        # Get current ownership
        stat_info = self.path.stat()
        current_uid, current_gid = stat_info.st_uid, stat_info.st_gid
        uid, gid = current_uid, current_gid

        if self.user:
            uid = pwd.getpwnam(self.user).pw_uid

        if self.group:
            gid = grp.getgrnam(self.group).gr_gid

        recursive_flag = []
        if self.recursive:
            recursive_flag = ["-R"]
        else:
            # only do current owner/group check for single file ownership change
            if uid == current_uid and gid == current_gid:
                print(f"Skipping ownership change for {self.path!r} because it already has the correct ownership.")
                return

        shell(["sudo", "chown"] + recursive_flag + [f"{uid}:{gid}", self.path])


class Perms(SimpleResource):
    def __init__(self, path: StrOrPath, mode: Union[int, str], recursive: bool = False) -> None:
        self.path = Path(path).expanduser()
        if isinstance(mode, str):
            mode = int("0o" + mode, 8)
        self.mode = mode
        self.recursive = recursive

    def get_full_mode(self) -> int:
        return self.path.stat().st_mode

    def get_full_mode_str(self) -> str:
        return oct(self.get_full_mode())[2:]

    def get_mode(self) -> int:
        return stat.S_IMODE(self.get_full_mode())

    def get_mode_str(self) -> str:
        return oct(self.get_full_mode())[-3:]

    def get_symbolic(self) -> SymbolicPermissions:
        mode = self.get_full_mode()
        return SymbolicPermissions(mode)

    def apply(self) -> None:
        if not self.path.exists():
            raise RuntimeError(f"Cannot change permissions of {self.path!r} because it doesn't exist.")

        if self.recursive:
            # always run chmod when in recursive mode
            shell(["chmod", "-R", oct(self.mode)[2:], self.path])
        elif self.get_mode() != self.mode:
            # run chmod when current mode doesn't match desired mode
            self.path.chmod(self.mode)
        else:
            print(f"Skipping permission change for {self.path!r} because it already has mode {oct(self.mode)}.")


class Symlink(Resource):
    def __init__(self, path: StrOrPath, src: StrOrPath, ensures: str = "present") -> None:
        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser()
        self.ensures = ensures

    def create(self) -> None:
        if not self.src.exists():
            print(f"Warning! Source {self.src!r} does not exist, but creating symlink anyway.")

        if self.path.exists() and not self.path.is_symlink():
            raise RuntimeError(f"Cannot create symlink at {self.path!r} because a file or directory already exists there.")

        if self.path.is_symlink():
            current_src = Path(os.readlink(self.path))
            if current_src == self.src:
                print(f"Skipping symlink creation for {self.path!r} because it already points to {self.src!r}.")
                return
            print(f"Updating symlink {self.path!r} to point to {self.src!r} instead of {current_src!r}.")
            self.path.unlink()

        # Create the symlink
        os.symlink(src=self.src, dst=self.path)
        print(f"Created symlink from {self.path!r} to {self.src!r}.")

    def destroy(self) -> None:
        if self.path.is_symlink():
            self.path.unlink()
            print(f"Removed symlink {self.path!r}.")
        else:
            print(f"Skipping removal of symlink {self.path!r} because it doesn't exist.")


## main #######################################################################


def wool_apply(task_name: str, tasks: Mapping[str, Callable[[], None]]) -> None:
    task_func = tasks[task_name]
    return task_func()


def wool_push(calling_script: str, remote: str, task_name: str, local_project: Optional[str] = None) -> None:
    # Remote python version check
    (_, output, _) = shell_output(["ssh", "-A", remote, "python3 -c 'import platform; print(tuple(map(int, platform.python_version_tuple())) >= (3, 6, 0))'"])
    assert output.strip() == "True", "Invalid remote python version. Need >=3.6.0."

    # Rsync up local project directory if specified
    shell(["ssh", remote, f"mkdir -p {PROJECT}"])
    if local_project:
        local_project_path = Path(local_project).resolve()
        # Rsync flags:
        # -pt preserve file permissions and mtimes
        # -h display human readable file sizes
        # -r recursive
        # -v verbose
        # -z compression
        # -L follow symlinks and copy their contents
        # --delete remove files on remote that don't exist locally
        shell(["rsync", "-pthrvzL", "--delete", f"{local_project_path}/", f"{remote}:{PROJECT}"])

    # Copy calling script and wool.py to remote host and execute
    calling_script_path = Path(calling_script)
    shell(["scp", __file__, f"{remote}:{PROJECT}/wool.py"])
    shell(["scp", calling_script_path, f"{remote}:{PROJECT}/{calling_script_path.name}"])
    shell(["ssh", "-A", remote, f"python3 -u {PROJECT}/{calling_script_path.name} --apply --task={task_name}"])


def wool_main(calling_script: str, tasks: Mapping[str, Callable[[], None]]) -> None:
    parser = argparse.ArgumentParser(description="Simple pure python config management")
    parser.add_argument("--project", type=str, metavar="PATH", help="(Optional) Path of your wool project which will be rsync-ed to the host.")
    parser.add_argument("--push", type=str, metavar="USER@HOST", help="Push and apply on remote user@host via SSH.")
    parser.add_argument("--apply", action="store_true", help="Used internally by --push. Or you can run it yourself on a local machine.")
    parser.add_argument("--task", type=str, metavar="NAME", help="The name of the task to apply.")

    args = parser.parse_args()

    if args.task:
        assert args.task.isidentifier(), f"Invalid task name: {args.task!r}"

    if args.push and args.task:
        wool_push(calling_script, args.push, args.task, args.project)
    elif args.apply and args.task:
        wool_apply(args.task, tasks)
    else:
        parser.print_help()


if __name__ == "__main__":
    print("Wool is a library, not a framework or script. You need to import wool_main from your own code.")
    sys.exit(1)
