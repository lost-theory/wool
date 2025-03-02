"""
Experimental CM code for consolidation.
"""

import argparse
import os
import subprocess
from string import Template
from subprocess import check_call, check_output

## start nginx code ###########################################################

NGINX_CONFIG = """
server {
  listen 0.0.0.0:80;
  server_name 0.0.0.0 ${ips};

  location / {
    proxy_pass http://app;
  }
}

upstream app {
  server unix:/tmp/gunicorn.sock;
}
"""

# code
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

## end nginx code #############################################################

## start systemd code #########################################################

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
    check_call(cmd, **kw)


def run_with_output(cmd, **kw):
    print("Getting output from: {} with {}".format(cmd, kw))
    output = check_output(cmd, **kw).decode("utf8")
    return output


def checksum(path):
    checksum, _ = check_output(["sha256sum", path]).decode("utf8").strip().split()
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
    def __init__(self, name, provides):
        self.name = name
        self.provides = os.path.expanduser(provides)

    def run(self):
        if os.path.isfile(self.provides):
            print(f"Skipping apt install of {self.name} because {self.provides} exists.")
            return
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


def cm_run():
    steps = [
        AptPackage("nginx", provides="/usr/sbin/nginx"),
        Directory("/tmp/foo/bar"),
        Download("http://lost-theory.org/robots.txt", provides="/tmp/robots.txt"),
        Virtualenv("/usr/bin/python3", "~/testing-env"),
        Command("/bin/bash", "-c", "date | tee -a /tmp/out.log"),
    ]
    for step in steps:
        step.run()


def push(remote):
    # Remote python version check
    version_check = run_with_output(
        [
            "ssh",
            "-A",
            remote,
            "python3 -c 'import platform; print(tuple(map(int, platform.python_version_tuple())) >= (3, 6, 0))'",
        ]
    )
    assert version_check.strip() == "True", "Invalid remote python version. Need >=3.6.0."

    # Push and run
    run(["scp", __file__, "{}:/tmp/cm.py".format(remote)])
    run(["ssh", "-A", remote, "python3 -u /tmp/cm.py --run"])


def main():
    parser = argparse.ArgumentParser(description="Simple pure python config management")
    parser.add_argument(
        "--push",
        type=str,
        metavar="USER@HOST",
        help="Push and run on remote user@host via SSH.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Used internally by --push. Or you can run it yourself on a local machine.",
    )

    args = parser.parse_args()

    if args.push:
        push(args.push)
    elif args.run:
        cm_run()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
