#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2014 - 2019 Kirk Byers
# Copyright (c) 2014 - 2019 Twin Bridges Technology
# Copyright (c) 2019 NOKIA Inc.
# MIT License - See License file at:
#   https://github.com/ktbyers/netmiko/blob/develop/LICENSE

import re
import os
import time

from netmiko import log
from netmiko.base_connection import BaseConnection
from netmiko.scp_handler import BaseFileTransfer


class NokiaSrosSSH(BaseConnection):
    """
    Implement methods for interacting with Nokia SR OS devices.

    Not applicable in Nokia SR OS (disabled):
        - enable()
        - exit_enable_mode()
        - check_enable_mode()

    Overriden methods to adapt Nokia SR OS behavior (changed):
        - session_preparation()
        - set_base_prompt()
        - config_mode()
        - exit_config_mode()
        - check_config_mode()
        - save_config()
        - commit()
        - strip_prompt()
    """

    def session_preparation(self):
        self._test_channel_read()
        self.set_base_prompt()
        # "@" indicates model-driven CLI (vs Classical CLI)
        if "@" in self.base_prompt:
            self.disable_paging(command="environment more false")
            self.disable_paging(command="//environment no more")
            self.set_terminal_width(command="environment console width 512")
        else:
            self.disable_paging(command="environment no more")
            self.disable_paging(command="//environment more false")

        # Clear the read buffer
        time.sleep(0.3 * self.global_delay_factor)
        self.clear_buffer()

    def set_base_prompt(self, *args, **kwargs):
        """Remove the > when navigating into the different config level."""
        cur_base_prompt = super().set_base_prompt(*args, **kwargs)
        match = re.search(r"\*?(.*?)(>.*)*#", cur_base_prompt)
        if match:
            # strip off >... from base_prompt; strip off leading *
            self.base_prompt = match.group(1)
            return self.base_prompt

    def enable(self, *args, **kwargs):
        """Nokia SR OS does not support enable-mode"""
        return ""

    def check_enable_mode(self, *args, **kwargs):
        """Nokia SR OS does not support enable-mode"""
        return True

    def exit_enable_mode(self, *args, **kwargs):
        """Nokia SR OS does not support enable-mode"""
        return ""

    def config_mode(self, config_command="edit-config exclusive", pattern=r"\(ex\)\["):
        """Enable config edit-mode for Nokia SR OS"""
        output = ""
        # Only model-driven CLI supports config-mode
        if "@" in self.base_prompt:
            output += super().config_mode(
                config_command=config_command, pattern=pattern
            )
        return output

    def exit_config_mode(self, *args, **kwargs):
        """Disable config edit-mode for Nokia SR OS"""
        output = self._exit_all()
        # Model-driven CLI
        if "@" in self.base_prompt and "(ex)[" in output:
            # Asterisk indicates changes were made.
            if "*(ex)[" in output:
                log.warning("Uncommitted changes! Discarding changes!")
                output += self._discard()
            cmd = "quit-config"
            self.write_channel(self.normalize_cmd(cmd))
            if self.global_cmd_verify is not False:
                output += self.read_until_pattern(pattern=re.escape(cmd))
            else:
                output += self.read_until_prompt()
        if self.check_config_mode():
            raise ValueError("Failed to exit configuration mode")
        return output

    def check_config_mode(self, check_string=r"(ex)[", pattern=r"@"):
        """Check config mode for Nokia SR OS"""
        if "@" not in self.base_prompt:
            # Classical CLI
            return False
        else:
            # Model-driven CLI look for "exclusive"
            return super().check_config_mode(check_string=check_string, pattern=pattern)

    def save_config(self, *args, **kwargs):
        """Persist configuration to cflash for Nokia SR OS"""
        output = self.send_command(command_string="/admin save")
        return output

    def send_config_set(self, config_commands=None, exit_config_mode=None, **kwargs):
        """Model driven CLI requires you not exit from configuration mode."""
        if exit_config_mode is None:
            # Set to False if model-driven CLI
            exit_config_mode = False if "@" in self.base_prompt else True
        return super().send_config_set(
            config_commands=config_commands, exit_config_mode=exit_config_mode, **kwargs
        )

    def commit(self, *args, **kwargs):
        """Activate changes from private candidate for Nokia SR OS"""
        output = self._exit_all()
        if "@" in self.base_prompt and "*(ex)[" in output:
            log.info("Apply uncommitted changes!")
            cmd = "commit"
            self.write_channel(self.normalize_cmd(cmd))
            new_output = ""
            if self.global_cmd_verify is not False:
                new_output += self.read_until_pattern(pattern=re.escape(cmd))
            if "@" not in new_output:
                new_output += self.read_until_pattern(r"@")
            output += new_output
        return output

    def _exit_all(self):
        """Return to the 'root' context."""
        output = ""
        exit_cmd = "exit all"
        self.write_channel(self.normalize_cmd(exit_cmd))
        # Make sure you read until you detect the command echo (avoid getting out of sync)
        if self.global_cmd_verify is not False:
            output += self.read_until_pattern(pattern=re.escape(exit_cmd))
        else:
            output += self.read_until_prompt()
        return output

    def _discard(self):
        """Discard changes from private candidate for Nokia SR OS"""
        output = ""
        if "@" in self.base_prompt:
            cmd = "discard"
            self.write_channel(self.normalize_cmd(cmd))
            new_output = ""
            if self.global_cmd_verify is not False:
                new_output += self.read_until_pattern(pattern=re.escape(cmd))
            if "@" not in new_output:
                new_output += self.read_until_prompt()
            output += new_output
        return output

    def strip_prompt(self, *args, **kwargs):
        """Strip prompt from the output."""
        output = super().strip_prompt(*args, **kwargs)
        if "@" in self.base_prompt:
            # Remove context prompt too
            strips = r"[\r\n]*\!?\*?(\((ex|gl|pr|ro)\))?\[\S*\][\r\n]*"
            return re.sub(strips, "", output)
        else:
            return output

    def cleanup(self, command="logout"):
        """Gracefully exit the SSH session."""
        try:
            # The pattern="" forces use of send_command_timing
            if self.check_config_mode(pattern=""):
                self.exit_config_mode()
        except Exception:
            pass
        # Always try to send final 'logout'.
        self._session_log_fin = True
        self.write_channel(command + self.RETURN)


