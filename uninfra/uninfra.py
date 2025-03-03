"""
Uninfra: Pure Python Configuration Management.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
from string import Template
from pathlib import Path

# XXX: some code I once used for writing Nginx configs using a `Template`, with "idempotency check"
"""
ips = check_output(["hostname", "--all-ip-addresses"]).strip()
with open("/tmp/app.conf", "w") as f:
    f.write(Template(NGINX_CONFIG).substitute(
        ips=ips
    ))
if file_needs_update(src="/tmp/app.conf", dst="/etc/nginx/conf.d/app.conf"):
    run(["sudo", "mv", "/tmp/app.conf", "/etc/nginx/conf.d/app.conf"])
    run(["sudo", "service", "nginx", "reload"])
"""

## start systemd code #########################################################

# XXX: currently unused

SERVICE_CONFIG = """
[Unit]
Description=${service_description}
After=network.target

[Service]
User=${user}
WorkingDirectory=${repo_path}
Type=simple
Environment=${env}
ExecStart=${gunicorn_path} --error-logfile=- --access-logfile=- --log-syslog --bind=unix:/tmp/gunicorn.sock --workers=4 app:app
KillMode=mixed

[Install]
WantedBy=multi-user.target
"""

"""
# gunicorn systemd service config
gunicorn_service_src = "/tmp/gunicorn.service"
gunicorn_service_dst = "/etc/systemd/system/multi-user.target.wants/gunicorn.service"
with open(gunicorn_service_src, "w") as f:
    f.write(Template(SERVICE_CONFIG).substitute(
        user=os.environ['USER'],
        secret_key_path=os.path.join(os.environ['HOME'], '.moviepicker-secret'),
        gunicorn_path=os.path.join(venv_path, "bin/gunicorn"),
        repo_path=repo_path,
    ))
if file_needs_update(src=gunicorn_service_src, dst=gunicorn_service_dst):
    run(["sudo", "mv", gunicorn_service_src, gunicorn_service_dst])
    run(["sudo", "systemctl", "daemon-reload"])
    run(["sudo", "service", "gunicorn", "restart"])
