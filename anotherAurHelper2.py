#!/usr/bin/env python3

# ISC License
#
# Copyright (c) 2025 Stephen Seo
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

import atexit
import argparse
import datetime
import getpass
import pathlib
import re
import signal
import subprocess
import sys
import tarfile
import threading
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
IS_PKG_REGEX = re.compile(r"^.*\.pkg\.tar\.([a-z]+)$")


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


def get_datetime_now() -> str:
    """Returns formatted string now."""
    if GLOBAL_TOML_D is not None:
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
    else:
        lt = datetime.datetime.now().astimezone()
        time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
    return time_str


def log_print(*args, **kwargs):
    """Prints to stdout and logs the same to a log file."""
    global GLOBAL_TOML_D
    time_str = get_datetime_now()
    if "toml" in kwargs:
        if "log_file" in kwargs["toml"]:
            log_file = kwargs["toml"]["log_file"]
        else:
            log_file = "anotherAurHelper2.log"
    elif GLOBAL_TOML_D is not None:
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


def thread_handle_output_stream(
    handle,
    output_file,
    shared_state,
    print_to_log=False,
    ignore_output_file=False,
):
    """Reads lines from an input stream "handle" and writes them to
    "output_file". Flags in "shared_state" determine certain behaviors, such as
    prepending a timestamp to each line, or the filesize-limit for the
    "output_file"."""

    log_count = 0
    limit_reached = False
    while True:
        line = handle.readline()
        if len(line) == 0:
            break

        if print_to_log:
            log_print(line.rstrip("\n"))

        if ignore_output_file:
            continue

        if not limit_reached:
            nowstring = get_datetime_now()
            line = nowstring + " " + line
            log_count += len(line)
            if log_count > shared_state["toml"]["log_limit"]:
                limit_reached = True
                if shared_state["toml"]["error_on_limit"]:
                    output_file.write(
                        "\nERROR: Reached log_limit! No longer logging to file!\n"
                    )
                    output_file.flush()
                    log_print(
                        "ERROR: Reached log_limit! No longer logging to file!",
                    )
                    handle.close()
                    break
                else:
                    output_file.write(
                        "\nWARNING: Reached log_limit! No longer logging to file!\n"
                    )
                    output_file.flush()
                    log_print(
                        "WARNING: Reached log_limit! No longer logging to file!",
                    )
            else:
                output_file.write(line)
                output_file.flush()


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
                f"  {opts[idx][0]}: {opts[idx]}",
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
                cwd=clone_dir.as_posix(),
            )
            run_result = subprocess.run(
                ("/usr/bin/git", "pull"),
                check=True,
                cwd=clone_dir.as_posix(),
            )
        except:
            log_print(f'ERROR: Failed to update "{name}"!')
            log_print(repr(sys.exception()))
            return 0
        return 2


