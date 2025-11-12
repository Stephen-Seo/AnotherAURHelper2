#!/usr/bin/env python3

import argparse
import datetime
import getpass
import pathlib
import re
import subprocess
import sys
import tarfile
import time
import tomllib
import typing

AUR_GIT_REPO_PATH = "https://aur.archlinux.org"
AUR_GIT_REPO_PATH_TEMPLATE = AUR_GIT_REPO_PATH + "/{}.git"
STRFTIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S%:z"
GLOBAL_TOML_D = None
GLOBAL_SHARED_STATE = None
KNOWN_ARCHITECTURES = ("x86_64", "aarch64", "any")
EPOCH_RE = re.compile("^([0-9]+):(.+)$")
IS_DIGIT_REGEX = re.compile("^[0-9]+$")


class ArchPkgVersion:
    """Holds a version (typically of an ArchLinux package) for comparison."""

    def __init__(self, version_str: str):
        self.versions = []
        self.pkgver = 0
        self.epoch = 0
        epoch_match = EPOCH_RE.match(version_str)
        if not epoch_match is None:
            self.epoch = int(epoch_match.group(1))
            version_str = epoch_match.group(2)
        end_dash_idx = version_str.rfind("-")
        if end_dash_idx != -1 and end_dash_idx + 1 < len(version_str):
            try:
                self.pkgver = int(version_str[end_dash_idx + 1 :])
            except ValueError:
                self.pkgver = version_str[end_dash_idx + 1 :]
            version_str = version_str[:end_dash_idx]

        for sub in version_str.split("."):
            if IS_DIGIT_REGEX.match(sub) is not None:
                self.versions.append(int(sub))
            else:
                subversion = []
                string = None
                integer = None
                for char in sub:
                    if IS_DIGIT_REGEX.match(char) is not None:
                        if string is not None:
                            subversion.append(string)
                            string = None
                        if integer is None:
                            integer = int(char)
                        else:
                            integer = integer * 10 + int(char)
                    else:
                        if integer is not None:
                            subversion.append(integer)
                            integer = None
                        if string is None:
                            string = char
                        else:
                            string = string + char
                if string is not None:
                    subversion.append(string)
                    string = None
                if integer is not None:
                    subversion.append(integer)
                    integer = None
                self.versions.append(tuple(subversion))
        self.versions = tuple(self.versions)

    def compare_with(self, other_self: "ArchPkgVersion"):
        """Returns -1 if self is less than other_self, 0 if they are equal, and
        1 if self is greater than other_self."""
        if self.epoch < other_self.epoch:
            return -1
        elif self.epoch > other_self.epoch:
            return 1
        self_count = len(self.versions)
        other_count = len(other_self.versions)
        if other_count < self_count:
            count = other_count
        else:
            count = self_count
        for i in range(count):
            if type(self.versions[i]) is tuple:
                if type(other_self.versions[i]) is tuple:
                    self_subcount = len(self.versions[i])
                    other_subcount = len(other_self.versions[i])
                    if other_subcount < self_subcount:
                        subcount = other_subcount
                    else:
                        subcount = self_subcount
                    for j in range(subcount):
                        try:
                            if self.versions[i][j] < other_self.versions[i][j]:
                                return -1
                            elif (
                                self.versions[i][j] > other_self.versions[i][j]
                            ):
                                return 1
                        except TypeError:
                            if str(self.versions[i][j]) < str(
                                other_self.versions[i][j]
                            ):
                                return -1
                            elif str(self.versions[i][j]) > str(
                                other_self.versions[i][j]
                            ):
                                return 1
                    if self_subcount < other_subcount:
                        return -1
                    elif self_subcount > other_subcount:
                        return 1
                else:
                    # self is tuple but other is not
                    return 1
            elif type(other_self.versions[i]) is tuple:
                # other is tuple but self is not
                return -1
            else:
                try:
                    if self.versions[i] < other_self.versions[i]:
                        return -1
                    elif self.versions[i] > other_self.versions[i]:
                        return 1
                except TypeError:
                    if str(self.versions[i]) < str(other_self.versions[i]):
                        return -1
                    elif str(self.versions[i]) > str(other_self.versions[i]):
                        return 1
        if self_count < other_count:
            return -1
        elif self_count > other_count:
            return 1
        else:
            try:
                if self.pkgver < other_self.pkgver:
                    return -1
                elif self.pkgver > other_self.pkgver:
                    return 1
                else:
                    return 0
            except TypeError:
                if str(self.pkgver) < str(other_self.pkgver):
                    return -1
                elif str(self.pkgver) > str(other_self.pkgver):
                    return 1
                else:
                    return 0

    def __eq__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) == 0
        else:
            return False

    def __ne__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) != 0
        else:
            return False

    def __lt__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) < 0
        else:
            return False

    def __le__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) <= 0
        else:
            return False

    def __gt__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) > 0
        else:
            return False

    def __ge__(self, other: typing.Any):
        if isinstance(other, ArchPkgVersion):
            return self.compare_with(other) >= 0
        else:
            return False

    def __str__(self):
        self_str = ""
        if self.epoch != 0:
            self_str = f"{self.epoch}:"
        for idx in range(len(self.versions)):
            if type(self.versions[idx]) is tuple:
                for sub in self.versions[idx]:
                    self_str += str(sub)
            else:
                self_str += str(self.versions[idx])
            if idx + 1 < len(self.versions):
                self_str += "."
        self_str += "-" + str(self.pkgver)
        return self_str


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
        if start_container(shared_state) != 0:
            return 1
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
                f"cd {name} && makepkg -c -s --nobuild --noconfirm",
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


