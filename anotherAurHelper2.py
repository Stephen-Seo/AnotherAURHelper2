#!/usr/bin/env python3

import argparse
import datetime
import getpass
import subprocess
import sys
import tomllib

STRFTIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S%:z"


def timedelta_to_offset_string(timed: datetime.timedelta) -> str:
    """Returns a timedelta string in the format "+HH:MM" or "-HH:MM"."""

    seconds = timed.days * 24 * 60 * 60 + timed.seconds
    minutes_offset = int(seconds / 60)
    hours_offset = int(minutes_offset / 60)
    minutes_offset = abs(minutes_offset - hours_offset * 60)
    return f"{hours_offset:+03d}:{minutes_offset:02d}"


def log_print(*args, **kwargs):
    """Prints to stdout and logs the same to a log file."""
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
    else:
        lt = datetime.datetime.now().astimezone()
        time_str = lt.strftime(STRFTIME_LOCAL_FORMAT)
        log_file = "anotherAurHelper2.log"
    print(time_str, end=" ")
    with open(log_file, "a", encoding="utf-8") as lf:
        print(time_str, end=" ", file=lf)

    if "toml" in kwargs:
        del kwargs["toml"]

    print(*args, **kwargs)
    with open(log_file, "a", encoding="utf-8") as lf:
        kwargs["file"] = lf
        print(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(
        prog="AnotherAURHelper2",
        description="Builds AUR pkgs in a chroot handled by systemd-nspawn",
    )
    parser.add_argument("-c", "--config")
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


if __name__ == "__main__":
    main()