def check_PKGBUILD(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error, 2 if user does not accept PKGBUILD, 3 on interrupt, 4 to force build."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    try:
        subprocess.run(
            ("/usr/bin/git", "restore", "."),
            check=True,
            cwd=clone_dir.as_posix(),
        )
        if "PKGBUILD_patches_dir" in entry:
            patches_path = pathlib.PosixPath(entry["PKGBUILD_patches_dir"])
            subprocess.run(
                (
                    "/usr/bin/find",
                    patches_path.as_posix(),
                    "-type",
                    "f",
                    "-exec",
                    "sh",
                    "-c",
                    "patch -p1 < {}",
                    ";",
                ),
                check=True,
                cwd=clone_dir.as_posix(),
            )
        subprocess.run(
            ("/usr/bin/env", shared_state["toml"]["editor"], "PKGBUILD"),
            check=True,
            cwd=clone_dir.as_posix(),
        )
        subprocess.run(
            ("/usr/bin/git", "restore", "."),
            check=True,
            cwd=clone_dir.as_posix(),
        )
    except:
        log_print(f"""ERROR: Failed to check "{name}"'s PKGBUILD!""")
        return 1
    check = user_interact_alpha(
        "Is PKGBUILD OK?",
        ["OK", "Not OK", "Recheck", "Force build"],
        True,
        shared_state,
    )
    if check == "interrupt":
        return 3
    elif check == "Recheck":
        return check_PKGBUILD(entry, shared_state)
    elif check == "Force build":
        return 4
    elif check != "OK":
        return 2
    return 0


def run_prepare_only(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    container = shared_state["toml"]["container_name"]
    id_file = shared_state["toml"]["container_identity_file"]
    c_addr = shared_state["toml"]["container_addr"]
    user = shared_state["toml"]["container_user"]
    other_deps = entry["other_deps"] if "other_deps" in entry else list()
    aur_deps = get_aur_deps(entry, shared_state)
    try:
        if start_container(shared_state) != 0:
            return 1

        other_dep_str = ""
        for other_dep in other_deps:
            if len(other_dep_str) == 0:
                other_dep_str = other_dep
            else:
                other_dep_str = other_dep_str + " " + other_dep
        if len(other_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -S {other_dep_str}",
                ),
                check=True,
            )
        aur_dep_str = ""
        for aur_dep in aur_deps:
            dest = pathlib.PosixPath("/tmp")
            dest = dest / aur_dep.name
            if rsync_file_to_dest(aur_dep, dest.as_posix(), shared_state) != 0:
                log_print(
                    f'ERROR: Failed to send aur_dep "{aur_dep.name}" to chroot!'
                )
                return 1
            if len(aur_dep_str) == 0:
                aur_dep_str = dest.as_posix()
            else:
                aur_dep_str = aur_dep_str + " " + dest.as_posix()
        if len(aur_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -U {aur_dep_str}",
                ),
                check=True,
            )

        if shared_state["toml"]["build_in_tmpfs"]:
            dest_dir = f"/tmp/{name}"
        else:
            dest_dir = name

        if rsync_package_to_container(entry, shared_state) != 0:
            return 1

        if "PKGBUILD_patches_dir" in entry:
            patch_dir = pathlib.PosixPath(entry["PKGBUILD_patches_dir"])
            if (
                rsync_dir_to_dest(
                    patch_dir, "/tmp/PKGBUILD_patches/", shared_state
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir} && find /tmp/PKGBUILD_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        checking_gpg_dir = rsync_checking_gpg(shared_state)
        if len(checking_gpg_dir) == 0:
            return 1

        time.sleep(0.3)
        subprocess.run(
            (
                "/usr/bin/ssh",
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" makepkg -s --nobuild --noconfirm',
            ),
            check=True,
            text=True,
        )

        if "SOURCE_patches_dir" in entry:
            patch_dir = pathlib.PosixPath(entry["SOURCE_patches_dir"])
            if (
                rsync_dir_to_dest(
                    patch_dir, "/tmp/SOURCE_patches/", shared_state
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir} && source ./PKGBUILD && cd src/${{pkgname:-{name}}} && find /tmp/SOURCE_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        if rsync_package_from_container(entry, shared_state) != 0:
            return 1

        run_result = subprocess.run(
            ("/usr/bin/git", "restore", "."),
            check=True,
            cwd=clone_dir.as_posix(),
        )

        subprocess.run(
            ("/usr/bin/sudo", "--stdin", "machinectl", "poweroff", container),
            check=False,
            text=True,
            input=shared_state["pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
        time.sleep(3)
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
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}"
    else:
        dest_dir = name
    full_dest_dir = f"{user}@{c_addr}:{dest_dir}/"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--exclude=.git*",
                f"{clone_dir}/",
                full_dest_dir,
            ),
            check=True,
            text=True,
        )
    except:
        log_print("ERROR: Failed to rsync to container!")
        log_print(repr(sys.exception()))
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
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}"
    else:
        dest_dir = name
    full_dest_dir = f"{user}@{c_addr}:{dest_dir}/"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--exclude=src*",
                "--exclude=pkg*",
                full_dest_dir,
                f"{clone_dir}/",
            ),
            check=True,
            text=True,
        )
    except:
        log_print("ERROR: Failed to rsync from container!")
        log_print(repr(sys.exception()))
        return 1
    return 0