class NokiaSrosFileTransfer(BaseFileTransfer):
    def _get_cmd_prefix(self):
        """
        Returns "//" if the current prompt is MD-CLI
        empty string otherwise
        """
        return "//" if "@" in self.ssh_ctl_chan.base_prompt else ""

    def remote_space_available(self, search_pattern=r"(\d+)\s+\w+\s+free"):
        """Return space available on remote device."""

        # Sample text for search_pattern.
        # "               3 Dir(s)               961531904 bytes free."
        remote_cmd = self._get_cmd_prefix() + "file dir {}".format(self.file_system)
        remote_output = self.ssh_ctl_chan.send_command(remote_cmd)
        match = re.search(search_pattern, remote_output)
        return int(match.group(1))

    def check_file_exists(self, remote_cmd=""):
        """Check if destination file exists (returns boolean)."""

        if self.direction == "put":
            if not remote_cmd:
                remote_cmd = self._get_cmd_prefix() + "file dir {}/{}".format(
                    self.file_system, self.dest_file
                )
            remote_out = self.ssh_ctl_chan.send_command(remote_cmd)
            if "File Not Found" in remote_out:
                return False
            elif self.dest_file in remote_out:
                return True
            else:
                raise ValueError("Unexpected output from check_file_exists")
        elif self.direction == "get":
            return os.path.exists(self.dest_file)

    def remote_file_size(self, remote_cmd=None, remote_file=None):
        """Get the file size of the remote file."""

        if remote_file is None:
            if self.direction == "put":
                remote_file = self.dest_file
            elif self.direction == "get":
                remote_file = self.source_file
        if not remote_cmd:
            remote_cmd = self._get_cmd_prefix() + "file dir {}/{}".format(
                self.file_system, remote_file
            )
        remote_out = self.ssh_ctl_chan.send_command(remote_cmd)

        if "File Not Found" in remote_out:
            raise IOError("Unable to find file on remote system")

        # Parse dir output for filename. Output format is:
        # "10/16/2019  10:00p                6738 {filename}"

        pattern = r"\S+\s+\S+\s+(\d+)\s+{}".format(re.escape(remote_file))
        match = re.search(pattern, remote_out)

        if not match:
            raise ValueError("Filename entry not found in dir output")

        file_size = int(match.group(1))
        return file_size

    def process_md5(self, md5_output, pattern=r"=\s+(\S+)"):
        """ Nokia SROS does not support a md5sum calculation."""
        pass

    def verify_file(self):
        """Verify the file has been transferred correctly based on filesize."""
        if self.direction == "put":
            return os.stat(self.source_file).st_size == self.remote_file_size(
                remote_file=self.dest_file
            )
        elif self.direction == "get":
            return (
                self.remote_file_size(remote_file=self.source_file)
                == os.stat(self.dest_file).st_size
            )

    def compare_md5(self):
        """ Nokia SROS does not support a md5sum calculation.
         File verification is patched with verify_file which is based on file size."""
        return self.verify_file()
