"""
SSH 通信层：管理 SSH 连接、远程文件读写、备份和冲突检测。
所有写入操作通过 sudo 提权执行。
"""
import base64
import re
from datetime import datetime
from typing import List, Optional

import paramiko


class SSHPermissionError(PermissionError):
    """远程文件写入权限不足时抛出的异常"""
    pass


class SSHHandler:
    def __init__(self):
        self._client = None  # type: Optional[paramiko.SSHClient]
        self._sftp = None    # type: Optional[paramiko.SFTPClient]
        self._host = ""
        self._remote_path = ""
        self._original_raw_bytes = None  # type: Optional[bytes]
        self._max_backups = 5
        self._sudo_password = ""  # sudo 密码

    # ---------- 连接管理 ----------

    def connect(self, host, port, username, password, sudo_password=""):
        # type: (str, int, str, str, str) -> None
        """建立 SSH 连接并打开 SFTP 通道。

        Args:
            host: 远程主机
            port: SSH 端口
            username: 用户名
            password: SSH 密码
            sudo_password: sudo 提权密码（留空则与 SSH 密码相同）
        """
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=10,
        )
        self._sftp = self._client.open_sftp()
        self._host = host
        self._sudo_password = sudo_password if sudo_password else password

    def disconnect(self):
        # type: () -> None
        """断开连接并清理资源。"""
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self._client = None
        self._original_raw_bytes = None
        self._remote_path = ""
        self._sudo_password = ""

    @property
    def connected(self):
        # type: () -> bool
        return self._client is not None and self._sftp is not None

    # ---------- sudo 命令执行 ----------

    def _exec_sudo(self, command, timeout=15):
        # type: (str, int) -> str
        """通过 sudo 执行远程命令并返回 stdout 文本。

        使用 sudo -S 从 stdin 接收密码。
        返回命令的标准输出。
        如果失败则抛出 SSHPermissionError。
        """
        if not self._client:
            raise RuntimeError("未连接到远程服务器")

        # 构造 sudo 命令，-S 从 stdin 读取密码
        full_cmd = "sudo -S -- bash -c '{}'".format(command.replace("'", "'\\''"))
        stdin, stdout, stderr = self._client.exec_command(full_cmd, timeout=timeout)

        # 写入 sudo 密码
        stdin.write(self._sudo_password + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()

        exit_status = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")

        if exit_status != 0:
            # 过滤 sudo 的密码提示信息
            err_lines = [l for l in err_text.splitlines()
                         if "password" not in l.lower() and l.strip()]
            msg = "\n".join(err_lines) if err_lines else err_text
            if not msg.strip():
                msg = "sudo 命令执行失败（退出码 {}）".format(exit_status)
            raise SSHPermissionError("远程写入权限不足：{}".format(msg))

        return out_text

    # ---------- 文件操作 ----------

    def read_remote_file(self, remote_path):
        # type: (str) -> str
        """读取远程文件内容（文本）。同时缓存原始字节用于冲突检测。

        读取操作通常不需要 sudo，使用 SFTP。
        """
        self._remote_path = remote_path
        raw = self._read_raw(remote_path)
        self._original_raw_bytes = raw
        return raw.decode("utf-8")

    def write_remote_file(self, content):
        # type: (str) -> None
        """通过 sudo 将文本内容写入远程文件。"""
        if not self._client:
            raise RuntimeError("未连接到远程服务器")

        # 使用 base64 编码传输，避免特殊字符问题
        data_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        command = "echo '{}' | base64 -d > '{}'".format(data_b64, self._remote_path)

        try:
            self._exec_sudo(command)
        except SSHPermissionError:
            raise
        except Exception as e:
            raise SSHPermissionError("写入远程文件失败：{}".format(e))

        # 更新缓存
        self._original_raw_bytes = content.encode("utf-8")

    def _read_raw(self, remote_path):
        # type: (str) -> bytes
        """通过 SFTP 读取远程文件原始字节。"""
        if not self._sftp:
            raise RuntimeError("未连接到远程服务器")
        try:
            with self._sftp.file(remote_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError("远程文件不存在: {}".format(remote_path))

    # ---------- 冲突检测 ----------

    def check_conflict(self):
        # type: () -> bool
        """检测远程文件是否被他人修改。"""
        if self._original_raw_bytes is None:
            return False
        try:
            current = self._read_raw(self._remote_path)
            return current != self._original_raw_bytes
        except FileNotFoundError:
            return True

    def get_remote_file_mtime(self):
        # type: () -> str
        """获取远程文件的最后修改时间。"""
        try:
            stat = self._sftp.stat(self._remote_path)
            mtime = datetime.fromtimestamp(stat.st_mtime)
            return mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "未知"

    # ---------- 备份管理 ----------

    def create_backup(self):
        # type: () -> Optional[str]
        """通过 sudo cp 创建远程文件的备份。"""
        if not self._client or not self._remote_path:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = "{}.bak_{}".format(self._remote_path, timestamp)

        try:
            command = "cp '{}' '{}'".format(self._remote_path, backup_name)
            self._exec_sudo(command)
        except SSHPermissionError:
            raise
        except Exception as e:
            raise SSHPermissionError("创建备份失败：{}".format(e))

        # 清理旧备份
        self._cleanup_old_backups()

        return backup_name

    def _cleanup_old_backups(self):
        # type: () -> None
        """通过 sudo rm 删除多余的旧备份文件。"""
        if not self._client or not self._remote_path:
            return

        remote_dir = self._remote_path.rsplit("/", 1)[0]
        base_name = self._remote_path.rsplit("/", 1)[-1]
        pattern = re.compile(rf"^{re.escape(base_name)}\.bak_\d{{8}}_\d{{6}}$")

        # 列出目录（通过 exec 避免 SFTP 权限问题）
        try:
            out = self._exec_sudo("ls '{}'".format(remote_dir))
            all_files = out.strip().splitlines()
        except Exception:
            return

        backups = [f for f in all_files if pattern.match(f)]
        backups.sort(reverse=True)

        for old in backups[self._max_backups:]:
            try:
                self._exec_sudo("rm -f '{}/{}'".format(remote_dir, old))
            except Exception:
                pass

    def get_backup_list(self):
        # type: () -> List[str]
        """获取远程当前文件的所有备份文件名列表（按时间倒序）。"""
        if not self._client or not self._remote_path:
            return []

        remote_dir = self._remote_path.rsplit("/", 1)[0]
        base_name = self._remote_path.rsplit("/", 1)[-1]
        pattern = re.compile(rf"^{re.escape(base_name)}\.bak_\d{{8}}_\d{{6}}$")

        try:
            out = self._exec_sudo("ls '{}'".format(remote_dir))
            all_files = out.strip().splitlines()
        except Exception:
            return []

        backups = [f for f in all_files if pattern.match(f)]
        backups.sort(reverse=True)
        return backups