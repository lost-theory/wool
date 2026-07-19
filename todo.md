# Todo list / ideas for improvement

* [TODO] Command capture mode: Right now Command() calls shell(), which throws an error on failure. There should be an arg on Command() that allows for the capture of exit code, stdout, and stderr. Or make it a separate resource. This would let you easily write logic in task code based on the results of the command.
* [TODO] run_task_as_user is not enough for coordinating user vs. root multi-step tasks:
  * I ran into this when trying to think of a way to allow the "app" user task for one of my applications to restart the service (which needs to happen as root).
  * `run_task_as_user(__file__, "rms2_app", user="rms")` just calls tasks.py again as a shell command. There's no way to pass data in or get data back out.
  * You could go "out of band" and store state on disk, but this seems ugly and error prone (e.g. leaving around stale state).
  * You could also write JSON as stdin to the command and read JSON back from stdout.
  * This also makes me think of the "signal", "trigger", "notify" etc. features from other CM tools.
* [TODO] In general the `__file__` and stringly typed task names need to be redesigned. Top level tasks could be decorated with e.g. `@task` and collected automatically for `wool_main`. `run_task_as_user` should ideally take a callable instead of a string. The `__file__` arg could be determined automatically via `inspect`, I think. Taking callables creates some new problems. E.g. what if you pass a lambda, or a class with `__call__`, etc. I don't think there's a way to get those to work. Passing functions from other modules also creates problems. But if we could enforce "normal" named functions that exist at module import time, the API cleanup would probably be worth it.
* [TODO] Improve the `steps = [...]` pattern: In pretty much all of the tasks I've written so far, I instantiate a list of resources in a "declarative" style (no execution yet) then use `for step in steps: step.apply()` to actually execute them. You could wrap the list in a higher level ResourceGroup() resource or similar, then call `resources.apply()`. You could then loop over the resources before or after `apply` to do stuff. Like maybe for this previous run_task_as_user coordination issue, I could have one or more of the "app" sub-resources emit a "app server service needs restart" event/signal/whatever, then have that conditionally run some other resource to do the restart as root.
* [TODO] Virtualenv resource should run `python -mensurepip --upgrade` if `bin/pip` doesn't exist.
* [TODO] Wool via `wool_main` should have some built-in way to accept args. I think it'd work to expose the ArgumentParser and let the user add their own arguments.
* [TODO] Diff mode.
* [TODO] New `AptPackages` resource (or let AptPackage take a list) for speeding up install of many packages at once.

# Systemd resource ideas

Potential API:

    SystemdUnitFile(
        "rms2.service",
        unit={
            "Unit": {
                "Description": "RMS2 web app",
                "After": ["network-online.target"],
            },
            "Service": {
                "User": "rms2",
                "Group": "rms2",
                "ExecStart": "...",
            }
        },
    )
    SystemdService("rms2.service", enabled=True, running=True)

Once you have something that can turn the `unit` python dict into the INI-like syntax, you can just use normal python techniques to flexibly build that dict (e.g. shared constant, helper functions, subclass of SystemdUnitFile that already has a base unit defined, etc.).

The SystemdUnitFile resource could use `systemd-analyze verify /etc/systemd/system/foo.service` to validate the configs.

This kinda needs the "trigger/event/notify" type of functionality in one of the todos above (unit definition changed -> `systemctl daemon-reload` and restart the service).

# SwapFile resource ideas

    class SwapFile(Resource):
        def __init__(self, path="/swapfile", size="1G", ensures="present"):
            self.path = Path(path)
            self.size = size
            self.ensures = ensures

        @property
        def active(self) -> bool:
            with open("/proc/swaps") as f:
                return str(self.path) in f.read()

        def create(self):
            if not self.path.exists():
                Command(["fallocate", "-l", self.size, self.path]).apply()
                Command(["chmod", "600", self.path]).apply()
                Command(["mkswap", self.path]).apply()

            if not self.active:
                Command(["swapon", self.path]).apply()

            BlockInFile(
                path="/etc/fstab",
                name=f"wool_swap",
                start_marker="# {start}",
                end_marker="# {end}",
                contents=f"{self.path} none swap sw 0 0",
            ).apply()

        def destroy(self):
            if swap_is_active(self.path):
                Command(["swapoff", self.path]).apply()

            BlockInFile(
                path="/etc/fstab",
                name=f"wool_swap",
                start_marker="# {start}",
                end_marker="# {end}",
                contents=f"{self.path} none swap sw 0 0",
                ensures="absent",
            ).apply()

            if self.path.exists():
                self.path.unlink()
