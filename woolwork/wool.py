"""
Woolwork: Pure Python Configuration Management.
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

## caddy ######################################################################

CADDYFILE_AMBIX = '''
{
    auto_https off
    http_port 80
    https_port 443
}
:8080 {
    reverse_proxy https://ce.ocmca.org {
        header_up Host ce.ocmca.org
        transport http {
            tls
            tls_insecure_skip_verify
        }
    }
}
'''

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

SYSTEMD_CADDY = """
[Unit]
Description=Caddy
After=network.target

[Service]
User=caddy
Group=caddy
WorkingDirectory=/opt/caddy/
ExecStart=/opt/caddy/bin/caddy run --environ --config /opt/caddy/ambix.caddy --adapter caddyfile
ExecReload=/opt/caddy/bin/caddy reload --config /opt/caddy/ambix.caddy --adapter caddyfile
LimitNOFILE=1048576
LimitNPROC=512
Restart=on-failure
PrivateTmp=true
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
"""

# gunicorn systemd service config
"""
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

## utils ######################################################################

def shell(cmd, **kw):
    '''Runs `cmd`, raising an error if it fails.'''
    print("Running: {} with {}".format(cmd, kw))
    subprocess.check_call(cmd, **kw)


def shell_output(cmd, **kw):
    '''
    Runs `cmd`, returning (returncode, stdout, stderr). Does not raise an error
    on command failure.
    '''
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
        raise RuntimeError(f"Unable to parse dkpg-query result: {output!r}.")

## resource base classes ######################################################

class ResourceMeta(type):
    """Metaclass to validate Resource subclasses."""
    def __new__(mcs, name, bases, attrs):
        # Skip validation for the baseclasses.
        if name == 'Resource' or name == "SimpleResource":
            return super().__new__(mcs, name, bases, attrs)

        # Check that subclasses of Resource (but not SimpleResource) have 'exists' kwarg for __init__.
        if Resource in bases and SimpleResource not in bases:
            init = attrs.get('__init__')
            if init:
                import inspect
                sig = inspect.signature(init)
                if 'exists' not in sig.parameters:
                    raise TypeError(f"Class {name}.__init__ must be defined with 'exists' kwarg.")

        return super().__new__(mcs, name, bases, attrs)

class Resource(metaclass=ResourceMeta):
    def apply(self):
        self.before_apply()
        if self.exists:
            self.create()
        else:
            self.destroy()

    def before_apply(self):
        '''Used to gather state that is needed in both create & destroy.'''
        pass

    def create(self):
        '''Create/enable/etc. the resource when exists=True.'''
        raise NotImplementedError()

    def destroy(self):
        '''Destroy/remove/disable/etc. the resource when exists=False.'''
        raise NotImplementedError()

class SimpleResource(Resource):
    def apply(self):
        raise NotImplementedError()

    def destroy(self):
        raise RuntimeError("SimpleResources cannot be destroyed.")

## resources ##################################################################

class Directory(Resource):
    def __init__(self, path, exists=True):
        self.path = Path(path).expanduser()
        self.exists = exists

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
    def __init__(self, path, src=None, contents=None, exists=True):
        if not src and not contents:
            contents = ""

        self.path = Path(path).expanduser()
        self.src = Path(src).expanduser() if src else None
        self.contents = contents
        self.exists = exists

    def apply(self):
        if self.exists:
            self.create()
        else:
            self.destroy()

    def create(self):
        if self.src:
            if file_needs_update(self.src, self.path):
                shutil.copy(self.src, self.path)
            else:
                print(f"Skipped copying of {self.src!r} to {self.path!r} because files match.")
        else:
            is_bytes = isinstance(self.contents, bytes)
            needs_update = True
            if self.path.exists():
                needs_update = checksum(self.path) != hashlib.sha256(self.contents if is_bytes else self.contents.encode()).hexdigest()
            if needs_update:
                with open(self.path, "wb" if is_bytes else "w") as f:
                    f.write(self.contents)
            else:
                print(f"Skipped writing of {self.path!r} because checksum matches contents.")

    def destroy(self):
        if self.path.is_file():
            os.unlink(self.path)
        else:
            print(f"Skipping removal of {self.path!r} beacause it does not exist.")