"""

## end systemd code ###########################################################


def run(cmd, **kw):
    print("Running: {} with {}".format(cmd, kw))
    subprocess.check_call(cmd, **kw)


def run_with_output(cmd, **kw):
    print("Getting output from: {} with {}".format(cmd, kw))
    result = subprocess.run(cmd, **kw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return (result.returncode, result.stdout, result.stderr)


def checksum(path):
    checksum, _ = subprocess.check_output(["sha256sum", path], text=True).strip().split()
    return checksum


def file_needs_update(src, dst):
    if not os.path.exists(dst):
        return True
    if checksum(src) != checksum(dst):
        return True
    return False


class Directory:
    def __init__(self, path, exists=True):
        self.path = Path(path).expanduser()
        self.exists = exists

    def run(self):
        if self.exists:
            self.run_positive()
        else:
            self.run_negative()

    def run_positive(self):
        if self.path.is_dir():
            print(f"Skipping directory creation of {self.path} because it already exists.")
        else:
            run(["mkdir", "-p", self.path])

    def run_negative(self):
        if not self.path.is_dir():
            print(f"Skipping directory removal of {self.path} because it doesn't exist.")
        else:
            run(["rm", "-rf", self.path])


class File:
    def __init__(self, path, src=None, contents=None, exists=True):
        if not src and not contents:
            contents = ""

        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser() if src else None
        self.contents = contents
        self.exists = exists

    def run(self):
        if self.exists:
            self.run_positive()
        else:
            self.run_negative()

    def run_positive(self):
        if self.src:
            if file_needs_update(self.src, self.path):
                shutil.copy(self.src, self.path)
            else:
                print(f"Skipped copying of {self.src!r} to {self.path!r} because files match.")
        else:
            is_bytes = isinstance(self.contents, bytes)
            needs_update = True
            if self.path.exists():
                needs_update = checksum(self.path) == hashlib.sha256(self.contents if is_bytes else self.contents.encode())
            if needs_update:
                with open(self.path, "wb" if is_bytes else "w") as f:
                    f.write(self.contents)
            else:
                print(f"Skipped writing of {self.path!r} because checksum matches contents.")

    def run_negative(self):
        if self.path.is_file():
            os.unlink(self.path)
        else:
            print(f"Skipping removal of {self.path!r} beacause it does not exist.")


class User:
    def __init__(self, username, groups=None, shell=None, home=None, system=False, exists=True):
        self.username = username
        self.wanted_groups = groups or []
        self.shell = shell
        self.home = home
        self.system = system
        self.exists = exists

    def is_present(self):
        (status, out, err) = run_with_output(["id", self.username])
        return status == 0 and "uid=" in out

    def pre_run(self):
        self.user_already_exists = self.is_present()

    def get_current_groups(self):
        """Get list of groups user belongs to."""
        (status, out, err) = run_with_output(["groups", self.username])
        if status == 0:
            return out.split(":")[1].strip().split()
        else:
            raise RuntimeError(f"Got unexpected response from `groups`: {(status, out, err)!r}.")

    def run(self):
        self.pre_run()
        if self.exists:
            self.run_positive()
        else:
            self.run_negative()

    def run_positive(self):
        if self.user_already_exists:
            print(f"Skipping user creation for {self.username!r} because user already exists.")
        else:
            cmd = ["sudo", "useradd"]
            if self.system:
                cmd.append("--system")
            if self.shell:
                cmd.extend(["--shell", self.shell])
            if self.home:
                cmd.extend(["--home-dir", self.home])
            if self.wanted_groups:
                cmd.extend(["--groups", ",".join(self.wanted_groups)])
            cmd.append(self.username)
            run(cmd)

        if self.wanted_groups:
            current_groups = self.get_current_groups()
            groups_to_add = set(self.wanted_groups) - set(current_groups)
            groups_to_remove = set(current_groups) - set(self.wanted_groups)

            for group in groups_to_add:
                run(["sudo", "usermod", "-a", "-G", group, self.username])
            for group in groups_to_remove:
                run(["sudo", "gpasswd", "-d", self.username, group])

    def run_negative(self):
        if self.user_already_exists:
            run(["sudo", "userdel", "-r", self.username])
        else:
            print(f"Skipping user deletion for {self.username!r} because user doesn't exist.")


class Download:
    def __init__(self, url, provides):
        self.url = url
        self.provides = Path(provides).expanduser()

    def run(self):
        if self.provides.is_file():
            print(f"Skipping download {self.url} because {self.provides} exists.")
            return
        run(["curl", "-o", self.provides, self.url])


def apt_pkg_is_installed(name):
    (_, output, _) = run_with_output(["dpkg-query", "-W", "-f='${Status}'", name])
    if "ok not-installed" in output:
        return False
    elif "ok installed" in output:
        return True
    else:
        raise RuntimeError(f"Unable to parse dkpg-query result: {output!r}.")


def apt_pkg_install(name):
    run(["sudo", "apt-get", "install", "-y", name])


def apt_pkg_remove(name):
    run(["sudo", "apt-get", "remove", "-y", name])


class AptPackage:
    def __init__(self, name, provides=None, exists=True):
        self.name = name
        self.provides = Path(provides).expanduser() if provides else None
        self.exists = exists

    def pre_run(self):
        self.package_already_installed = self.is_installed()

    def is_installed(self):
        return apt_pkg_is_installed(self.name)

    def run(self):
        self.pre_run()
        if self.exists:
            self.run_positive()
        else:
            self.run_negative()

    def run_positive(self):
        needs_install = False
        if self.provides and not self.provides.is_file():
            needs_install = True
        elif not self.package_already_installed:
            needs_install = True

        if needs_install:
            apt_pkg_install(self.name)
        else:
            print(f"Skipping package install of {self.name} because it's already installed.")

    def run_negative(self):
        if self.package_already_installed:
            apt_pkg_remove(self.name)
        else:
            print(f"Skipping package removal of {self.name} because it's not installed.")


class Virtualenv:
    def __init__(self, python_bin, path):
        self.python_bin = Path(python_bin).expanduser()
        self.path = Path(path).expanduser()
        self.pip_path = self.path / "bin/pip"

    def run(self):
        if self.path.exists():
            print(f"Skipping venv creation of {self.path} because it already exists.")
            return
        run([self.python_bin, "-mvenv", self.path])

    # TODO: make a method for this?
    # run([pip_path, "install", "-r", os.path.join(repo_path, "requirements.txt")])


class Command:
    def __init__(self, *args):
        self.args = list(args)

    def run(self):
        run(self.args)


# TODO: Secrets class?

## main #######################################################################


def demo():
    steps = [
        AptPackage("nginx", provides="/usr/sbin/nginx"),
        AptPackage("nginx", exists=False),
        AptPackage("nginx", exists=False),
        AptPackage("nginx"),
        Directory("/tmp/foo/bar"),
        Directory("/tmp/foo/bar", exists=False),
        Directory("/tmp/foo/bar", exists=False),
        Directory("/tmp/foo/bar"),
        Download("http://lost-theory.org/robots.txt", provides="/tmp/foo/bar/robots.txt"),
        Virtualenv("/usr/bin/python3", "~/testing-env"),
        Command("/bin/bash", "-c", "date | tee -a /tmp/foo/out.log"),
        User("app"),
        User("app", exists=False),
        Directory("/tmp/app-home/"),
        User("app", groups=["foo"], shell="/bin/bash", home="/tmp/app-home/"),
    ]
    for step in steps:
        step.run()


def ltorg_stage_1():
    steps = [
        AptPackage("mercurial", provides="/usr/bin/hg"),
        AptPackage("python3.12-venv"),
        User("app"),
        Directory("/tmp/foo/bar"),
        Download("http://lost-theory.org/robots.txt", provides="/tmp/foo/bar/robots.txt"),
        Virtualenv("/usr/bin/python3", "~/testing-env"),
        Command("/bin/bash", "-c", "date | tee -a /tmp/foo/out.log"),
    ]
    for step in steps:
        step.run()


def cm_run(task_name):
    tasks = {
        "demo": demo,
        "ltorg_stage_1": ltorg_stage_1,
    }
    task_func = tasks[task_name]
    return task_func()


def push(remote, task_name):
    # Remote python version check
    (_, output, _) = run_with_output(["ssh", "-A", remote, "python3 -c 'import platform; print(tuple(map(int, platform.python_version_tuple())) >= (3, 6, 0))'"])
    assert output.strip() == "True", "Invalid remote python version. Need >=3.6.0."

    # Push and run
    run(["scp", __file__, "{}:/tmp/uninfra.py".format(remote)])
    run(["ssh", "-A", remote, f"python3 -u /tmp/uninfra.py --run --task={task_name}"])


def main():
    parser = argparse.ArgumentParser(description="Simple pure python config management")
    parser.add_argument("--push", type=str, metavar="USER@HOST", help="Push and run on remote user@host via SSH.")
    parser.add_argument("--run", action="store_true", help="Used internally by --push. Or you can run it yourself on a local machine.")
    parser.add_argument("--task", type=str, metavar="NAME", help="The name of the task to run.")

    args = parser.parse_args()

    if args.task:
        assert args.task.isidentifier(), f"Invalid task name: {args.task!r}"

    if args.push and args.task:
        push(args.push, args.task)
    elif args.run and args.task:
        cm_run(args.task)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
