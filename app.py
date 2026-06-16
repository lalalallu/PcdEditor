#!/usr/bin/env python3
"""
PCD 图形化编辑器 — tkinter GUI 主程序
通过 SSH 连接远程服务器，按组（每 4 个点）编辑 PCD 文件的 xyz 坐标。
支持站点配置记忆（JSON 持久化）。
所有写入操作通过 sudo 提权。
"""
import copy
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from ssh_handler import SSHHandler, SSHPermissionError
from pcd_parser import parse_pcd, groups_to_text


# 打包后 __file__ 指向临时目录，改用可执行文件所在目录
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

SITES_FILE = os.path.join(_APP_DIR, "sites.json")


class PCDEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCD 远程图形化编辑器")
        self.root.geometry("1100x780")
        self.root.minsize(960, 660)

        # Windows DPI 模糊修复
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        # 核心状态
        self.ssh = SSHHandler()
        self.document = None
        self._original_groups = None   # 深拷贝原始 groups，用于撤销
        self._selected_group_index = -1

        # 编辑面板的 Entry 变量: [[x_var, y_var, z_var] * 4]
        self._edit_entries = []

        # 组状态："未修改" / "已修改(未保存)" / "已保存"
        self._group_status = []

        # 站点配置
        self._sites = self._load_sites()  # list of dict

        # ----- 构建界面 -----
        self._build_ui()
        self._refresh_site_combo()

    # ================================================================
    #  站点配置持久化
    # ================================================================

    def _load_sites(self):
        """从 JSON 文件加载站点配置列表。"""
        try:
            if os.path.isfile(SITES_FILE):
                with open(SITES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for site in data:
                        if "_display" not in site:
                            site["_display"] = "{}@{}:{}".format(
                                site.get("username", ""),
                                site.get("host", ""),
                                site.get("port", 22),
                            )
                        # 兼容旧数据
                        if "use_sudo" not in site:
                            site["use_sudo"] = True
                        if "sudo_password" not in site:
                            site["sudo_password"] = ""
                    return data
        except Exception:
            pass
        return []

    def _save_sites(self):
        """持久化站点配置到 JSON 文件。"""
        try:
            cleaned = []
            for site in self._sites:
                s = {k: v for k, v in site.items() if k != "_display"}
                cleaned.append(s)
            with open(SITES_FILE, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _refresh_site_combo(self):
        """刷新站点下拉列表。"""
        values = [site.get("name", site.get("_display", "未命名")) for site in self._sites]
        if not values:
            values = ["(无已保存站点)"]
        self.combo_site["values"] = values
        if values:
            self.combo_site.current(0)

    def _on_site_selected(self, event=None):
        """下拉选中站点时填充输入框。"""
        sel = self.combo_site.get()
        if not sel or sel == "(无已保存站点)":
            return
        for site in self._sites:
            name = site.get("name", site.get("_display", ""))
            if name == sel:
                self.entry_host.delete(0, tk.END)
                self.entry_host.insert(0, site.get("host", ""))
                self.entry_port.delete(0, tk.END)
                self.entry_port.insert(0, str(site.get("port", 22)))
                self.entry_user.delete(0, tk.END)
                self.entry_user.insert(0, site.get("username", ""))
                self.entry_password.delete(0, tk.END)
                self.entry_password.insert(0, site.get("password", ""))
                self.entry_remote_path.delete(0, tk.END)
                self.entry_remote_path.insert(0, site.get("remote_path", ""))
                self.var_use_sudo.set(site.get("use_sudo", True))
                self.entry_sudo_password.delete(0, tk.END)
                self.entry_sudo_password.insert(0, site.get("sudo_password", ""))
                self._on_use_sudo_toggle()
                break

    def _on_save_site(self):
        """保存当前输入框内容为一个站点配置。"""
        host = self.entry_host.get().strip()
        port_str = self.entry_port.get().strip()
        username = self.entry_user.get().strip()
        password = self.entry_password.get()
        remote_path = self.entry_remote_path.get().strip()

        if not host or not username or not remote_path:
            messagebox.showwarning("信息不完整", "请至少填写主机、用户名和远程路径。")
            return

        try:
            port = int(port_str) if port_str else 22
        except ValueError:
            messagebox.showwarning("端口错误", "端口必须是整数。")
            return

        name = self._ask_site_name(host, username, port)
        if not name:
            return

        site = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "remote_path": remote_path,
            "use_sudo": self.var_use_sudo.get(),
            "sudo_password": self.entry_sudo_password.get(),
            "_display": "{}@{}:{}".format(username, host, port),
        }

        existing_idx = None
        for i, s in enumerate(self._sites):
            if s.get("name") == name:
                existing_idx = i
                break

        if existing_idx is not None:
            ok = messagebox.askyesno("站点已存在", "站点「{}」已存在，是否覆盖？".format(name))
            if not ok:
                return
            self._sites[existing_idx] = site
        else:
            self._sites.append(site)

        self._save_sites()
        self._refresh_site_combo()
        for i, n in enumerate(self.combo_site["values"]):
            if n == name:
                self.combo_site.current(i)
                break
        messagebox.showinfo("已保存", "站点「{}」已保存。".format(name))

    def _on_delete_site(self):
        """删除当前选中的站点。"""
        sel = self.combo_site.get()
        if not sel or sel == "(无已保存站点)":
            messagebox.showwarning("无站点", "没有可删除的站点。")
            return

        for i, site in enumerate(self._sites):
            name = site.get("name", site.get("_display", ""))
            if name == sel:
                ok = messagebox.askyesno("确认删除", "确定删除站点「{}」？".format(name))
                if not ok:
                    return
                del self._sites[i]
                self._save_sites()
                self._refresh_site_combo()
                for entry in (self.entry_host, self.entry_port, self.entry_user,
                              self.entry_password, self.entry_remote_path,
                              self.entry_sudo_password):
                    entry.delete(0, tk.END)
                if self.entry_port.get() == "":
                    self.entry_port.insert(0, "22")
                self.var_use_sudo.set(True)
                self._on_use_sudo_toggle()
                return

    def _ask_site_name(self, host, username, port):
        """弹窗询问站点名称。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("保存站点")
        dialog.geometry("320x120")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="站点名称:").pack(pady=(12, 4))
        entry = ttk.Entry(dialog, width=30)
        entry.insert(0, "{}@{}:{}".format(username, host, port))
        entry.pack(pady=(0, 8))
        entry.select_range(0, tk.END)
        entry.focus_set()

        result = [None]

        def on_ok():
            name = entry.get().strip()
            if not name:
                messagebox.showwarning("名称不能为空", "请输入站点名称。", parent=dialog)
                return
            result[0] = name
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack()
        ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=4)

        dialog.wait_window()
        return result[0]

    # ================================================================
    #  UI 构建
    # ================================================================

    def _build_ui(self):
        """构建全部界面控件。"""
        # ---------- 顶部：SSH 连接区 ----------
        top_frame = ttk.LabelFrame(self.root, text="SSH 连接", padding=8)
        top_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        # 站点选择行
        site_row = ttk.Frame(top_frame)
        site_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(site_row, text="已保存站点:").pack(side=tk.LEFT)
        self.combo_site = ttk.Combobox(site_row, state="readonly", width=32)
        self.combo_site.pack(side=tk.LEFT, padx=(4, 8))
        self.combo_site.bind("<<ComboboxSelected>>", self._on_site_selected)
        self.btn_save_site = ttk.Button(site_row, text="保存当前为站点", command=self._on_save_site)
        self.btn_save_site.pack(side=tk.LEFT, padx=4)
        self.btn_delete_site = ttk.Button(site_row, text="删除站点", command=self._on_delete_site)
        self.btn_delete_site.pack(side=tk.LEFT, padx=4)

        # 连接参数行
        row0 = ttk.Frame(top_frame)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="主机:").pack(side=tk.LEFT)
        self.entry_host = ttk.Entry(row0, width=18)
        self.entry_host.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row0, text="端口:").pack(side=tk.LEFT)
        self.entry_port = ttk.Entry(row0, width=6)
        self.entry_port.insert(0, "22")
        self.entry_port.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row0, text="用户名:").pack(side=tk.LEFT)
        self.entry_user = ttk.Entry(row0, width=14)
        self.entry_user.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row0, text="密码:").pack(side=tk.LEFT)
        self.entry_password = ttk.Entry(row0, width=16, show="*")
        self.entry_password.pack(side=tk.LEFT, padx=(4, 12))

        # sudo 行
        sudo_row = ttk.Frame(top_frame)
        sudo_row.pack(fill=tk.X, pady=2)
        self.var_use_sudo = tk.BooleanVar(value=True)
        self.chk_use_sudo = ttk.Checkbutton(
            sudo_row, text="使用 sudo 提权写入", variable=self.var_use_sudo,
            command=self._on_use_sudo_toggle
        )
        self.chk_use_sudo.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(sudo_row, text="sudo 密码:").pack(side=tk.LEFT)
        self.entry_sudo_password = ttk.Entry(sudo_row, width=16, show="*")
        self.entry_sudo_password.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(sudo_row, text="（留空则同 SSH 密码）", foreground="gray").pack(
            side=tk.LEFT, padx=(4, 0)
        )

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="远程路径:").pack(side=tk.LEFT)
        self.entry_remote_path = ttk.Entry(row1, width=70)
        self.entry_remote_path.pack(side=tk.LEFT, padx=(4, 12))

        self.btn_connect = ttk.Button(row1, text="连接并拉取", command=self._do_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=4)

        self.btn_disconnect = ttk.Button(row1, text="断开", command=self._do_disconnect, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=4)

        self.label_status = ttk.Label(row1, text="\u25cf 未连接", foreground="gray")
        self.label_status.pack(side=tk.LEFT, padx=12)

        # ---------- 中部：组列表 + 编辑区 ----------
        mid_frame = ttk.Frame(self.root)
        mid_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # 左侧：组列表
        list_frame = ttk.LabelFrame(mid_frame, text="点组列表（每 4 个点为一组）", padding=4)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        columns = ("组号", "坐标范围", "状态")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=14)
        self.tree.heading("组号", text="组号")
        self.tree.heading("坐标范围", text="坐标范围 (x / y)")
        self.tree.heading("状态", text="状态")
        self.tree.column("组号", width=50, anchor=tk.CENTER)
        self.tree.column("坐标范围", width=280)
        self.tree.column("状态", width=110, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_group_select)
        self.tree.bind("<Double-1>", lambda e: self._load_group_to_edit())

        # 右侧：编辑面板
        edit_frame = ttk.LabelFrame(mid_frame, text="编辑选中组", padding=8)
        edit_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self.edit_inner = ttk.Frame(edit_frame)
        self.edit_inner.pack(fill=tk.BOTH, expand=True)

        self._build_edit_panel(self.edit_inner)

        btn_edit_row = ttk.Frame(edit_frame)
        btn_edit_row.pack(fill=tk.X, pady=(8, 0))
        self.btn_apply = ttk.Button(btn_edit_row, text="应用修改", command=self._do_apply_edit, state=tk.DISABLED)
        self.btn_apply.pack(side=tk.LEFT, padx=4)
        self.btn_undo_group = ttk.Button(btn_edit_row, text="撤销本组", command=self._do_undo_group, state=tk.DISABLED)
        self.btn_undo_group.pack(side=tk.LEFT, padx=4)

        # ---------- 底部：全局操作 ----------
        bottom_frame = ttk.Frame(self.root, padding=8)
        bottom_frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        self.btn_save = ttk.Button(bottom_frame, text="保存到远程", command=self._do_save, state=tk.DISABLED)
        self.btn_save.pack(side=tk.LEFT, padx=4)

        self.btn_undo_all = ttk.Button(bottom_frame, text="撤销全部修改", command=self._do_undo_all, state=tk.DISABLED)
        self.btn_undo_all.pack(side=tk.LEFT, padx=4)

        self.btn_refresh = ttk.Button(bottom_frame, text="刷新", command=self._do_refresh, state=tk.DISABLED)
        self.btn_refresh.pack(side=tk.LEFT, padx=4)

        self.label_info = ttk.Label(bottom_frame, text="")
        self.label_info.pack(side=tk.RIGHT, padx=8)

    def _on_use_sudo_toggle(self):
        """sudo 复选框切换时更新密码输入框状态。"""
        if self.var_use_sudo.get():
            self.entry_sudo_password.configure(state="normal")
        else:
            self.entry_sudo_password.configure(state="disabled")

    def _build_edit_panel(self, parent):
        """构建编辑面板（4 个点，每个点的 xyz 可编辑，其余只读）。"""
        for widget in parent.winfo_children():
            widget.destroy()

        self._edit_entries = []

        for pt_idx in range(4):
            pt_frame = ttk.LabelFrame(parent, text="点 {}".format(pt_idx + 1), padding=4)
            pt_frame.pack(fill=tk.X, pady=2)

            # 第一行：xyz 可编辑
            row1 = ttk.Frame(pt_frame)
            row1.pack(fill=tk.X)

            vars_pt = []
            for label_text in ("x", "y", "z"):
                ttk.Label(row1, text="{}:".format(label_text)).pack(side=tk.LEFT, padx=(2, 2))
                var = tk.StringVar()
                entry = ttk.Entry(row1, textvariable=var, width=14)
                entry.pack(side=tk.LEFT, padx=(0, 6))
                vars_pt.append(var)

            # 第二行：normal_x/y/z、curvature 只读
            row2 = ttk.Frame(pt_frame)
            row2.pack(fill=tk.X, pady=(2, 0))

            for label_text in ("normal_x", "normal_y", "normal_z", "curvature"):
                ttk.Label(row2, text="{}:".format(label_text)).pack(side=tk.LEFT, padx=(2, 2))
                lbl = ttk.Label(row2, text="0", width=12, relief=tk.SUNKEN, anchor=tk.CENTER)
                lbl.pack(side=tk.LEFT, padx=(0, 4))

            self._edit_entries.append(vars_pt)

        self._set_edit_panel_state("disabled")

    def _set_edit_panel_state(self, state):
        """设置编辑面板所有 Entry 的启用/禁用状态。"""
        for child in self.edit_inner.winfo_children():
            if isinstance(child, ttk.LabelFrame):
                for sub in child.winfo_children():
                    if isinstance(sub, ttk.Frame):
                        for w in sub.winfo_children():
                            if isinstance(w, ttk.Entry):
                                w.configure(state=state)

    # ================================================================
    #  SSH 操作
    # ================================================================

    def _do_connect(self):
        """建立 SSH 连接并拉取远程文件。"""
        host = self.entry_host.get().strip()
        port_str = self.entry_port.get().strip()
        username = self.entry_user.get().strip()
        password = self.entry_password.get()
        remote_path = self.entry_remote_path.get().strip()
        use_sudo = self.var_use_sudo.get()
        sudo_password = self.entry_sudo_password.get()

        if not all([host, port_str, username, remote_path]):
            messagebox.showwarning("输入不完整", "请填写主机、端口、用户名和远程路径。")
            return

        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("端口错误", "端口必须是整数。")
            return

        try:
            self.ssh.connect(
                host, port, username, password,
                sudo_password=sudo_password if use_sudo else ""
            )
            self.label_status.config(text="\u25cf 已连接，正在拉取文件...", foreground="orange")
            self.root.update()

            content = self.ssh.read_remote_file(remote_path)
            self.document = parse_pcd(content)

            self._original_groups = copy.deepcopy(self.document.groups)
            self._group_status = ["未修改"] * len(self.document.groups)
            self._selected_group_index = -1

            self._populate_tree()

            self._set_connected_ui(True)
            self.label_status.config(text="\u25cf 已连接", foreground="green")
            self.label_info.config(
                text="共 {} 组，{} 个点".format(len(self.document.groups), self.document.total_points)
            )

        except Exception as e:
            messagebox.showerror("连接失败", "SSH 连接或文件读取失败：\n{}".format(e))
            self._do_disconnect(silent=True)

    def _do_disconnect(self, silent=False):
        """断开 SSH 连接。"""
        self.ssh.disconnect()
        self.document = None
        self._original_groups = None
        self._group_status = []
        self._selected_group_index = -1

        for item in self.tree.get_children():
            self.tree.delete(item)

        self._clear_edit_panel()
        self._set_edit_panel_state("disabled")

        self._set_connected_ui(False)
        self.label_status.config(text="\u25cf 未连接", foreground="gray")
        self.label_info.config(text="")

        if not silent:
            messagebox.showinfo("已断开", "SSH 连接已断开。")

    def _set_connected_ui(self, connected):
        """根据连接状态启用/禁用控件。"""
        state_conn = tk.NORMAL if connected else tk.DISABLED
        self.btn_disconnect.config(state=state_conn)
        self.btn_refresh.config(state=state_conn)
        self.btn_save.config(state=state_conn)
        self.btn_undo_all.config(state=state_conn)
        self.entry_remote_path.config(state=tk.DISABLED if connected else tk.NORMAL)
        self.btn_connect.config(state=tk.DISABLED if connected else tk.NORMAL)

    def _do_refresh(self):
        """重新从远程拉取文件（丢弃所有本地修改）。"""
        if not self.ssh.connected:
            return
        if self._has_unsaved_changes():
            ok = messagebox.askyesno("确认刷新", "有未保存的修改，刷新将丢失所有本地更改。确定继续？")
            if not ok:
                return
        try:
            content = self.ssh.read_remote_file(self.entry_remote_path.get().strip())
            self.document = parse_pcd(content)
            self._original_groups = copy.deepcopy(self.document.groups)
            self._group_status = ["未修改"] * len(self.document.groups)
            self._selected_group_index = -1
            self._populate_tree()
            self._clear_edit_panel()
            self._set_edit_panel_state("disabled")
            self.btn_apply.config(state=tk.DISABLED)
            self.btn_undo_group.config(state=tk.DISABLED)
            self.label_info.config(text="已刷新")
        except Exception as e:
            messagebox.showerror("刷新失败", str(e))

    # ================================================================
    #  组列表
    # ================================================================

    def _populate_tree(self):
        """根据当前 document 填充 Treeview。"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.document:
            return

        for group in self.document.groups:
            xs = [p.x for p in group.points]
            ys = [p.y for p in group.points]
            x_range = "x: {:.3f} ~ {:.3f}".format(min(xs), max(xs))
            y_range = "y: {:.3f} ~ {:.3f}".format(min(ys), max(ys))
            range_str = "{}    {}".format(x_range, y_range)
            status = self._group_status[group.index]
            self.tree.insert(
                "", tk.END,
                iid=str(group.index),
                values=(group.index, range_str, status),
            )

    def _on_group_select(self, event):
        """Treeview 选中变化时更新编辑面板。"""
        selection = self.tree.selection()
        if not selection:
            return
        self._load_group_to_edit()

    def _load_group_to_edit(self):
        """将当前选中组的数据加载到编辑面板。"""
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        if not self.document or idx >= len(self.document.groups):
            return

        self._selected_group_index = idx
        group = self.document.groups[idx]

        for pt_idx in range(4):
            if pt_idx < len(group.points):
                p = group.points[pt_idx]
                self._edit_entries[pt_idx][0].set(str(p.x))
                self._edit_entries[pt_idx][1].set(str(p.y))
                self._edit_entries[pt_idx][2].set(str(p.z))
            else:
                for var in self._edit_entries[pt_idx]:
                    var.set("")

        self._update_readonly_labels(group)

        self._set_edit_panel_state("normal")
        self.btn_apply.config(state=tk.NORMAL)
        self.btn_undo_group.config(state=tk.NORMAL)

    def _update_readonly_labels(self, group):
        """更新编辑面板中 normal/curvature 只读 Label 的显示值。"""
        for pt_idx, pt_frame in enumerate(self.edit_inner.winfo_children()):
            if not isinstance(pt_frame, ttk.LabelFrame):
                continue
            if pt_idx >= len(group.points):
                for row_widget in pt_frame.winfo_children():
                    if isinstance(row_widget, ttk.Frame):
                        for w in row_widget.winfo_children():
                            if isinstance(w, ttk.Label) and str(w.cget("relief")) == "sunken":
                                w.config(text="")
                continue

            p = group.points[pt_idx]
            values = [
                str(p.normal_x),
                str(p.normal_y),
                str(p.normal_z),
                str(p.curvature),
            ]
            vi = 0
            for row_widget in pt_frame.winfo_children():
                if isinstance(row_widget, ttk.Frame):
                    for w in row_widget.winfo_children():
                        if isinstance(w, ttk.Label) and str(w.cget("relief")) == "sunken":
                            if vi < len(values):
                                w.config(text=values[vi])
                                vi += 1

    def _clear_edit_panel(self):
        """清空编辑面板。"""
        for pt_vars in self._edit_entries:
            for var in pt_vars:
                var.set("")
        self._selected_group_index = -1
        self._set_edit_panel_state("disabled")
        self.btn_apply.config(state=tk.DISABLED)
        self.btn_undo_group.config(state=tk.DISABLED)

    # ================================================================
    #  编辑操作
    # ================================================================

    def _do_apply_edit(self):
        """将编辑面板中的 xyz 值应用回 document.groups。"""
        if self._selected_group_index < 0 or not self.document:
            return

        idx = self._selected_group_index
        group = self.document.groups[idx]

        try:
            for pt_idx in range(len(group.points)):
                x_str = self._edit_entries[pt_idx][0].get().strip()
                y_str = self._edit_entries[pt_idx][1].get().strip()
                z_str = self._edit_entries[pt_idx][2].get().strip()
                if not x_str or not y_str or not z_str:
                    raise ValueError("点 {} 的 x/y/z 不能为空".format(pt_idx + 1))
                group.points[pt_idx].x = float(x_str)
                group.points[pt_idx].y = float(y_str)
                group.points[pt_idx].z = float(z_str)
        except ValueError as e:
            messagebox.showwarning("数值错误", "输入无效：{}".format(e))
            return

        self._group_status[idx] = "已修改(未保存)"
        self._update_tree_status(idx)
        self._update_readonly_labels(group)
        self.btn_undo_all.config(state=tk.NORMAL)

    def _do_undo_group(self):
        """撤销当前选中组的修改。"""
        if self._selected_group_index < 0 or not self.document or not self._original_groups:
            return

        idx = self._selected_group_index
        original = copy.deepcopy(self._original_groups[idx])
        self.document.groups[idx] = original
        self._group_status[idx] = "未修改"
        self._update_tree_status(idx)
        self._load_group_to_edit()

        if not self._has_unsaved_changes():
            self.btn_undo_all.config(state=tk.DISABLED)

    def _do_undo_all(self):
        """撤销所有修改。"""
        if not self.document or not self._original_groups:
            return
        ok = messagebox.askyesno("确认撤销", "确定撤销所有修改？")
        if not ok:
            return
        self.document.groups = copy.deepcopy(self._original_groups)
        self._group_status = ["未修改"] * len(self.document.groups)
        self._populate_tree()
        self._clear_edit_panel()
        self.btn_undo_all.config(state=tk.DISABLED)

    # ================================================================
    #  保存操作（含备份失败时跳过备份的逻辑）
    # ================================================================

    def _do_save(self):
        """保存修改到远程文件（含冲突检测和备份）。

        备份失败时询问用户是否跳过备份继续保存。
        """
        if not self.document or not self.ssh.connected:
            return

        modified_count = sum(1 for s in self._group_status if s == "已修改(未保存)")
        if modified_count == 0:
            messagebox.showinfo("无需保存", "没有需要保存的修改。")
            return

        # 冲突检测
        if self.ssh.check_conflict():
            mtime = self.ssh.get_remote_file_mtime()
            ok = messagebox.askyesno(
                "冲突警告",
                "远程文件已被他人修改！\n修改时间: {}\n\n"
                "选择「是」强制覆盖，选择「否」取消保存。".format(mtime),
            )
            if not ok:
                return

        # 确认保存
        confirm_msg = "共修改了 {} 组，确认保存到远程？".format(modified_count)
        if self.var_use_sudo.get():
            confirm_msg += "\n\n将使用 sudo 提权写入文件并创建备份（最多保留 5 个）。"
        else:
            confirm_msg += "\n\n保存前将自动创建备份（最多保留 5 个）。"
        ok = messagebox.askyesno("确认保存", confirm_msg)
        if not ok:
            return

        # 创建备份（如果失败则询问是否跳过）
        backup_done = False
        try:
            backup_name = self.ssh.create_backup()
            if backup_name:
                self.label_info.config(
                    text="备份已创建: {}".format(backup_name.split("/")[-1])
                )
                self.root.update()
                backup_done = True
        except SSHPermissionError as e:
            skip = messagebox.askyesno(
                "备份失败",
                "{}\n\n"
                "是否跳过备份，直接保存文件？\n"
                "（注意：无法恢复修改前的文件）".format(str(e)),
            )
            if not skip:
                return
            self.label_info.config(text="已跳过备份（权限不足）")
            self.root.update()

        # 生成内容并写入
        try:
            new_content = groups_to_text(self.document)
            self.ssh.write_remote_file(new_content)
        except SSHPermissionError as e:
            messagebox.showerror("保存失败", str(e))
            return
        except Exception as e:
            messagebox.showerror("保存失败", "写入远程文件失败：\n{}".format(e))
            return

        # 更新状态
        self._original_groups = copy.deepcopy(self.document.groups)
        self._group_status = ["未修改"] * len(self.document.groups)
        self._populate_tree()
        self.btn_undo_all.config(state=tk.DISABLED)
        self.label_info.config(text="已保存到远程")
        messagebox.showinfo("保存成功", "文件已保存到远程服务器。")

    # ================================================================
    #  工具方法
    # ================================================================

    def _update_tree_status(self, group_index):
        """更新 Treeview 中指定行的状态列。"""
        self.tree.set(str(group_index), "状态", self._group_status[group_index])

    def _has_unsaved_changes(self):
        return any(s == "已修改(未保存)" for s in self._group_status)


# ================================================================
#  入口
# ================================================================

def main():
    root = tk.Tk()
    PCDEditorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()