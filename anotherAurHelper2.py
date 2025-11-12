#!/usr/bin/env python3

import argparse
import datetime
import getpass
import pathlib
import subprocess
import sys
import time
import tomllib

AUR_GIT_REPO_PATH = "https://aur.archlinux.org"
AUR_GIT_REPO_PATH_TEMPLATE = AUR_GIT_REPO_PATH + "/{}.git"
STRFTIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S%:z"
GLOBAL_TOML_D = None
GLOBAL_SHARED_STATE = None


def log_print(*args, **kwargs):
    """Prints to stdout and logs the same to a log file."""
    global GLOBAL_TOML_D
    if "toml" in kwargs:
        if "tz_force_offset_hours" in kwargs["toml"]:
            offset_hours = kwargs["toml"]["tz_force_offset_hours"]
            offset_minutes = 0
            if "tz_force_offset_minutes" in kwargs["toml"]:
                offset_minutes = kwargs["toml"]["tz_force_offset_minutes"]
            tz = datetime.timezone(
                datetime.timedelta(hours=offset_hours, minutes=offset_minutes)
            )
            lt = datetime.datetime.now(tz)
            time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        else:
            lt = datetime.datetime.now().astimezone()
            time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        if "log_file" in kwargs["toml"]:
            log_file = kwargs["toml"]["log_file"]
        else:
            log_file = "anotherAurHelper2.log"
    elif GLOBAL_TOML_D is not None:
        if "tz_force_offset_hours" in GLOBAL_TOML_D:
            offset_hours = GLOBAL_TOML_D["tz_force_offset_hours"]
            offset_minutes = 0
            if "tz_force_offset_minutes" in GLOBAL_TOML_D:
                offset_minutes = GLOBAL_TOML_D["tz_force_offset_minutes"]
            tz = datetime.timezone(
                datetime.timedelta(hours=offset_hours, minutes=offset_minutes)
            )
            lt = datetime.datetime.now(tz)
            time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        else:
            lt = datetime.datetime.now().astimezone()
            time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        if "log_file" in GLOBAL_TOML_D:
            log_file = GLOBAL_TOML_D["log_file"]
        else:
            log_file = "anotherAurHelper2.log"
    else:
        lt = datetime.datetime.now().astimezone()
        time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        log_file = "anotherAurHelper2.log"

    if "no_time" not in kwargs or not kwargs["no_time"]:
        print(time_str, end=" ")
        with open(log_file, "a", encoding="utf-8") as lf:
            print(time_str, end=" ", file=lf)

    if "toml" in kwargs:
        del kwargs["toml"]
    if "no_time" in kwargs:
        del kwargs["no_time"]

    print(*args, **kwargs)
    with open(log_file, "a", encoding="utf-8") as lf:
        kwargs["file"] = lf
        print(*args, **kwargs)


def user_interact(prompt: str, opts: list[str], shared_state: dict) -> str:
    """Returns the name of the chosen option."""
    while True:
        for idx in range(len(opts)):
            log_print(f"{idx + 1}: {opts[idx]}", toml=shared_state["toml"])
        user_input = input(f"{prompt} Pick the number > ")
        try:
            user_input = int(user_input) - 1
        except:
            continue
        if user_input >= 0 and user_input < len(opts):
            return opts[user_input]


def user_interact_alpha(
    prompt: str, opts: list[str], is_first_default: bool, shared_state: dict
) -> str:
    """Returns the name of the chosen option. Returns "interrupt" if Ctrl-C."""
    while True:
        for idx in range(len(opts)):
            log_print(
                f"{opts[idx][0]}: {opts[idx]}",
                toml=shared_state["toml"],
                end=" ",
            )
            if is_first_default and idx == 0:
                log_print("(default)", no_time=True, toml=shared_state["toml"])
            else:
                log_print("", no_time=True, toml=shared_state["toml"])
        try:
            user_input = input(f"{prompt} Pick the letter > ")
            if len(user_input) == 0 and is_first_default:
                return opts[0]
            for idx in range(len(opts)):
                if opts[idx][0].casefold() == user_input[0].casefold():
                    return opts[idx]
        except KeyboardInterrupt:
            return "interrupt"
        except:
            continue