def build_pkg(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success, 1 on error."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    other_deps = entry["other_deps"] if "other_deps" in entry else list()
    aur_deps = get_aur_deps(entry, shared_state)
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}"
    else:
        dest_dir = name
    try:
        container = shared_state["toml"]["container_name"]
        if start_container(shared_state) != 0:
            return 1
        user = shared_state["toml"]["container_user"]
        c_addr = shared_state["toml"]["container_addr"]

        id_file = shared_state["toml"]["container_identity_file"]

        if rsync_package_to_container(entry, shared_state) != 0:
            return 1

        if "PKGBUILD_patches_dir" in entry:
            patch_dir = pathlib.PosixPath(entry["PKGBUILD_patches_dir"])
            if (
                rsync_dir_to_dest(
                    patch_dir, "/tmp/PKGBUILD_patches/", shared_state
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir} && find /tmp/PKGBUILD_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        other_dep_str = ""
        for other_dep in other_deps:
            if len(other_dep_str) == 0:
                other_dep_str = other_dep
            else:
                other_dep_str = other_dep_str + " " + other_dep
        if len(other_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -S {other_dep_str}",
                ),
                check=True,
            )
        aur_dep_str = ""
        for aur_dep in aur_deps:
            dest = pathlib.PosixPath("/tmp")
            dest = dest / aur_dep.name
            if rsync_file_to_dest(aur_dep, dest.as_posix(), shared_state) != 0:
                log_print(
                    f'ERROR: Failed to send aur_dep "{aur_dep.name}" to chroot!'
                )
                return 1
            if len(aur_dep_str) == 0:
                aur_dep_str = dest.as_posix()
            else:
                aur_dep_str = aur_dep_str + " " + dest.as_posix()
        if len(aur_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -U {aur_dep_str}",
                ),
                check=True,
            )

        checking_gpg_dir = rsync_checking_gpg(shared_state)
        if len(checking_gpg_dir) == 0:
            return 1

        if "SOURCE_patches_dir" in entry:
            # Prepare first to populate sources for patching
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" makepkg -s --nobuild --noconfirm',
                ),
                check=True,
                text=True,
            )
            patch_dir = pathlib.PosixPath(entry["SOURCE_patches_dir"])
            if (
                rsync_dir_to_dest(
                    patch_dir, "/tmp/SOURCE_patches/", shared_state
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir} && source ./PKGBUILD && cd src/${{pkgdir:-{name}}} && find /tmp/SOURCE_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        nowstring = get_datetime_now()
        logs_dir_path = pathlib.PosixPath(shared_state["toml"]["logs_dir"])
        with logs_dir_path.joinpath(
            "{}_stdout_{}.log".format(name, nowstring)
        ).open(
            mode="w", encoding="utf-8"
        ) as log_stdout, logs_dir_path.joinpath(
            "{}_stderr_{}.log".format(name, nowstring)
        ).open(
            mode="w", encoding="utf-8"
        ) as log_stderr:
            p1 = subprocess.Popen(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" makepkg -s --noconfirm',
                ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print_to_log = shared_state["toml"]["print_build_logs"]
            tout = threading.Thread(
                target=thread_handle_output_stream,
                args=(p1.stdout, log_stdout, shared_state, print_to_log),
            )
            terr = threading.Thread(
                target=thread_handle_output_stream,
                args=(p1.stderr, log_stderr, shared_state, print_to_log),
            )

            tout.start()
            terr.start()

            p1.wait()
            tout.join()
            terr.join()

            if p1.returncode is None:
                raise RuntimeError("pOpen process didn't finish")
            elif type(p1.returncode) is not int:
                raise RuntimeError("pOpen process non-integer return-code")
            elif p1.returncode != 0:
                raise RuntimeError(
                    f"pOpen process non-zero return code {p1.returncode}"
                )

        if rsync_package_from_container(entry, shared_state) != 0:
            return 1

        subprocess.run(
            ("/usr/bin/sudo", "--stdin", "machinectl", "poweroff", container),
            check=True,
            input=shared_state["pass"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
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
    container = shared_state["toml"]["container_name"]
    user = shared_state["toml"]["container_user"]
    saved_pkgver = get_pkgver(entry, shared_state)
    other_deps = entry["other_deps"] if "other_deps" in entry else list()
    aur_deps = get_aur_deps(entry, shared_state)
    id_file = shared_state["toml"]["container_identity_file"]
    c_addr = shared_state["toml"]["container_addr"]
    if saved_pkgver is None:
        log_print(f"{name} has not been built; should be built")
        return 0
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}"
    else:
        dest_dir = name

    start_container(shared_state)
    if rsync_package_to_container(entry, shared_state) != 0:
        return 2
    try:
        if "PKGBUILD_patches_dir" in entry:
            patch_dir = pathlib.PosixPath(entry["PKGBUILD_patches_dir"])
            if (
                rsync_dir_to_dest(
                    patch_dir, "/tmp/PKGBUILD_patches/", shared_state
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir} && find /tmp/PKGBUILD_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        other_dep_str = ""
        for other_dep in other_deps:
            if len(other_dep_str) == 0:
                other_dep_str = other_dep
            else:
                other_dep_str = other_dep_str + " " + other_dep
        if len(other_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -S {other_dep_str}",
                ),
                check=True,
            )
        aur_dep_str = ""
        for aur_dep in aur_deps:
            dest = pathlib.PosixPath("/tmp")
            dest = dest / aur_dep.name
            if rsync_file_to_dest(aur_dep, dest.as_posix(), shared_state) != 0:
                log_print(
                    f'ERROR: Failed to send aur_dep "{aur_dep.name}" to chroot!'
                )
                return 1
            if len(aur_dep_str) == 0:
                aur_dep_str = dest.as_posix()
            else:
                aur_dep_str = aur_dep_str + " " + dest.as_posix()
        if len(aur_dep_str) != 0:
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo pacman --noconfirm -U {aur_dep_str}",
                ),
                check=True,
            )

        checking_gpg_dir = rsync_checking_gpg(shared_state)
        if len(checking_gpg_dir) == 0:
            return 1

        run_ret = subprocess.run(
            (
                "/usr/bin/ssh",
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" makepkg -c -s --noconfirm --nobuild >&/dev/null && source PKGBUILD >&/dev/null && echo "${{epoch:-0}}:${{pkgver:-0.0}}-${{pkgrel:-1}}"',
            ),
            check=True,
            text=True,
            capture_output=True,
        )
        # log_print("DEBUG: stdout is: " + run_ret.stdout.strip())
        PKGBUILD_ver = ArchPkgVersion(run_ret.stdout.strip())
    except:
        log_print("ERROR: Failed to verify if entry should be built!")
        log_print(repr(sys.exception()))
        return 2
    log_print(
        f"{name}: PKGBUILD: {str(PKGBUILD_ver)}, saved: {str(saved_pkgver)}"
    )
    if PKGBUILD_ver > saved_pkgver:
        return 0
    else:
        return 1


def enumerate_clone_dir(
    entry: dict, shared_state: dict
) -> list[pathlib.PosixPath]:
    """Returns a list of files in the clone dir."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    return list(clone_dir.iterdir())


def finalize_build(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    pre_enumerate = shared_state["pre_enumerate"]
    post_enumerate = enumerate_clone_dir(entry, shared_state)
    new_enumerate = list(
        filter(lambda i: i not in pre_enumerate, post_enumerate)
    )
    pkgs = list()
    for item in new_enumerate:
        if IS_PKG_REGEX.fullmatch(item.as_posix()) is not None:
            pkg = item.as_posix()
            pkgs.append(pkg)
            try:
                process_env = dict()
                process_env["GNUPGHOME"] = shared_state["toml"][
                    "signing_gpg_dir"
                ]
                if "sign_gpg_pass" in shared_state:
                    subprocess.run(
                        (
                            "/usr/bin/gpg",
                            "--batch",
                            "--passphrase-fd",
                            "0",
                            "--default-key",
                            shared_state["toml"]["signing_gpg_fingerprint"],
                            "--pinentry-mode",
                            "loopback",
                            "--detach-sign",
                            pkg,
                        ),
                        check=True,
                        text=True,
                        env=process_env,
                        input=shared_state["sign_gpg_pass"],
                    )
                else:
                    subprocess.run(
                        (
                            "/usr/bin/gpg",
                            "--yes",
                            "--default-key",
                            shared_state["toml"]["signing_gpg_fingerprint"],
                            "--pinentry-mode",
                            "loopback",
                            "--detach-sign",
                            pkg,
                        ),
                        check=True,
                        text=True,
                        env=process_env,
                    )
            except:
                log_print(f"ERROR: Failed to sign pkg {pkg}!")
                return 1
    pkgs_out_path = pathlib.PosixPath(shared_state["toml"]["pkgs_out_dir"])
    repo_name = shared_state["toml"]["aur_repo_name"]
    repo_path = pkgs_out_path / f"{repo_name}.db.tar"
    repo_path_sig = pkgs_out_path / f"{repo_name}.db.tar.sig"
    repo_sig_link = pkgs_out_path / f"{repo_name}.db.sig"
    repo_add_cmd = ["/usr/bin/repo-add", "--include-sigs", repo_path.as_posix()]
    repo_add_cmd.extend(pkgs)
    try:
        subprocess.run(
            repo_add_cmd,
            check=True,
            text=True,
        )
    except:
        log_print(f"ERROR: Failed to add pkg {pkg}!")
        return 1
    for pkg in pkgs:
        pkg_path = pathlib.PosixPath(pkg)
        pkg_sig_path = pathlib.PosixPath(pkg + ".sig")
        dest_pkg_path = pkgs_out_path / pkg_path.name
        dest_pkg_sig_path = pkgs_out_path / pkg_sig_path.name
        with pkg_path.open(mode="rb") as r, dest_pkg_path.open(mode="wb") as w:
            ret_read = r.read(4096)
            while len(ret_read) > 0:
                ret_write = w.write(ret_read)
                ret_read = r.read(4096)
        with pkg_sig_path.open(mode="rb") as r, dest_pkg_sig_path.open(
            mode="wb"
        ) as w:
            ret_read = r.read(4096)
            while len(ret_read) > 0:
                ret_write = w.write(ret_read)
                ret_read = r.read(4096)
        pkg_path.unlink()
        pkg_sig_path.unlink()
    try:
        if repo_path_sig.exists():
            repo_path_sig.unlink()
        process_env = dict()
        process_env["GNUPGHOME"] = shared_state["toml"]["signing_gpg_dir"]
        if "sign_gpg_pass" in shared_state:
            subprocess.run(
                (
                    "/usr/bin/gpg",
                    "--batch",
                    "--passphrase-fd",
                    "0",
                    "--default-key",
                    shared_state["toml"]["signing_gpg_fingerprint"],
                    "--pinentry-mode",
                    "loopback",
                    "--detach-sign",
                    repo_path.as_posix(),
                ),
                check=True,
                text=True,
                env=process_env,
                input=shared_state["sign_gpg_pass"],
            )
        else:
            subprocess.run(
                (
                    "/usr/bin/gpg",
                    "--yes",
                    "--default-key",
                    shared_state["toml"]["signing_gpg_fingerprint"],
                    "--pinentry-mode",
                    "loopback",
                    "--detach-sign",
                    repo_path.as_posix(),
                ),
                check=True,
                text=True,
                env=process_env,
            )
    except:
        log_print(f"ERROR: Failed to sign {repo_path.as_posix()}!")
        log_print(repr(sys.exception()))
        return 1
    try:
        repo_sig_link.symlink_to(f"{repo_name}.db.tar.sig")
    except:
        pass

    return 0


def get_aur_deps(entry: dict, shared_state: dict) -> list[pathlib.PosixPath]:
    """Returns a list to each aur dep in the pkgs-out dir."""
    if "aur_deps" not in entry or entry["aur_deps"] is None:
        return list()
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
    aur_deps = list()
    for aur_dep in entry["aur_deps"]:
        aur_dep_regex = re.compile(f"""^{aur_dep}-([0-9].*)$""")
        aur_dep_names = list(
            filter(
                lambda p: p.find(aur_dep) != -1
                and p.find("/") == -1
                and aur_dep_regex.fullmatch(p) is not None,
                repo_names,
            )
        )
        if len(aur_dep_names) == 1:
            aur_deps.extend(aur_dep_names)
        elif len(aur_dep_names) == 0:
            log_print(f"WARNING: aur dep {aur_dep} not found in tar file!")
        else:
            log_print(f"WARNING: aur dep {aur_dep} had multiples in tar file!")
    aur_deps_out = list()
    for aur_dep in aur_deps:
        matching = list(pkgs_out_dir.glob(f"*{aur_dep}*"))
        matching = list(filter(lambda m: m.suffix != ".sig", matching))
        if len(matching) == 1:
            aur_deps_out.extend(matching)
        elif len(matching) == 0:
            log_print(f"WARNING: aur dep {aur_dep.name} not found in pkgs dir!")
        else:
            log_print(f"WARNING: aur dep {aur_dep.name} multiples in pkgs dir!")
    return aur_deps_out


def rsync_file_to_dest(
    file: pathlib.PosixPath, dest: str, shared_state: dict
) -> int:
    """Returns 0 on success."""
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    full_dest_dir = f"{user}@{c_addr}:{dest}"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-ivt",
                file.as_posix(),
                full_dest_dir,
            ),
            check=True,
        )
    except:
        log_print(
            f'ERROR: Failed to rsync/send "{file.as_posix()}" -> "{dest}" file!'
        )
        log_print(repr(sys.exception()))
        return 1
    return 0


def rsync_dir_to_dest(
    dir_path: pathlib.PosixPath, dest: str, shared_state: dict
) -> int:
    """Returns 0 on success."""
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    full_dest_dir = f"{user}@{c_addr}:{dest}"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                dir_path.as_posix() + "/",
                full_dest_dir,
            ),
            check=True,
        )
    except:
        log_print(
            f'ERROR: Failed to rsync/send "{dir_path.as_posix()}" -> "{dest}" directory!'
        )
        log_print(repr(sys.exception()))
        return 1
    return 0


def print_pkg_status(shared_state: dict):
    log_print("Pending Pkgs:")
    for pkg in shared_state["pending_pkgs"]:
        log_print(f"  {pkg}")
    log_print("Failed Pkgs:")
    for pkg in shared_state["failed_pkgs"]:
        log_print(f"  {pkg}")
    log_print("Built Pkgs:")
    for pkg in shared_state["built_pkgs"]:
        log_print(f"  {pkg}")


def handle_signal(sig, other):
    if sig == signal.SIGUSR1:
        print_pkg_status(GLOBAL_SHARED_STATE)


def rsync_checking_gpg(shared_state: dict) -> str:
    """Returns the path to the checking gpg dir in the chroot."""
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    checking_gpg_dir = shared_state["toml"]["checking_gpg_dir"]
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = "/tmp/checking_gpg"
    else:
        dest_dir = f"/home/{user}/checking_gpg"
    full_dest_dir = f"{user}@{c_addr}:{dest_dir}/"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -i {id_file}",
                "-rivt",
                "--chmod=D700,F600",
                checking_gpg_dir + "/",
                full_dest_dir,
            ),
            check=True,
        )
    except:
        log_print(
            f'ERROR: Failed to rsync/send "{dir_path.as_posix()}" -> "{dest}" directory!'
        )
        log_print(repr(sys.exception()))
        return ""
    return dest_dir


def cleanup_packages(shared_state: dict, dry_run: bool) -> int:
    """Returns 0 on success."""
    pkgs_out_dir = pathlib.PosixPath(shared_state["toml"]["pkgs_out_dir"])
    repo_name = shared_state["toml"]["aur_repo_name"]
    repo_path = pkgs_out_dir / f"{repo_name}.db.tar"
    try:
        with tarfile.open(name=repo_path) as f:
            repo_names = f.getnames()
    except:
        log_print(f'Failed to open "{repo_path}"!')
        return 1
    repo_names = set(filter(lambda n: n.find("/") == -1, repo_names))
    pkgs = pkgs_out_dir.iterdir()
    pkgs = set(
        filter(
            lambda p: p.name.find(".pkg.tar") != -1 and p.suffix != ".sig", pkgs
        )
    )
    to_remove = set()
    for pkg in pkgs:
        found = False
        for rname in repo_names:
            if pkg.name.find(rname) != -1:
                found = True
                break
        if not found:
            to_remove.add(pkg)

    # remove
    for pkg in to_remove:
        if dry_run:
            log_print(f"Would remove pkg {pkg.name}...")
        else:
            log_print(f"Removing outdated pkg {pkg.name}...")
            try:
                pkg.unlink()
                pathlib.PosixPath(pkg.as_posix() + ".sig").unlink()
            except:
                log_print(f"WARNING: Failed to remove pkg {pkg.name}!")
                log_print(repr(sys.exception()))

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="AnotherAURHelper2",
        description="Builds AUR pkgs in a chroot handled by systemd-nspawn",
    )
    parser.add_argument("-c", "--config")
    parser.add_argument("-p", "--pkg", action="append")
    parser.add_argument("--skip", action="append")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--cleanup-dryrun", action="store_true")
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
    shared_state["pending_pkgs"] = set()
    shared_state["built_pkgs"] = set()
    shared_state["failed_pkgs"] = set()
    global GLOBAL_TOML_D
    GLOBAL_TOML_D = toml_d
    global GLOBAL_SHARED_STATE
    GLOBAL_SHARED_STATE = shared_state

    if args.cleanup_dryrun:
        cleanup_packages(shared_state, True)
        return
    if args.cleanup:
        cleanup_packages(shared_state, False)
        return

    atexit.register(print_pkg_status, shared_state)
    signal.signal(signal.SIGUSR1, handle_signal)

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

    log_print("Preload signing gpg key credentials?")
    user_result = user_interact_alpha(
        "Preload GPG key pass?", ["Yes, preload", "Skip"], True, shared_state
    )
    if user_result == "Yes, preload":
        shared_state["sign_gpg_pass"] = getpass.getpass(
            prompt="signing gpg password: "
        )
        test_file_name = "test_file_gpg_signing"
        test_file_p_base = pathlib.PosixPath("/tmp")
        test_file_p = test_file_p_base / test_file_name
        while test_file_p.exists():
            test_file_name += "_"
            test_file_p = test_file_p_base / test_file_name
        test_file_p.write_text(
            "Test file to sign with gpg to confirm the credentials are correct."
        )

        try:
            subprocess.run(
                (
                    "gpg",
                    "--batch",
                    "--passphrase-fd",
                    "0",
                    "--pinentry-mode",
                    "loopback",
                    "--default-key",
                    toml_d["signing_gpg_fingerprint"],
                    "--detach-sign",
                    test_file_p.as_posix(),
                ),
                check=True,
                text=True,
                input=shared_state["sign_gpg_pass"],
                env={"GNUPGHOME": toml_d["signing_gpg_dir"]},
            )
        except:
            log_print("ERROR: Failed to sign test_file!")
            log_print(repr(sys.exception()))
            test_file_p.unlink()
            (test_file_p_base / (test_file_name + ".sig")).unlink(
                missing_ok=True
            )
            return
        test_file_p.unlink()
        (test_file_p_base / (test_file_name + ".sig")).unlink()
    elif user_result == "interrupt":
        return

    log_print("Begin checking each package...")
    shared_state["skipped"] = set()
    shared_state["confirmed"] = set()
    idx = 0
    while idx < len(toml_d["entry"]):
        entry = toml_d["entry"][idx]
        if args.pkg is not None:
            if entry["name"] not in args.pkg:
                idx += 1
                shared_state["skipped"].add(entry["name"])
                continue
            elif args.force:
                idx += 1
                shared_state["confirmed"].add(entry["name"])
                shared_state["pending_pkgs"].add(entry["name"])
                continue
        elif args.skip is not None:
            if entry["name"] in args.skip:
                idx += 1
                shared_state["skipped"].add(entry["name"])
                continue
            elif args.force:
                idx += 1
                shared_state["confirmed"].add(entry["name"])
                shared_state["pending_pkgs"].add(entry["name"])
                continue
        elif args.force:
            idx += 1
            shared_state["confirmed"].add(entry["name"])
            shared_state["pending_pkgs"].add(entry["name"])
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
        elif check_ret == 4:
            shared_state["confirmed"].add(entry["name"])
            shared_state["pending_pkgs"].add(entry["name"])
            idx += 1
            continue
        elif check_ret != 0:
            log_print(f"""Skipping "{entry['name']}" due to PKGBUILD...""")
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        if run_prepare_only(entry, shared_state) == 1:
            log_print(f'"{entry['name']}" failed to prepare!')
            user_result = user_interact_alpha(
                "What to do?",
                ["Skip", "Force build", "Abort"],
                True,
                shared_state,
            )
            if user_result == "Abort" or user_result == "interrupt":
                return
            elif user_result == "Skip":
                log_print(
                    f"""Skipping "{entry['name']}" due to failure to "prepare"..."""
                )
                shared_state["skipped"].add(entry["name"])
                idx += 1
                continue
            elif user_result == "Force build":
                shared_state["confirmed"].add(entry["name"])
                shared_state["pending_pkgs"].add(entry["name"])
                idx += 1
                continue
        user_result = user_interact_alpha(
            "OK with pkg?",
            ["OK", "Not OK", "Force build", "Retry"],
            True,
            shared_state,
        )
        if user_result == "interrupt":
            return
        elif user_result == "Force build":
            shared_state["confirmed"].add(entry["name"])
            shared_state["pending_pkgs"].add(entry["name"])
            idx += 1
            continue
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
            shared_state["pending_pkgs"].add(entry["name"])
            log_print(f"Will build {entry['name']}")
        elif verif_ret == 1:
            shared_state["skipped"].add(entry["name"])
            log_print(f"Will NOT build {entry['name']}")
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
            elif user_result == "Abort" or user_result == "interrupt":
                return
            shared_state["skipped"].add(entry["name"])
        idx += 1

    # Multiple passes through all packages.
    # Don't care about efficiency because its not that important in this case.
    log_print("List of skipped:")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["skipped"]:
            log_print(f'  {entry["name"]}')
    log_print("List of not skipped:")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["confirmed"]:
            log_print(f'  {entry["name"]}')
    if len(shared_state["confirmed"]) == 0:
        log_print("Nothing to build, stopping.")
        return
    log_print("Continue?")
    user_result = user_interact_alpha(
        "Build pkgs?", ["Continue", "Abort"], True, shared_state
    )
    if user_result != "Continue":
        return
    log_print("Building packages...")
    for idx in range(len(toml_d["entry"])):
        entry = toml_d["entry"][idx]
        if entry["name"] in shared_state["confirmed"]:
            shared_state["pre_enumerate"] = enumerate_clone_dir(
                entry, shared_state
            )
            build_ret = build_pkg(entry, shared_state)
            if build_ret != 0:
                log_print(f"WARNING: Failed to build \"{entry['name']}\"!")
                shared_state["failed_pkgs"].add(entry["name"])
                shared_state["pending_pkgs"].remove(entry["name"])
                continue
            ret = finalize_build(entry, shared_state)
            if ret != 0:
                log_print(f"WARNING: Failed to finalize \"{entry['name']}\"!")
                shared_state["failed_pkgs"].add(entry["name"])
                shared_state["pending_pkgs"].remove(entry["name"])
                continue
            shared_state["built_pkgs"].add(entry["name"])
            shared_state["pending_pkgs"].remove(entry["name"])
    log_print("Done.")


if __name__ == "__main__":
    main()
