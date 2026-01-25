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
import fcntl
import getpass
import hashlib
import os
import pathlib
import re
import signal
import sqlite3
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
PKG_REL_RE = re.compile("^(.*)-([0-9]+)$")
IS_DIGIT_REGEX = re.compile("^[0-9]+$")
IS_PKG_REGEX = re.compile(r"^.*\.pkg\.tar\.([a-z]+)$")
CONTAINER_WAIT_TIMEOUT = 20
CONTAINER_SSH_WAIT_TIMEOUT = 2
SQLITE_PKGBUILD_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS PkgbuildHash (PKG TEXT PRIMARY KEY, HASH TEXT)"
)
SQLITE_PKGBUILD_INIT = "INSERT INTO PkgbuildHash (PKG) VALUES (?)"
SQLITE_PKGBUILD_UPDATE = "UPDATE PkgbuildHash SET HASH = ? WHERE PKG = ?"
SQLITE_PKGBUILD_CHECK = (
    "SELECT PKG FROM PkgbuildHash WHERE PKG = ? AND HASH = ?"
)


class ArchPkgVersion:
    """Holds a version (typically of an ArchLinux package) for comparison."""

    def __init__(self, version_str: str):
        self.versions = []
        self.pkgrel = 1
        self.epoch = 0
        epoch_match = EPOCH_RE.match(version_str)
        if not epoch_match is None:
            self.epoch = int(epoch_match.group(1))
            version_str = epoch_match.group(2)
        pkgrel_match = PKG_REL_RE.match(version_str)
        if not pkgrel_match is None:
            version_str = pkgrel_match.group(1)
            self.pkgrel = int(pkgrel_match.group(2))

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
                if self.pkgrel < other_self.pkgrel:
                    return -1
                elif self.pkgrel > other_self.pkgrel:
                    return 1
                else:
                    return 0
            except TypeError:
                if str(self.pkgrel) < str(other_self.pkgrel):
                    return -1
                elif str(self.pkgrel) > str(other_self.pkgrel):
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
        self_str += "-" + str(self.pkgrel)
        return self_str

    def __repr__(self):
        "<ArchPkgVersion: " + self.__str__() + " >"


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

        if ignore_output_file:
            continue

        if not limit_reached:
            if print_to_log:
                log_print(line.rstrip())
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
    PKGBUILD_path = clone_dir / "PKGBUILD"
    sqlitedb_path = shared_state["toml"]["database_path"]
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

        # Check if already approved: hash matches in db.
        with PKGBUILD_path.open("rb") as r:
            m = hashlib.sha256()
            read_ret = r.read(4096)
            while len(read_ret) != 0:
                m.update(read_ret)
                read_ret = r.read(4096)
            h = m.hexdigest()
        conn = sqlite3.connect(sqlitedb_path)
        cur = conn.execute(SQLITE_PKGBUILD_CHECK, (name, h))
        row_count = len(cur.fetchall())
        if row_count == 0:
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

        if row_count != 0:
            return 0
    except:
        log_print(f"""ERROR: Failed to check "{name}"'s PKGBUILD!""")
        log_print(repr(sys.exception()))
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
    if "hash_compare_PKGBUILD" in entry and entry["hash_compare_PKGBUILD"]:
        conn.execute(SQLITE_PKGBUILD_UPDATE, (h, name))
        conn.commit()
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
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
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
                    "-p",
                    ssh_port,
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
                    "-p",
                    ssh_port,
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

        if (
            not "disable_cargo_cache" in entry
            or not entry["disable_cargo_cache"]
        ):
            rsync_cargo_home_to_container(entry, shared_state)

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
                    "-p",
                    ssh_port,
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
                "-p",
                ssh_port,
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" CARGO_HOME="{dest_dir}/cargo-home" makepkg -s --nobuild --noconfirm',
            ),
            check=True,
            text=True,
        )

        # Prefetch PKGBUILD version
        run_ret = subprocess.run(
            (
                "/usr/bin/ssh",
                "-p",
                ssh_port,
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f'cd {dest_dir} && source PKGBUILD >&/dev/null && echo "${{epoch:-0}}:${{pkgver:-0.0}}-${{pkgrel:-1}}"',
            ),
            check=True,
            text=True,
            capture_output=True,
        )
        shared_state["cached_PKGBUILD_ver"][name] = ArchPkgVersion(
            run_ret.stdout.strip()
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
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir}/src && find /tmp/SOURCE_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )

        if (
            not "disable_cargo_cache" in entry
            or not entry["disable_cargo_cache"]
        ):
            rsync_cargo_home_from_container(entry, shared_state)
        delete_cargo_home_in_container(entry, shared_state)

        if rsync_package_from_container(entry, shared_state) != 0:
            return 1

        run_result = subprocess.run(
            ("/usr/bin/git", "restore", "."),
            check=True,
            cwd=clone_dir.as_posix(),
        )

        stop_ret = stop_container(shared_state)
        if stop_ret != 0:
            return 1
    except:
        log_print(f"""ERROR: Failed to run "prepare" on "{name}"'s PKGBUILD!""")
        log_print(repr(sys.exception()))
        return 1
    return 0