def start_container(shared_state: dict) -> int:
    """Returns 0 on success."""
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
    except:
        log_print("ERROR: Failed to start container!")
        return 1
    return 0


def rsync_package_to_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    try:
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
    except:
        log_print("ERROR: Failed to rsync to container!")
        return 1
    return 0


def rsync_package_from_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--exclude=src*",
                "--exclude=pkg*",
                f"{user}@{c_addr}:{name}/",
                f"{clone_dir}/",
            ),
            check=True,
            text=True,
        )
    except:
        log_print("ERROR: Failed to rsync from container!")
        return 1
    return 0


def build_pkg(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    try:
        container = shared_state["toml"]["container_name"]
        if start_container(shared_state) != 0:
            return 1
        user = shared_state["toml"]["container_user"]
        c_addr = shared_state["toml"]["container_addr"]

        id_file = shared_state["toml"]["container_identity_file"]

        if rsync_package_to_container(entry, shared_state) != 0:
            return 1

        subprocess.run(
            (
                "/usr/bin/sudo",
                "machinectl",
                "shell",
                f"{user}@{container}",
                "/usr/bin/sh",
                "-c",
                f"cd {name} && makepkg -c -s --noconfirm",
            ),
            check=True,
            text=True,
        )

        if rsync_package_from_container(entry, shared_state) != 0:
            return 1
    except:
        log_print(f"""ERROR: Failed to build "{name}"!""")
        log_print(repr(sys.exception()))
        return 1
    return 0


def get_pkgver(
    entry: dict, shared_state: dict
) -> typing.Optional[ArchPkgVersion]:
    """Gets the latest built version of a package."""
    name = entry["name"]
    pkgs_out_dir = pathlib.PosixPath(shared_state["toml"]["pkgs_out_dir"])
    repo_name = shared_state["toml"]["aur_repo_name"]
    repo_path = pkgs_out_dir / f"{repo_name}.db.tar"
    try:
        with tarfile.open(name=repo_path) as f:
            repo_names = f.getnames()
    except:
        log_print(f'Failed to open "{repo_path}"!')
        return None
    name_regex = re.compile(f"""^{name}-([0-9].*)$""")
    repo_names = list(
        filter(
            lambda p: p.find(name) != -1
            and p.find("/") == -1
            and name_regex.fullmatch(p) is not None,
            repo_names,
        )
    )
    if len(repo_names) == 0:
        log_print(f"{name} not in {repo_name}.db.tar!")
        return None
    elif len(repo_names) > 1:
        log_print(f"Duplicate {name} entries in {repo_name}.db.tar!")
        return None
    match = name_regex.fullmatch(repo_names[0])
    return ArchPkgVersion(match.group(1))


def verify_to_build(entry: dict, shared_state: dict) -> int:
    """Returns 0 if entry should be built, 1 if it shouldn't be built, 2 on error"""
    name = entry["name"]
    saved_pkgver = get_pkgver(entry, shared_state)
    if saved_pkgver is None:
        log_print(f"{name} has not been built; should be built")
        return 0
    start_container(shared_state)
    rsync_package_to_container(entry, shared_state)
    try:
        run_ret = subprocess.run(
            (
                "/usr/bin/sudo",
                "machinectl",
                "shell",
                f"{user}@{container}",
                "/usr/bin/bash",
                "-c",
                f"cd {name} && makepkg -c -s --noconfirm 1>&2 && source PKGBUILD >&/dev/null && echo ${{epoch:-0}}:${{pkgver}}-${{pkgrel}}",
            ),
            check=True,
            text=True,
            capture_output=True,
        )
        PKGBUILD_ver = ArchPkgVersion(run_ret.stdout.strip())
    except:
        log_print("ERROR: Failed to verify if entry should be built!")
        return 2
    if PKGBUILD_ver > saved_pkgver:
        return 0
    else:
        return 1


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
        if args.pkg is not None and entry["name"] not in args.pkg:
            idx += 1
            shared_state["skipped"].add(entry["name"])
            continue
        log_print(f"Checking {entry['name']}...")
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
        verif_ret = verify_to_build(entry, shared_state)
        if verif_ret == 0:
            shared_state["confirmed"].add(entry["name"])
        elif verif_ret == 1:
            shared_state["skipped"].add(entry["name"])
        else:
            log_print(
                f"ERROR: Failed to verify version of pkg \"{entry['name']}\"."
            )
            user_result = user_interact_alpha(
                "Skip or Abort?",
                ["Skip", "Abort", "Retry"],
                False,
                shared_state,
            )
            if user_result == "Retry":
                continue
            elif user_result == "Abort":
                return
            shared_state["skipped"].add(entry["name"])
        idx += 1

    # Multiple passes through all packages.
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
    log_print("Building packages...")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["confirmed"]:
            build_ret = build_pkg(entry, shared_state)
            if build_ret != 0:
                log_print(f"WARNING: Failed to build \"{entry['name']}\"!")


if __name__ == "__main__":
    main()
