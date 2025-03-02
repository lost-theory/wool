"""
Uninfra: Pure Python Configuration Management.
"""

import argparse
import os
import subprocess
from string import Template

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
    def __init__(self, path):
        self.path = os.path.expanduser(path)

    def run(self):
        if os.path.isdir(self.path):
            print(f"Skipping directory creation of {self.path} because it already exists.")
            return
        run(["mkdir", "-p", self.path])


class User:
    def __init__(self, username, groups=None, shell=None, home=None, system=False):
        self.username = username
        self.wanted_groups = groups or []
        self.shell = shell
        self.home = home
        self.system = system

    def get_current_groups(self):
        """Get list of groups user belongs to."""
        (status, out, err) = run_with_output(["groups", self.username])
        if status == 0:
            return out.split(":")[1].strip().split()
        else:
            raise RuntimeError(f"Got unexpected response from `groups`: {(status, out, err)!r}.")

    def run(self):
        # Check if user exists
        (status, out, err) = run_with_output(["id", self.username])
        user_exists = status == 0 and "uid=" in out

        if not user_exists:
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
        else:
            print(f"Skipping user creation for {self.username!r} because user already exists.")

        if self.wanted_groups:
            current_groups = self.get_current_groups()
            groups_to_add = set(self.wanted_groups) - set(current_groups)
            groups_to_remove = set(current_groups) - set(self.wanted_groups)

            for group in groups_to_add:
                run(["sudo", "usermod", "-a", "-G", group, self.username])
            for group in groups_to_remove:
                run(["sudo", "gpasswd", "-d", self.username, group])


class Download:
    def __init__(self, url, provides):
        self.url = url
        self.provides = os.path.expanduser(provides)

    def run(self):
        if os.path.isfile(self.provides):
            print(f"Skipping download {self.url} because {self.provides} exists.")
            return
        run(["curl", "-o", self.provides, self.url])


class AptPackage:
    def __init__(self, name, provides=None):
        self.name = name
        self.provides = os.path.expanduser(provides) if provides else None

    def is_installed(self):
        (_, output, _) = run_with_output(["dpkg-query", "-W", "-f='${Status}'", self.name])
        if "ok not-installed" in output:
            return False
        elif "ok installed" in output:
            return True
        else:
            raise RuntimeError(f"Unable to parse dkpg-query result: {output!r}.")

    def run(self):
        install = False
        if self.provides:
            if os.path.isfile(self.provides):
                print(f"Skipping apt install of {self.name} because {self.provides} exists.")
            else:
                install = True
        else:
            if self.is_installed():
                print(f"Skipping apt install of {self.name} because it's already installed.")
            else:
                install = True

        if install:
            run(["sudo", "apt-get", "install", "-y", self.name])


class Virtualenv:
    def __init__(self, python_bin, path):
        self.python_bin = os.path.expanduser(python_bin)
        self.venv_path = os.path.expanduser(path)
        self.pip_path = os.path.join(self.venv_path, "bin/pip")

    def run(self):
        if os.path.exists(self.venv_path):
            print(f"Skipping venv creation of {self.venv_path} because it already exists.")
            return
        run([self.python_bin, "-mvenv", self.venv_path])

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
        Directory("/tmp/foo/bar"),
        Download("http://lost-theory.org/robots.txt", provides="/tmp/foo/bar/robots.txt"),
        Virtualenv("/usr/bin/python3", "~/testing-env"),
        Command("/bin/bash", "-c", "date | tee -a /tmp/foo/out.log"),
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
    run(["scp", __file__, "{}:/tmp/cm.py".format(remote)])
    run(["ssh", "-A", remote, f"python3 -u /tmp/cm.py --run --task={task_name}"])


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