def stop_container(shared_state: dict) -> int:
    """Returns 0 on success."""
    container = shared_state["toml"]["container_name"]
    id_file = shared_state["toml"]["container_identity_file"]
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    subproc_ret = subprocess.run(
        (
            "/usr/bin/systemctl",
            "is-active",
            f"systemd-nspawn@{container}.service",
        ),
        check=False,
        text=True,
        capture_output=True,
    )
    if subproc_ret.stdout.strip() == "inactive":
        return 0
    elif subproc_ret.stdout.strip().find("active") == -1:
        return 1
    try:
        subprocess.run(
            ("/usr/bin/sudo", "--stdin", "machinectl", "poweroff", container),
            check=True,
            text=True,
            input=shared_state["pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            f"while systemctl is-active systemd-nspawn@{container} | grep '^active'; do sleep 0.3; done",
            check=True,
            text=True,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CONTAINER_WAIT_TIMEOUT,
        )
    except:
        log_print("ERROR: Failed to stop container!")
        log_print(repr(sys.exception()))
        return 1
    return 0


def start_container(shared_state: dict) -> int:
    """Returns 0 on success."""
    container = shared_state["toml"]["container_name"]
    container_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    c_addr = shared_state["toml"]["container_addr"]
    user = shared_state["toml"]["container_user"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    stop_ret = stop_container(shared_state)
    if stop_ret != 0:
        log_print("ERROR: Failed to stop before starting container!")
        return 1
    try:
        subprocess.run(
            ("/usr/bin/sudo", "--stdin", "machinectl", "start", container),
            check=True,
            text=True,
            input=shared_state["pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            f"while ping -c 1 -W 1 {container_addr}; do sleep 0.1; done",
            check=True,
            text=True,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CONTAINER_WAIT_TIMEOUT,
        )
    except:
        log_print("ERROR: Failed to start container!")
        log_print(repr(sys.exception()))
        return 1

    ssh_is_ready = False
    fail_count = 0
    while not ssh_is_ready:
        try:
            subprocess.run(
                f"ssh -p {ssh_port} -i {id_file} {user}@{c_addr} ls",
                check=True,
                text=True,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CONTAINER_SSH_WAIT_TIMEOUT,
            )
            ssh_is_ready = True
        except:
            fail_count += 1
            if fail_count > 10:
                log_print("ERROR: Failed to check container ssh!")
                log_print(repr(sys.exception()))
                return 1
            continue

    return 0


def rsync_package_to_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
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
                f"ssh -p {ssh_port} -i {id_file}",
                "-rivt",
                "--links",
                "--exclude=/.git*",
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
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
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
                f"ssh -p {ssh_port} -i {id_file}",
                "-rivt",
                "--links",
                "--delete",
                "--exclude=/src*",
                "--exclude=/pkg*",
                "--exclude=/.git*",
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


def rsync_cargo_home_to_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}/cargo-home"
    else:
        dest_dir = name + "/" + "cargo-home"
    full_dest_dir = f"{user}@{c_addr}:{dest_dir}/"
    if "cargo_home_path" in shared_state["toml"]:
        cargo_home_path = shared_state["toml"]["cargo_home_path"]
    else:
        return 1
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -p {ssh_port} -i {id_file}",
                "-rivt",
                cargo_home_path + "/",
                full_dest_dir,
            ),
            check=True,
            text=True,
        )
    except:
        log_print("WARNING: Failed to rsync cargo-home to CONTAINER!")
        log_print(repr(sys.exception()))
        return 1

    return 0