def check_clone_package(entry: dict, shared_state: dict) -> int:
    """Returns 0 if an error ocurred, 1 if the repo was cloned, and 2 if the repo already existed."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    if not clones_dir.is_dir():
        log_print(
            'ERROR: "{}" is not a directory!'.format(
                shared_state["toml"]["clones_dir"]
            )
        )
        return 0
    clone_dir = clones_dir / name
    if not clone_dir.is_dir():
        if clone_dir.is_file():
            log_print(
                'ERROR: "{}" is a file!'.format(
                    shared_state["toml"]["clone_dir"]
                )
            )
            return 0
        if "repo_path" in entry:
            if "repo_branch" in entry:
                try:
                    run_result = subprocess.run(
                        (
                            "/usr/bin/git",
                            "clone",
                            "--single-branch",
                            "-b",
                            entry["repo_branch"],
                            entry["repo_path"],
                            name,
                        ),
                        check=True,
                        cwd=clones_dir,
                    )
                except:
                    log_print(
                        f'ERROR: Failed to clone "{name}" at path "{entry['repo_path']}"!'
                    )
                    log_print(repr(sys.exception()))
                    return 0
            else:
                try:
                    run_result = subprocess.run(
                        ("/usr/bin/git", "clone", entry["repo_path"], name),
                        check=True,
                        cwd=clones_dir,
                    )
                except:
                    log_print(
                        f'ERROR: Failed to clone "{name}" at path "{entry['repo_path']}"!'
                    )
                    log_print(repr(sys.exception()))
                    return 0
        else:
            clone_url = AUR_GIT_REPO_PATH_TEMPLATE.format(name)
            try:
                run_result = subprocess.run(
                    ("/usr/bin/git", "clone", clone_url, name),
                    check=True,
                    cwd=clones_dir,
                )
            except:
                log_print(f'ERROR: Failed to clone "{name}"!')
                log_print(repr(sys.exception()))
                return 0
        return 1
    else:
        try:
            run_result = subprocess.run(
                ("/usr/bin/git", "restore", "."),
                check=True,
                cwd=clone_dir,
            )
            run_result = subprocess.run(
                ("/usr/bin/git", "pull"),
                check=True,
                cwd=clone_dir,
            )
        except:
            log_print(f'ERROR: Failed to update "{name}"!')
            log_print(repr(sys.exception()))
            return 0
        return 2


def check_PKGBUILD(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error, 2 if user does not accept PKGBUILD, 3 on interrupt."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    try:
        subprocess.run(
            ("/usr/bin/env", shared_state["toml"]["editor"], "PKGBUILD"),
            check=True,
            cwd=clone_dir,
        )
    except:
        log_print(f"""ERROR: Failed to check "{name}"'s PKGBUILD!""")
        return 1
    check = user_interact_alpha(
        "Is PKGBUILD OK?", ["OK", "Not OK"], True, shared_state
    )
    if check == "interrupt":
        return 3
    elif check != "OK":
        return 2
    return 0


