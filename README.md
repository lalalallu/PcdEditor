# PCD 远程图形化编辑器

基于 tkinter 的 PCD 文件编辑器，通过 SSH 连接远程服务器，按组（每 4 个点为一组）编辑 PCD 文件的 xyz 坐标。支持站点配置记忆、sudo 提权写入、自动备份和冲突检测。

## 功能特性

- **SSH 远程编辑**：通过 SSH 连接远程服务器，直接读取和编辑 PCD 文件
- **按组编辑**：PCD 点云数据每 4 个点分为一组，方便批量编辑 xyz 坐标
- **sudo 提权写入**：可配置使用 `sudo` 执行远程写入和备份操作，解决权限不足问题
- **自动备份**：保存前自动创建备份文件（`.bak_YYYYMMDD_HHMMSS`），最多保留 5 个
- **冲突检测**：保存时自动检测远程文件是否被他人修改，防止数据覆盖
- **撤销支持**：支持撤销当前组修改和撤销全部修改
- **站点记忆**：连接配置（主机、端口、用户名、密码、远程路径、sudo 设置）可保存为站点，下次快速填充

## 环境要求

- Python 3.7+
- paramiko >= 2.6.0

## 安装

```bash
# 安装依赖
pip install -r requirements.txt
```

## 使用

```bash
python3 app.py
```

### 连接远程服务器

1. 填写 SSH 连接参数：主机、端口（默认 22）、用户名、密码
2. 勾选「使用 sudo 提权写入」并填写 sudo 密码（留空则使用 SSH 密码），或取消勾选以普通用户写入
3. 填写远程 PCD 文件路径（如 `/home/user/data.pcd`）
4. 点击「连接并拉取」

连接成功后，PCD 文件内容将被解析并显示在组列表中。

### 编辑点坐标

1. 在左侧组列表中选择一个组（双击或单击后自动加载）
2. 在右侧编辑面板中修改 x、y、z 坐标
3. normal_x、normal_y、normal_z、curvature 为只读字段
4. 点击「应用修改」提交到本地缓存

### 保存到远程

1. 点击底部「保存到远程」
2. 系统自动检测冲突（远程文件是否被他人修改）
3. 确认后自动创建备份并写入新内容

### 站点配置

- **保存站点**：填写好连接参数后点击「保存当前为站点」
- **加载站点**：从下拉列表选择已保存的站点，自动填充输入框
- **删除站点**：选中站点后点击「删除站点」

站点信息保存到 `sites.json`（与 `app.py` 同目录），密码和 sudo 密码以明文存储，请注意安全。

## 项目结构

```
PcdEditor/
├── app.py              # tkinter GUI 主程序
├── ssh_handler.py      # SSH 通信层（连接、读写、备份、冲突检测）
├── pcd_parser.py       # PCD 文件解析与生成
├── requirements.txt    # Python 依赖
└── README.md
```

### 各模块说明

| 文件 | 功能 |
|------|------|
| `app.py` | tkinter GUI：连接面板、组列表、编辑面板、站点记忆 |
| `ssh_handler.py` | SSH 连接管理、sudo 命令执行、文件读写、备份管理、冲突检测 |
| `pcd_parser.py` | PCD ascii 文件解析（按 DATA ascii 分隔，每 4 个点分组）和内容生成 |

## PCD 文件格式

支持标准 PCD ascii 格式，每行一个点，字段按空格分隔。典型格式：

```
# .PCD v.7 - Point Cloud Data file format
VERSION .7
FIELDS x y z normal_x normal_y normal_z curvature
SIZE 4 4 4 4 4 4 4
TYPE F F F F F F F
COUNT 1 1 1 1 1 1 1
WIDTH 1200
HEIGHT 1
VIEWPOINT 0 0 0 1 0 0 0
POINTS 1200
DATA ascii
0.93773 0.86663 0.32476 -0.0038345 0.0027531 0.003309 0.002206
...
```

编辑器保留 PCD 头部完整不变，仅修改 `DATA ascii` 之后的坐标数据。

## 许可

内部工具，无许可限制。