def rsync_cargo_home_from_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}/cargo-home"
    else:
        dest_dir = name + "/" + "cargo-home"
    full_dest_dir = f"{user}@{c_addr}:{dest_dir}/"
    if "cargo_home_path" in shared_state["toml"]:
        cargo_home_path = shared_state["toml"]["cargo_home_path"]
    else:
        return 1
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -p {ssh_port} -i {id_file}",
                "-rivt",
                full_dest_dir,
                cargo_home_path + "/",
            ),
            check=True,
            text=True,
        )
    except:
        log_print("WARNING: Failed to rsync cargo-home from CONTAINER!")
        log_print(repr(sys.exception()))
        return 1
    return 0


def delete_cargo_home_in_container(entry: dict, shared_state: dict) -> int:
    """Returns 0 on success."""
    name = entry["name"]
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}/cargo-home"
    else:
        dest_dir = name + "/" + "cargo-home"
    try:
        subprocess.run(
            (
                "/usr/bin/ssh",
                "-p",
                ssh_port,
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f"rm -rf {dest_dir}",
            ),
            check=True,
            text=True,
        )
    except:
        log_print("WARNING: Failed to remove cargo-home in CONTAINER!")
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
    pkg_ver = get_pkgver(entry, shared_state)
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    ccache_enabled = False
    ccache_env_str = ""
    if "ccache_dir" in entry:
        ccache_dir = pathlib.PosixPath(entry["ccache_dir"])
        if "ccache_in_tmpfs" in entry and entry["ccache_in_tmpfs"]:
            ccache_container_dir = pathlib.PosixPath("/tmp")
            ccache_container_dir = ccache_container_dir / "ccache"
        else:
            ccache_container_dir = pathlib.PosixPath("/home")
            ccache_container_dir = (
                ccache_container_dir / entry["container_user"] / "ccache"
            )
        ccache_env_str = 'CCACHE_DIR="' + ccache_container_dir.as_posix() + '"'
        ccache_enabled = True

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

        if ccache_enabled:
            if (
                rsync_dir_to_dest(
                    ccache_dir,
                    ccache_container_dir.as_posix() + "/",
                    shared_state,
                )
                != 0
            ):
                return 1
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"sudo sed -i -e '/^BUILDENV/s/!ccache/ccache/' /etc/makepkg.conf",
                ),
                check=True,
            )
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    "sudo pacman --noconfirm -S ccache",
                ),
                check=True,
            )

        if rsync_package_to_container(entry, shared_state) != 0:
            return 1

        if (
            not "disable_cargo_cache" in entry
            or not entry["disable_cargo_cache"]
        ):
            rsync_cargo_home_to_container(entry, shared_state)

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
                    "-p",
                    ssh_port,
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
                    "-p",
                    ssh_port,
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
                    "-p",
                    ssh_port,
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

        no_prepare_str = ""
        if "SOURCE_patches_dir" in entry:
            # Prepare first to populate sources for patching
            subprocess.run(
                (
                    "/usr/bin/ssh",
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" CARGO_HOME="{dest_dir}/cargo-home" makepkg -s --nobuild --noconfirm',
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
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f"cd {dest_dir}/src && find /tmp/SOURCE_patches/ -type f -exec sh -c 'patch -p1 < {{}}' ';'",
                ),
                check=True,
            )
            no_prepare_str = "--noextract"

        if pkg_ver is not None:
            # Check if pkgrel should be incremented.
            if len(no_prepare_str) == 0:
                subprocess.run(
                    (
                        "/usr/bin/ssh",
                        "-p",
                        ssh_port,
                        "-i",
                        id_file,
                        f"{user}@{c_addr}",
                        f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" CARGO_HOME="{dest_dir}/cargo-home" makepkg -s --noconfirm --nobuild',
                    ),
                    check=True,
                )
                no_prepare_str = "--noextract"
                run_ret = subprocess.run(
                    (
                        "/usr/bin/ssh",
                        "-p",
                        ssh_port,
                        "-i",
                        id_file,
                        f"{user}@{c_addr}",
                        f'cd {dest_dir} && source PKGBUILD >&/dev/null && echo "${{epoch:-0}}:${{pkgver:-0.0}}-${{pkgrel:-1}}"',
                    ),
                    check=True,
                    text=True,
                    capture_output=True,
                )
            else:
                run_ret = subprocess.run(
                    (
                        "/usr/bin/ssh",
                        "-p",
                        ssh_port,
                        "-i",
                        id_file,
                        f"{user}@{c_addr}",
                        f'cd {dest_dir} && source PKGBUILD >&/dev/null && echo "${{epoch:-0}}:${{pkgver:-0.0}}-${{pkgrel:-1}}"',
                    ),
                    check=True,
                    text=True,
                    capture_output=True,
                )
            PKGBUILD_ver = ArchPkgVersion(run_ret.stdout.strip())
            if (
                PKGBUILD_ver.versions == pkg_ver.versions
                and PKGBUILD_ver.pkgrel <= pkg_ver.pkgrel
            ):
                new_pkgrel = pkg_ver.pkgrel + 1
                log_print(
                    f"NOTICE: Incrementing pkgrel in PKGBUILD for pkg {name} from {PKGBUILD_ver.pkgrel} to {new_pkgrel}..."
                )
                subprocess.run(
                    (
                        "/usr/bin/ssh",
                        "-p",
                        ssh_port,
                        "-i",
                        id_file,
                        f"{user}@{c_addr}",
                        f'cd {dest_dir} && sed -i -e "/^pkgrel/cpkgrel={new_pkgrel}" PKGBUILD',
                    ),
                    check=True,
                )

        nowstring = get_datetime_now()
        logs_dir_path = pathlib.PosixPath(shared_state["toml"]["logs_dir"])
        subprocess_log_output(
            name,
            [
                "/usr/bin/ssh",
                "-p",
                ssh_port,
                "-i",
                id_file,
                f"{user}@{c_addr}",
                f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" CARGO_HOME="{dest_dir}/cargo-home" {ccache_env_str} makepkg -s --noconfirm {no_prepare_str}',
            ],
            shared_state,
        )

        if (
            not "disable_cargo_cache" in entry
            or not entry["disable_cargo_cache"]
        ):
            rsync_cargo_home_from_container(entry, shared_state)
        delete_cargo_home_in_container(entry, shared_state)

        if ccache_enabled:
            rsync_dir_from_dest(
                ccache_dir,
                ccache_container_dir.as_posix() + "/",
                shared_state,
                rsync_del=True,
            )

        if rsync_package_from_container(entry, shared_state) != 0:
            return 1

        stop_ret = stop_container(shared_state)
        if stop_ret != 0:
            return 1
    except:
        if ccache_enabled:
            rsync_dir_from_dest(
                ccache_dir,
                ccache_container_dir.as_posix() + "/",
                shared_state,
                rsync_del=True,
            )
        log_print(f"""ERROR: Failed to build "{name}"!""")
        log_print(repr(sys.exception()))
        return 1
    return 0


