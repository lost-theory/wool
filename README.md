# Wool

Pure Python configuration management system.

Wool is a simple pure Python configuration management tool similar to Chef, Puppet, or Ansible with zero dependencies.

## Why Wool?

Wool was created in response to pain points I've experienced with other CM tools:

1. **Pure Python** - No DSLs, YAML, or Jinja2 templates.
2. **Zero dependencies** - Copy `wool.py` to any machine with Python 3.10+ and run your tasks. Or vendor it into your project. Think of it as `bottle.py` for configuration management.
3. **Library over framework** - No agents, servers, daemons, special runner API/CLI, no data stores, etc. Just import resources into your code and use them.
4. **Debuggable** - Use `pdb` and standard Python debugging techniques.
5. **Simple execution model** - Resources are declarative, idempotent, composable, and extensible using normal python techniques.
6. **Easy deployment** - Push and run via SSH, no other setup steps required.

## Features

Resource types:

- **Directory** - Create/remove directories (recursively)
- **File** - Manage file contents (from source file or inline content)
- **User** - Create/remove users with groups and shell configuration
- **Group** - Create/remove system groups
- **AptPackage** - Install/remove Debian/Ubuntu packages
- **Download** - Download files from URLs
- **Virtualenv** - Create Python virtual environments
- **Command** - Execute shell commands with an optional idempotency check
- **Owner** - Set file/directory ownership (user and group)
- **Perms** - Set file/directory permissions
- **Symlink** - Create/remove symbolic links
- **BlockInFile** - Manage configuration blocks within files
- **Hostkey** - Manage SSH host keys in known_hosts files
- **Touch** - Create empty files

Deployment options:

- You can import wool resources, use them from your existing code, then run your code. No "deployment" is needed beyond that.
- If you want more CLI scaffolding, you can call the optional `wool_main` function from your code to give your task script a few helpful args:

  - **Local execution**: `--apply --task foo` will run a task directly on your local machine.
  - **Remote push**: `--push user@remote --task foo` will sync wool.py and your task file to a remote host via SSH, then execute the given task.
  - **Remote project sync and push**: `--project . --push user@remote --task foo` is the same as remote push, but it also rsyncs up a "project" directory (e.g. additional submodules with other tasks/resources, config files, static files, templates, etc.) to the remote host before task execution.

Structured logging:

- All logging output from Resources is structured data for visibility into what actions are being taken and why they're being skipped or executed.

## Usage

No installation required. Just copy `wool.py` to your project or destination machine, import it, and write your tasks. Or, if you're using the optional `wool_main` function to execute your tasks, you can use the `--push` option described above.

To use wool, create a Python script that defines your resources and applies them:

```python
from wool import *

def webserver():
    AptPackage("nginx").apply()
    Directory("/var/www/mysite").apply()
    File("/etc/nginx/sites-available/mysite", src="./nginx-site.conf").apply()
    Perms("/var/www/mysite", "755", recursive=True).apply()
    Owner("/var/www/mysite", user="www-data", group="www-data", recursive=True).apply()

def create_user():
    User("app", home="/home/app", shell="/bin/bash", groups=["www-data"]).apply()

if __name__ == "__main__":
    tasks = {
        "webserver": webserver,
        "create_user": create_user,
    }
    wool_main(__file__, tasks)
```

Local execution:

```bash
# Run a specific task locally
python tasks.py --apply --task create_user
python tasks.py --apply --task webserver
```

Remote execution:

```bash
# Sync tasks.py and wool.py to remote host and run the "webserver" task:
python tasks.py --push user@remote --task webserver

# Or, sync an entire project directory (with config files, etc.) and run a task:
python tasks.py --project . --push user@remote --task webserver
```

## How do I...

Write conditional logic? Use normal python conditions:

```python
if Path("/etc/ssl/certs/mysite.crt").exists():
    File("/etc/nginx/sites-available/mysite-ssl", src="./nginx-ssl.conf").apply()
```