def run_prepare_only(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    try:
        container = shared_state["toml"]["container_name"]
        subprocess.run(
            ("/usr/bin/sudo", "--stdin", "machinectl", "poweroff", container),
            check=False,
            input=shared_state["pass"].encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        subprocess.run(
            ("/usr/bin/sudo", "machinectl", "start", container), check=True
        )
        time.sleep(1)
        user = shared_state["toml"]["container_user"]
        c_addr = shared_state["toml"]["container_addr"]

        id_file = shared_state["toml"]["container_identity_file"]
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--exclude=.git*",
                f"{clone_dir}/",
                f"{user}@{c_addr}:{name}/",
            ),
            check=True,
            text=True,
        )

        time.sleep(0.3)
        subprocess.run(
            (
                "/usr/bin/sudo",
                "machinectl",
                "shell",
                f"{user}@{container}",
                "/usr/bin/sh",
                "-c",
                f"cd {name} && makepkg -c -s --nobuild",
            ),
            check=True,
            text=True,
        )

        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--exclude=src*",
                f"{user}@{c_addr}:{name}/",
                f"{clone_dir}/",
            ),
            check=True,
            text=True,
        )

        subprocess.run(
            ("/usr/bin/sudo", "machinectl", "poweroff", container), check=True
        )
    except:
        log_print(f"""ERROR: Failed to run "prepare" on "{name}"'s PKGBUILD!""")
        log_print(repr(sys.exception()))
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="AnotherAURHelper2",
        description="Builds AUR pkgs in a chroot handled by systemd-nspawn",
    )
    parser.add_argument("-c", "--config")
    parser.add_argument("-p", "--pkg", action="append")
    args = parser.parse_args()

    if args.config is None:
        log_print("ERROR: config not specified!")
        parser.print_usage()
        return

    toml_d = None
    with open(args.config, "rb") as f:
        toml_d = tomllib.load(f)
    if toml_d is None:
        log_print("ERROR: Failed to parse toml config file!")
        parser.print_usage()
        return
    shared_state = dict()
    shared_state["toml"] = toml_d
    global GLOBAL_TOML_D
    GLOBAL_TOML_D = toml_d
    global GLOBAL_SHARED_STATE
    GLOBAL_SHARED_STATE = shared_state

    log_print(
        "AnotherAURHelper2 will prompt for your password for sudo auth on this host machine.",
        toml=toml_d,
    )
    # user_result = user_interact("Continue?", ["yes", "no"], shared_state)
    user_result = user_interact_alpha(
        "Continue?", ["continue", "quit"], True, shared_state
    )

    if user_result != "continue":
        return

    shared_state["pass"] = getpass.getpass(prompt="sudo password: ")
    try:
        subprocess.run(("/usr/bin/sudo", "-k"), check=True)
        subprocess.run(
            ("/usr/bin/sudo", "-v", "--stdin"),
            input=shared_state["pass"].encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except:
        log_print("ERROR: Failed to auth with sudo!", toml=toml_d)
        log_print(repr(sys.exception()), toml=toml_d)
        return

    try:
        subprocess.run(
            ("/usr/bin/sudo", "echo", "test echo with sudo"), check=True
        )
        subprocess.run(("/usr/bin/sudo", "-k"), check=True)
    except:
        log_print("ERROR: Failed to check sudo auth!", toml=toml_d)
        log_print(repr(sys.exception()), toml=toml_d)
        return

    log_print("Preload ssh key into ssh-agent with ssh-add?")
    user_result = user_interact_alpha(
        "Continue?", ["Add to ssh-agent", "Skip"], True, shared_state
    )

    if user_result == "interrupt":
        return
    elif user_result == "Add to ssh-agent":
        try:
            subprocess.run(
                (
                    "/usr/bin/ssh-add",
                    "-t",
                    "8h",
                    toml_d["container_identity_file"],
                ),
                check=True,
                text=True,
            )
        except:
            log_print("ERROR: Failed to add key to ssh-agent!")
            log_print(repr(sys.exception()))
            return

    log_print("Begin checking each package...")
    shared_state["skipped"] = set()
    shared_state["confirmed"] = set()
    idx = 0
    while idx < len(toml_d["entry"]):
        entry = toml_d["entry"][idx]
        if len(args.pkg) != 0 and entry["name"] not in args.pkg:
            idx += 1
            shared_state["skipped"].add(entry["name"])
            continue
        if check_clone_package(entry, shared_state) == 0:
            log_print(
                f"""Skipping "{entry['name']}" due to clone/update issue..."""
            )
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        check_ret = check_PKGBUILD(entry, shared_state)
        if check_ret == 3:
            return
        elif check_ret != 0:
            log_print(f"""Skipping "{entry['name']}" due to PKGBUILD...""")
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        if run_prepare_only(entry, shared_state) == 1:
            log_print(
                f"""Skipping "{entry['name']}" due to failure to "prepare"..."""
            )
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        user_result = user_interact_alpha(
            "OK with pkg?", ["OK", "Not OK", "Retry"], True, shared_state
        )
        if user_result == "interrupt":
            return
        elif user_result == "Retry":
            continue
        elif user_result != "OK":
            log_print(
                f"""Skipping "{entry['name']}" due to user not OK with pkg..."""
            )
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        log_print(f"User is OK with \"{entry['name']}\"")
        shared_state["confirmed"].add(entry["name"])
        idx += 1

    # Two passes through all packages.
    # Don't care about efficiency because its not that important in this case.
    log_print("List of skipped:")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["skipped"]:
            log_print(f'  {entry["name"]} Skipped, will not be built')
    log_print("List of not skipped:")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["confirmed"]:
            log_print(f'  {entry["name"]} Will be built (if out of date)')


if __name__ == "__main__":
    main()
