#!/usr/bin/env python3

import argparse
import datetime
import getpass
import subprocess
import sys
import tomllib

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
            if len(user_input) == 0:
                return opts[0]
            for idx in range(len(opts)):
                if opts[idx][0] == user_input[0]:
                    return opts[idx]
        except KeyboardInterrupt:
            return "interrupt"
        except:
            continue


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


if __name__ == "__main__":
    main()