Handle errors? Use normal python exception handling:

```python
try:
    Command(["my-special-command", "foo"]).apply()
except FileNotFoundError:
    print("Warning: my-special-command not found, installing...")
    AptPackage("my-special-package").apply()
```

Run a task as a different user? Run your script as that user, or use the provided `run_task_as_user` function:

```python
def system_setup():
    # add resources for root user here...
    # then run another task as "myuser"
    run_task_as_user(__file__, "user_setup", "myuser")

def user_setup():
    # add resources for "myuser" here...
    File("~/.bashrc", src="./user-bashrc").apply()
```

Compose multiple tasks together into a "recipe" or "role"? Compose functions together like any normal python program:

```python
def system_setup():
    # "base" level setup as root goes here
    ...

def nginx_setup():
    # set up nginx here
    ...

def db_setup():
    # set up your database here
    ...

def app_setup():
    # set up your application here
    ...

def app_deploy():
    # deploy the latest version of your application and reload
    ...

def setup():
    system_setup()
    nginx_setup()
    db_setup()
    app_setup()
    app_deploy()
```

Write my own "resources"? Subclass `Resource` or `SimpleResource` alongside your tasks and use normal python code:

```python
class JsonFile(Resource):
    def __init__(self, path: Path, data: Optional[Dict[Any, Any]] = None, ensures: str = "present") -> None:
        self.path = path
        self.data = data
        self.ensures = ensures

    def create(self) -> None:
        with self.path.open("w") as f:
            json.dump(self.data, f)

    def destroy(self) -> None:
        if self.path.is_file():
            os.unlink(self.path)

# Usage:
# >>> task1 = JsonFile(Path("foo.json"), data={"hello": "world"})
# >>> task2 = JsonFile(Path("foo.json"), ensures="absent")
# >>> task1.apply()
# >>> Path("foo.json").is_file()
# True
# >>> Path("foo.json").read_text()
# '{"hello": "world"}'
# >>> task2.apply()
# >>> Path("foo.json").is_file()
# False
```

Gather "facts" about the system? Use normal python code:

```python
num_cpus = os.cpu_count() or 1
File("app.cfg", contents=f"num_procs={num_cpus}").apply()
```

Use templates to manage file contents? Use your favorite templating language (you'll have to install it yourself on the remote machine, e.g. in a venv), or you can use python's built-in string formatting:

```python
def template_nginx_vhost(hostname, www_path, log_path):
    return f"""
        server {{
            listen 80;
            server_name {hostname};

            root {www_path};
            index index.html index.htm;

            access_log {log_path}/access.log;
            error_log  {log_path}/error.log;

            location / {{
                try_files $uri $uri/ =404;
            }}
        }}
    """

def nginx_setup():
    ...
    File("/etc/nginx/sites-enabled/app", contents=template_nginx_vhost("app.example.com", "/var/www/app", "/var/log/app/")).apply()
    File("/etc/nginx/sites-enabled/docs", contents=template_nginx_vhost("docs.example.com", "/var/www/docs", "/var/log/docs/")).apply()
    File("/etc/nginx/sites-enabled/admin", contents=template_nginx_vhost("admin.example.com", "/var/www/admin", "/var/log/admin/")).apply()
    ...
```

Debug my tasks? Use your favorite python debugging technique: print logging, wool's structured logging, run under pdb via `python -mpdb tasks.py --apply --task foo` (for local execution), or you can drop `breakpoint()` / `pdb.set_trace()` into your task code (even remotely).

Run across multiple machines in parallel? Use your favorite distributed execution tool like parallel-ssh, paramiko, or execnet to wrap the script that runs your tasks, then gather the results.

Manage secrets? Store them in your wool project (or wherever) as encrypted text (e.g. via git-crypt, gpg, age, etc.), load the decrypted values into memory inside your tasks, and then use them.

## License

Wool is free software under the MIT license.