def get_pkgver(
    entry: dict, shared_state: dict
) -> typing.Optional[ArchPkgVersion]:
    """Gets the latest built version of a package."""
    name = entry["name"]
    pkg_name = entry["name"] if "pkg_name" not in entry else entry["pkg_name"]
    pkgs_out_dir = pathlib.PosixPath(shared_state["toml"]["pkgs_out_dir"])
    repo_name = shared_state["toml"]["aur_repo_name"]
    repo_path = pkgs_out_dir / f"{repo_name}.db.tar"
    try:
        with tarfile.open(name=repo_path) as f:
            repo_names = f.getnames()
            name_regex = re.compile(f"""^{pkg_name}-(.*)$""")
            repo_names = list(
                filter(
                    lambda p: p.find(pkg_name) != -1
                    and p.find("/desc") != -1
                    and name_regex.fullmatch(p) is not None,
                    repo_names,
                )
            )
            repo_names_idx = 0
            if len(repo_names) == 0:
                log_print(f"{pkg_name} not in {repo_name}.db.tar!")
                return None
            elif len(repo_names) > 1:
                for idx in range(len(repo_names)):
                    ti = f.getmember(repo_names[idx])
                    desc_f = f.extractfile(ti)
                    line = desc_f.readline()
                    name_found = False
                    while len(line) != 0:
                        if line.decode().strip() == "%NAME%":
                            line = desc_f.readline()
                            if line.decode().strip() == pkg_name:
                                repo_names_idx = idx
                                name_found = True
                            break
                        line = desc_f.readline()
                    if name_found:
                        break
            ti = f.getmember(repo_names[repo_names_idx])
            desc_f = f.extractfile(ti)
            line = desc_f.readline()
            while len(line) != 0:
                if line.decode().strip() == "%VERSION%":
                    line = desc_f.readline().decode().strip()
                    return ArchPkgVersion(line)
                line = desc_f.readline()
    except:
        log_print(f'Failed to open "{repo_path}"!')
        return None
    return None


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
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    clones_dir = pathlib.PosixPath(shared_state["toml"]["clones_dir"])
    clone_dir = clones_dir / name
    SRCINFO_path = clone_dir / ".SRCINFO"
    if saved_pkgver is None:
        log_print(f"{name} has not been built; should be built")
        return 0
    if shared_state["toml"]["build_in_tmpfs"]:
        dest_dir = f"/tmp/{name}"
    else:
        dest_dir = name

    if "only_check_SRCINFO" in entry and entry["only_check_SRCINFO"]:
        SRCINFO_ver = None
        SRCINFO_pkgver = None
        SRCINFO_pkgrel = None
        SRCINFO_epoch = None
        with SRCINFO_path.open() as srcinfo:
            line = srcinfo.readline()
            while len(line) != 0:
                line = line.strip()
                if len(line) != 0:
                    tup = line.partition("=")
                    if tup[0].strip() == "pkgver":
                        SRCINFO_pkgver = tup[2].strip()
                        if (
                            SRCINFO_pkgrel is not None
                            and SRCINFO_epoch is not None
                        ):
                            SRCINFO_ver = ArchPkgVersion(
                                SRCINFO_epoch
                                + ":"
                                + SRCINFO_pkgver
                                + "-"
                                + SRCINFO_pkgrel
                            )
                            break
                    elif tup[0].strip() == "pkgrel":
                        SRCINFO_pkgrel = tup[2].strip()
                        if (
                            SRCINFO_pkgver is not None
                            and SRCINFO_epoch is not None
                        ):
                            SRCINFO_ver = ArchPkgVersion(
                                SRCINFO_epoch
                                + ":"
                                + SRCINFO_pkgver
                                + "-"
                                + SRCINFO_pkgrel
                            )
                            break
                    elif tup[0].strip() == "epoch":
                        SRCINFO_epoch = tup[2].strip()
                        if (
                            SRCINFO_pkgver is not None
                            and SRCINFO_pkgrel is not None
                        ):
                            SRCINFO_ver = ArchPkgVersion(
                                SRCINFO_epoch
                                + ":"
                                + SRCINFO_pkgver
                                + "-"
                                + SRCINFO_pkgrel
                            )
                            break
                line = srcinfo.readline()
        if (
            SRCINFO_epoch is None
            and SRCINFO_pkgver is not None
            and SRCINFO_pkgrel is not None
        ):
            SRCINFO_ver = ArchPkgVersion(SRCINFO_pkgver + "-" + SRCINFO_pkgrel)
        if SRCINFO_ver is None:
            return 2
        log_print(
            f"{name}: SRCINFO: {str(SRCINFO_ver)}, saved: {str(saved_pkgver)}"
        )
        if SRCINFO_ver > saved_pkgver:
            return 0
        else:
            return 1
    elif name in shared_state["cached_PKGBUILD_ver"]:
        log_print(
            f"{name}: PKGBUILD: {shared_state["cached_PKGBUILD_ver"][name]}, saved {saved_pkgver}"
        )
        if shared_state["cached_PKGBUILD_ver"][name] > saved_pkgver:
            return 0
        else:
            return 1
    else:
        start_container(shared_state)

        if rsync_package_to_container(entry, shared_state) != 0:
            return 2

        if (
            not "disable_cargo_cache" in entry
            or not entry["disable_cargo_cache"]
        ):
            rsync_cargo_home_to_container(entry, shared_state)

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
                        "-p",
                        ssh_port,
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
                        "-p",
                        ssh_port,
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
                if (
                    rsync_file_to_dest(aur_dep, dest.as_posix(), shared_state)
                    != 0
                ):
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
                        "-p",
                        ssh_port,
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
                    "-p",
                    ssh_port,
                    "-i",
                    id_file,
                    f"{user}@{c_addr}",
                    f'cd {dest_dir} && env GNUPGHOME="{checking_gpg_dir}" CARGO_HOME="{dest_dir}/cargo-home" makepkg -c -s --noconfirm --nobuild >&/dev/null && source PKGBUILD >&/dev/null && echo "${{epoch:-0}}:${{pkgver:-0.0}}-${{pkgrel:-1}}"',
                ),
                check=True,
                text=True,
                capture_output=True,
            )
            # log_print("DEBUG: stdout is: " + run_ret.stdout.strip())
            PKGBUILD_ver = ArchPkgVersion(run_ret.stdout.strip())

            if (
                not "disable_cargo_cache" in entry
                or not entry["disable_cargo_cache"]
            ):
                rsync_cargo_home_from_container(entry, shared_state)
            delete_cargo_home_in_container(entry, shared_state)
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
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    full_dest_dir = f"{user}@{c_addr}:{dest}"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -p {ssh_port} -i {id_file}",
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
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    full_dest_dir = f"{user}@{c_addr}:{dest}"
    try:
        subprocess.run(
            (
                "/usr/bin/rsync",
                "-e",
                f"ssh -p {ssh_port} -i {id_file}",
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


def rsync_dir_from_dest(
    dir_path: pathlib.PosixPath,
    dest: str,
    shared_state: dict,
    rsync_del: bool = False,
) -> int:
    """Returns 0 on success."""
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
    full_dest_dir = f"{user}@{c_addr}:{dest}"
    if full_dest_dir[len(full_dest_dir) - 1] != "/":
        full_dest_dir += "/"
    try:
        args = [
            "/usr/bin/rsync",
            "-e",
            f"ssh -p {ssh_port} -i {id_file}",
            "-rivt",
            full_dest_dir,
            dir_path.as_posix() + "/",
        ]
        if rsync_del:
            args.insert(4, "--delete")
        subprocess.run(
            args,
            check=True,
        )
    except:
        log_print(
            f'ERROR: Failed to rsync/recv "{dest}" -> "{dir_path.as_posix()}" directory!'
        )
        log_print(repr(sys.exception()))
        return 1
    return 0


def print_pkg_status(shared_state: dict):
    if "skipped" in shared_state:
        log_print("Skipped Pkgs (usually up-to-date):")
        for entry in shared_state["toml"]["entry"]:
            if entry["name"] in shared_state["skipped"]:
                log_print(f"  {entry["name"]}")
    if "pending_pkgs" in shared_state:
        log_print("Pending Pkgs:")
        for entry in shared_state["toml"]["entry"]:
            if entry["name"] in shared_state["pending_pkgs"]:
                log_print(f"  {entry["name"]}")
    if "failed_pkgs" in shared_state:
        log_print("Failed Pkgs:")
        for entry in shared_state["toml"]["entry"]:
            if entry["name"] in shared_state["failed_pkgs"]:
                log_print(f"  {entry["name"]}")
    if "built_pkgs" in shared_state:
        log_print("Built Pkgs:")
        for entry in shared_state["toml"]["entry"]:
            if entry["name"] in shared_state["built_pkgs"]:
                log_print(f"  {entry["name"]}")


def handle_signal(sig, other):
    if sig == signal.SIGUSR1:
        print_pkg_status(GLOBAL_SHARED_STATE)


def rsync_checking_gpg(shared_state: dict) -> str:
    """Returns the path to the checking gpg dir in the chroot."""
    user = shared_state["toml"]["container_user"]
    c_addr = shared_state["toml"]["container_addr"]
    id_file = shared_state["toml"]["container_identity_file"]
    checking_gpg_dir = shared_state["toml"]["checking_gpg_dir"]
    ssh_port = str(shared_state["toml"]["container_sshd_port"])
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
                f"ssh -p {ssh_port} -i {id_file}",
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


def delete_posix_path_dir(path: pathlib.PosixPath):
    """Recursively deletes a dir path."""
    if not path.is_dir():
        return
    l = list(path.walk(top_down=False))
    for pp, d_list, f_list in l:
        for f in f_list:
            (pp / f).unlink()
        for d in d_list:
            (pp / d).rmdir()
    path.rmdir()


def subprocess_log_output(fname: str, args: list[str], shared_state: dict):
    """Exception on error."""
    logs_dir_path = pathlib.PosixPath(shared_state["toml"]["logs_dir"])
    nowstring = get_datetime_now()
    with logs_dir_path.joinpath(f"{fname}_stdout_{nowstring}.log").open(
        mode="w", encoding="utf-8"
    ) as log_stdout, logs_dir_path.joinpath(
        f"{fname}_stderr_{nowstring}.log"
    ).open(
        mode="w", encoding="utf-8"
    ) as log_stderr:
        proc_handle = subprocess.Popen(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print_to_log = shared_state["toml"]["print_build_logs"]
        tout = threading.Thread(
            target=thread_handle_output_stream,
            args=(proc_handle.stdout, log_stdout, shared_state, print_to_log),
        )
        terr = threading.Thread(
            target=thread_handle_output_stream,
            args=(proc_handle.stderr, log_stderr, shared_state, print_to_log),
        )

        tout.start()
        terr.start()

        proc_handle.wait()
        tout.join()
        terr.join()

        if proc_handle.returncode is None:
            raise RuntimeError("pOpen process didn't finish")
        elif type(proc_handle.returncode) is not int:
            raise RuntimeError("pOpen process non-integer return-code")
        elif proc_handle.returncode != 0:
            raise RuntimeError(
                f"pOpen process non-zero return code {proc_handle.returncode}"
            )


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
    if "container_sshd_port" not in toml_d:
        toml_d["container_sshd_port"] = 22
    shared_state = dict()
    shared_state["toml"] = toml_d
    shared_state["pending_pkgs"] = set()
    shared_state["built_pkgs"] = set()
    shared_state["failed_pkgs"] = set()
    shared_state["cached_PKGBUILD_ver"] = dict()
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
        not_success = True
        while not_success:
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
                not_success = False
            except:
                log_print("ERROR: Failed to add key to ssh-agent!")
                log_print(repr(sys.exception()))

    log_print("Preload signing gpg key credentials?")
    user_result = user_interact_alpha(
        "Preload GPG key pass?", ["Yes, preload", "Skip"], True, shared_state
    )
    if user_result == "Yes, preload":
        log_print("pkill current user's gpg-agent before entering gpg pass?")
        user_result = user_interact_alpha(
            "Use pkill?",
            ["Yes", "No"],
            True,
            shared_state,
        )
        if user_result == "Yes":
            subprocess.run(
                (
                    "/usr/bin/pkill",
                    "-u",
                    os.environ["USER"],
                    "-x",
                    "gpg-agent",
                ),
                check=False,
            )

        not_success = True
        while not_success:
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
                        "/usr/bin/gpg",
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
                not_success = False
            except:
                log_print("ERROR: Failed to sign test_file!")
                log_print(repr(sys.exception()))
                test_file_p.unlink(missing_ok=True)
                (test_file_p_base / (test_file_name + ".sig")).unlink(
                    missing_ok=True
                )
        test_file_p.unlink(missing_ok=True)
        (test_file_p_base / (test_file_name + ".sig")).unlink(missing_ok=True)
    elif user_result == "interrupt":
        return

    # Prepare sqlitedb if first time using it.
    sqlite_conn = sqlite3.connect(toml_d["database_path"])
    sqlite_conn.execute(SQLITE_PKGBUILD_SCHEMA)
    for idx in range(len(toml_d["entry"])):
        try:
            sqlite_conn.execute(
                SQLITE_PKGBUILD_INIT, (toml_d["entry"][idx]["name"],)
            )
        except:
            # Just ensure entries exist, doesn't matter if they already do.
            pass
    sqlite_conn.commit()
    sqlite_conn.close()

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
        log_print(
            f"Checking {entry['name']} ({idx + 1} of {len(toml_d["entry"])})..."
        )
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
        do_continue = False
        while True:
            if run_prepare_only(entry, shared_state) == 1:
                log_print(f'"{entry['name']}" failed to prepare!')
                user_result = user_interact_alpha(
                    "What to do?",
                    ["Skip", "Retry", "Force build", "Abort"],
                    True,
                    shared_state,
                )
                if user_result == "Abort" or user_result == "interrupt":
                    return
                if user_result == "Retry":
                    continue
                elif user_result == "Skip":
                    log_print(
                        f"""Skipping "{entry['name']}" due to failure to "prepare"..."""
                    )
                    shared_state["skipped"].add(entry["name"])
                    idx += 1
                    do_continue = True
                    break
                elif user_result == "Force build":
                    shared_state["confirmed"].add(entry["name"])
                    shared_state["pending_pkgs"].add(entry["name"])
                    idx += 1
                    do_continue = True
                    break
            break
        if do_continue:
            continue
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
        log_print(f"OK with pkg {entry["name"]}?")
        user_result = user_interact_alpha(
            "OK with pkg?",
            ["OK", "Not OK", "Force build", "Retry", "Back"],
            True,
            shared_state,
        )
        if user_result == "interrupt":
            return
        elif user_result == "Force build":
            shared_state["confirmed"].add(entry["name"])
            shared_state["pending_pkgs"].add(entry["name"])
            if entry["name"] in shared_state["skipped"]:
                shared_state["skipped"].remove(entry["name"])
            idx += 1
            continue
        elif user_result == "Retry":
            if entry["name"] in shared_state["confirmed"]:
                shared_state["confirmed"].remove(entry["name"])
            if entry["name"] in shared_state["pending_pkgs"]:
                shared_state["pending_pkgs"].remove(entry["name"])
            if entry["name"] in shared_state["skipped"]:
                shared_state["skipped"].remove(entry["name"])
            continue
        elif user_result == "Back":
            if entry["name"] in shared_state["confirmed"]:
                shared_state["confirmed"].remove(entry["name"])
            if entry["name"] in shared_state["pending_pkgs"]:
                shared_state["pending_pkgs"].remove(entry["name"])
            if entry["name"] in shared_state["skipped"]:
                shared_state["skipped"].remove(entry["name"])
            idx -= 1
            if idx < 0:
                idx = 0
            entry = toml_d["entry"][idx]
            if entry["name"] in shared_state["confirmed"]:
                shared_state["confirmed"].remove(entry["name"])
            if entry["name"] in shared_state["pending_pkgs"]:
                shared_state["pending_pkgs"].remove(entry["name"])
            if entry["name"] in shared_state["skipped"]:
                shared_state["skipped"].remove(entry["name"])
            continue
        elif user_result != "OK":
            log_print(
                f"""Skipping "{entry['name']}" due to user not OK with pkg..."""
            )
            if entry["name"] in shared_state["confirmed"]:
                shared_state["confirmed"].remove(entry["name"])
            if entry["name"] in shared_state["pending_pkgs"]:
                shared_state["pending_pkgs"].remove(entry["name"])
            shared_state["skipped"].add(entry["name"])
            idx += 1
            continue
        log_print(f"User is OK with \"{entry['name']}\"")
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
        log_print("\nNothing to build, stopping.\n")
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
    with open("/tmp/anotherAurHelper2_flock", "a") as flock_fd:
        print("Acquiring flock...")
        fcntl.flock(flock_fd, fcntl.LOCK_EX)
        main()