class User(Resource):
    def __init__(self, username, group=None, groups=None, shell=None, home=None, system=False, exists=True):
        self.username = username
        self.primary_group = group
        self.wanted_groups = groups or []
        self.shell = shell
        self.home = home
        self.system = system
        self.exists = exists

    def is_present(self):
        (status, out, err) = shell_output(["id", self.username])
        return status == 0 and "uid=" in out

    def before_apply(self):
        self.user_already_exists = self.is_present()

    def get_current_groups(self):
        """Get list of groups user belongs to."""
        (status, out, err) = shell_output(["groups", self.username])
        if status == 0:
            return out.split(":")[1].strip().split()
        else:
            raise RuntimeError(f"Got unexpected response from `groups`: {(status, out, err)!r}.")

    def create(self):
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
        if self.user_already_exists:
            shell(["sudo", "userdel", "-r", self.username])
        else:
            print(f"Skipping user deletion for {self.username!r} because user doesn't exist.")


class Group(Resource):
    def __init__(self, groupname, system=False, exists=True):
        self.groupname = groupname
        self.system = system
        self.exists = exists

    def is_present(self):
        (status, out, err) = shell_output(["getent", "group", self.groupname])
        return status == 0 and self.groupname in out

    def before_apply(self):
        self.group_already_exists = self.is_present()

    def create(self):
        if self.group_already_exists:
            print(f"Skipping group creation for {self.groupname!r} because group already exists.")
            return
        cmd = ["sudo", "groupadd"]
        if self.system:
            cmd.append("--system")
        cmd.append(self.groupname)
        shell(cmd)

    def destroy(self):
        if self.group_already_exists:
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
    def __init__(self, name, provides=None, exists=True):
        self.name = name
        self.provides = Path(provides).expanduser() if provides else None
        self.exists = exists

    def before_apply(self):
        self.package_already_installed = self.is_installed()

    def is_installed(self):
        return apt_pkg_is_installed(self.name)

    def create(self):
        needs_install = False
        if self.provides and not self.provides.is_file():
            needs_install = True
        elif not self.package_already_installed:
            needs_install = True

        if needs_install:
            apt_pkg_install(self.name)
        else:
            print(f"Skipping package install of {self.name} because it's already installed.")

    def destroy(self):
        if self.package_already_installed:
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

    # TODO: make a method for this?
    # run([pip_path, "install", "-r", os.path.join(repo_path, "requirements.txt")])


class Command(SimpleResource):
    def __init__(self, *args, provides=None):
        self.args = list(args)
        self.provides = Path(provides).expanduser() if provides else None

    def apply(self):
        if self.provides and self.provides.exists():
            print(f"Skipping command because {self.provides} already exists.")
            return
        shell(self.args)

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


def ambix():
    steps = [
        # base
        AptPackage("net-tools"),

        # caddy install
        Download("https://github.com/caddyserver/caddy/releases/download/v2.9.1/caddy_2.9.1_linux_amd64.tar.gz", "/root/caddy.tgz"),
        Directory("/root/caddy-install"),
        Command("tar", "-zxvf", "/root/caddy.tgz", "-C", "/root/caddy-install/", provides="/root/caddy-install/caddy"),
        Directory("/opt/caddy/bin/"),
        Command("cp", "/root/caddy-install/caddy", "/opt/caddy/bin/", provides="/opt/caddy/bin/caddy"),

        # Caddyfile
        File("/opt/caddy/ambix.caddy", contents=CADDYFILE_AMBIX),

        # caddy user, group, and service
        Group("caddy", system=True),
        User("caddy", group='caddy', groups=['caddy'], system=True, home="/opt/caddy/", shell="/usr/sbin/nologin"),
        Command("setcap", "cap_net_bind_service=+ep", "/opt/caddy/bin/caddy"),
        File("/etc/systemd/system/caddy.service", contents=SYSTEMD_CADDY),
        Command("systemctl", "daemon-reload"),
        Command("systemctl", "enable", "--now", "caddy"),
    ]
    for step in steps:
        step.run()

def wool_apply(task_name):
    tasks = {
        "demo": demo,
        "ltorg_stage_1": ltorg_stage_1,
        "ambix": ambix,
    }
    task_func = tasks[task_name]
    return task_func()


def wool_push(remote, task_name):
    # Remote python version check
    (_, output, _) = shell_output(["ssh", "-A", remote, "python3 -c 'import platform; print(tuple(map(int, platform.python_version_tuple())) >= (3, 6, 0))'"])
    assert output.strip() == "True", "Invalid remote python version. Need >=3.6.0."

    # Push and run
    shell(["scp", __file__, "{}:/tmp/wool_push.py".format(remote)])
    shell(["ssh", "-A", remote, f"python3 -u /tmp/wool_push.py --apply --task={task_name}"])


def main():
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
        wool_apply(args.task)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
