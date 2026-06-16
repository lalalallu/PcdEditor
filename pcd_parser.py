"""
PCD 解析层：解析 PCD ascii 文件，按每 4 个点分组，支持生成保持原格式的输出。
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class Point:
    """PCD 中的一个点。"""
    x: float
    y: float
    z: float
    normal_x: float
    normal_y: float
    normal_z: float
    curvature: float
    raw_line: str = ""          # 保留原始行文本，用于回写时保持精度
    field_count: int = 7        # 字段数，用于生成时对齐


@dataclass
class Group:
    """一组点（通常是 4 个，最后一组可能不足 4 个）。"""
    index: int
    points: List[Point]


@dataclass
class PCDDocument:
    """解析后的 PCD 文档。"""
    headers: List[str]          # DATA ascii 之前的所有行（包含 DATA ascii 本身）
    groups: List[Group]
    total_points: int           # POINTS 字段值
    field_count: int            # FIELDS 数量
    original_raw: bytes = b""   # 原始字节（用于冲突检测等）


# ---------- 解析 ----------

def parse_pcd(text: str) -> PCDDocument:
    """解析 PCD ascii 文本内容。

    Args:
        text: 完整的文件内容字符串。

    Returns:
        PCDDocument 对象。
    """
    lines = text.splitlines()

    # 分离头部和数据行
    data_ascii_index = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("DATA"):
            data_ascii_index = i
            break

    if data_ascii_index == -1:
        raise ValueError("未找到 DATA ascii 标记行")

    headers = lines[: data_ascii_index + 1]  # 包含 DATA ascii 行
    data_lines = lines[data_ascii_index + 1:]

    # 从头部提取关键信息
    total_points = 0
    field_count = 0
    for line in headers:
        stripped = line.strip()
        if stripped.startswith("POINTS"):
            total_points = int(stripped.split()[-1])
        elif stripped.startswith("FIELDS"):
            field_count = len(stripped.split()) - 1  # 减掉 "FIELDS" 本身

    if total_points == 0:
        raise ValueError("无法从头部解析 POINTS 数量")

    # 解析数据行
    points: List[Point] = []
    for line in data_lines:
        stripped = line.strip()
        if not stripped:
            continue  # 跳过空行
        parts = stripped.split()
        if len(parts) < field_count:
            continue  # 跳过格式不全的行

        # 前三个始终是 x y z
        x = float(parts[0])
        y = float(parts[1])
        z = float(parts[2])

        # 剩余字段：可能是 normal_x, normal_y, normal_z, curvature（或更多）
        nx, ny, nz, curv = 0.0, 0.0, 0.0, 0.0
        if len(parts) >= 4:
            nx = float(parts[3]) if len(parts) > 3 else 0.0
        if len(parts) >= 5:
            ny = float(parts[4]) if len(parts) > 4 else 0.0
        if len(parts) >= 6:
            nz = float(parts[5]) if len(parts) > 5 else 0.0
        if len(parts) >= 7:
            curv = float(parts[6]) if len(parts) > 6 else 0.0

        point = Point(
            x=x, y=y, z=z,
            normal_x=nx, normal_y=ny, normal_z=nz,
            curvature=curv,
            raw_line=line,
            field_count=field_count,
        )
        points.append(point)

    # 校验点数
    if len(points) < total_points:
        raise ValueError(
            f"实际数据行数 ({len(points)}) 少于头部声明的 POINTS ({total_points})"
        )

    # 每 4 个点分组
    groups = _build_groups(points)

    return PCDDocument(
        headers=headers,
        groups=groups,
        total_points=total_points,
        field_count=field_count,
    )


def _build_groups(points: List[Point]) -> List[Group]:
    """将点列表按每 4 个分为一组。最后一组不足 4 个自成一组。"""
    groups = []
    group_size = 4
    for i in range(0, len(points), group_size):
        chunk = points[i: i + group_size]
        groups.append(Group(index=len(groups), points=chunk))
    return groups


# ---------- 生成 ----------

def groups_to_text(document: PCDDocument) -> str:
    """将 PCDDocument 重新组装为完整的 PCD 文本，保持头部原样。

    Args:
        document: 包含 headers 和 groups 的文档对象。

    Returns:
        完整的 PCD 文件内容字符串。
    """
    lines: List[str] = []

    # 输出头部原样
    for header_line in document.headers:
        lines.append(header_line)

    # 输出所有组的点数据
    for group in document.groups:
        for point in group.points:
            # 根据字段数生成行
            if document.field_count == 7:
                lines.append(
                    f"{_format_value(point.x, point.raw_line, 0)} "
                    f"{_format_value(point.y, point.raw_line, 1)} "
                    f"{_format_value(point.z, point.raw_line, 2)} "
                    f"{_format_value(point.normal_x, point.raw_line, 3)} "
                    f"{_format_value(point.normal_y, point.raw_line, 4)} "
                    f"{_format_value(point.normal_z, point.raw_line, 5)} "
                    f"{_format_value(point.curvature, point.raw_line, 6)}"
                )
            else:
                # 回退：直接重新格式化所有字段
                parts = point.raw_line.split()
                if len(parts) >= 3:
                    parts[0] = _format_value(point.x, point.raw_line, 0)
                    parts[1] = _format_value(point.y, point.raw_line, 1)
                    parts[2] = _format_value(point.z, point.raw_line, 2)
                lines.append(" ".join(parts))

    return "\n".join(lines) + "\n"


def _format_value(value: float, raw_line: str, field_index: int) -> str:
    """根据原始行中对应字段的格式，输出相同精度的字符串。

    Args:
        value: 当前 float 值。
        raw_line: 该点对应的原始行。
        field_index: 该字段在行中的位置（0-based）。

    Returns:
        格式化后的字符串。
    """
    if not raw_line:
        return str(value)

    parts = raw_line.split()
    if field_index >= len(parts):
        return str(value)

    original_str = parts[field_index]

    # 如果原始值是整数形式，保持整数形式
    if "." not in original_str and "e" not in original_str.lower():
        # 检查当前值是否为整数值
        if value == int(value):
            return str(int(value))
        # 否则用小数点格式
        return _format_float_precision(value, original_str)

    # 分析原始值的小数位数
    if "." in original_str:
        # 去除可能的末尾符号
        clean = original_str.rstrip()
        if "." in clean:
            decimal_places = len(clean.split(".")[1])
            return f"{value:.{decimal_places}f}"

    return str(value)


def _format_float_precision(value: float, original: str) -> str:
    """根据原始字符串推断精度并格式化。"""
    if "." in original:
        decimal_places = len(original.split(".")[1])
        return f"{value:.{decimal_places}f}"
    return str(value)