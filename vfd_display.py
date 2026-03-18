from __future__ import annotations

from copy import deepcopy

LINE_WIDTH = 20


def get_vfd_char_width(char: str, special_chars: dict[str, bytes] | None = None) -> int:
    special_chars = special_chars or {}
    if char in special_chars:
        return len(special_chars[char])
    return len(str(char).encode('cp866', errors='replace'))


def get_vfd_text_width(text: str, special_chars: dict[str, bytes] | None = None) -> int:
    return sum(get_vfd_char_width(char, special_chars) for char in str(text))


def trim_vfd_text(text: str, width: int = LINE_WIDTH, special_chars: dict[str, bytes] | None = None) -> str:
    result = []
    current_width = 0
    for char in str(text):
        char_width = get_vfd_char_width(char, special_chars)
        if current_width + char_width > width:
            break
        result.append(char)
        current_width += char_width
    return ''.join(result)


def fit_text(text: str, width: int = LINE_WIDTH, align: str = 'left', special_chars: dict[str, bytes] | None = None) -> str:
    text = trim_vfd_text(text, width=width, special_chars=special_chars)
    padding = max(0, width - get_vfd_text_width(text, special_chars))
    if align == 'center':
        left = padding // 2
        right = padding - left
        return (' ' * left) + text + (' ' * right)
    if align == 'right':
        return (' ' * padding) + text
    return text + (' ' * padding)


def encode_vfd_text(text: str, special_chars: dict[str, bytes] | None = None) -> bytes:
    special_chars = special_chars or {}
    encoded = bytearray()
    for char in str(text):
        if char in special_chars:
            encoded.extend(special_chars[char])
            continue
        encoded.extend(char.encode('cp866', errors='replace'))
    return bytes(encoded)


def fmt_v(value: float) -> str:
    mb = value / 1024 / 1024
    if mb >= 999.5:
        return f"{mb / 1024:.1f}G"
    return f"{int(round(mb)):03d}M"


def get_metric_templates(metric_formats: dict, default_metric_formats: dict, metric_name: str) -> list[str]:
    templates = metric_formats.get(metric_name, default_metric_formats.get(metric_name, []))
    if not isinstance(templates, list):
        templates = default_metric_formats.get(metric_name, [])
    return [str(item) for item in templates if str(item).strip()]


def apply_template(template: str, **kwargs) -> str:
    try:
        return str(template).format(**kwargs)
    except Exception:
        return ''


def build_usage_options(metric_formats: dict, default_metric_formats: dict, metric_name: str, full_prefix: str, short_prefix: str, percent, temp, show_usage: bool, show_temp: bool, degree_char: str) -> list[str]:
    options = []
    for template in get_metric_templates(metric_formats, default_metric_formats, metric_name):
        rendered = apply_template(
            template,
            usage=percent if percent is not None else 0,
            temp=temp if temp is not None else 0,
            full_prefix=full_prefix,
            short_prefix=short_prefix,
            degree=degree_char,
        )
        if not rendered:
            continue
        if not show_usage and '%' in rendered:
            continue
        if not show_temp and degree_char in rendered:
            continue
        if rendered not in options:
            options.append(rendered)

    filtered = []
    for candidate in options:
        has_percent = '%' in candidate
        has_temp = degree_char in candidate
        if show_usage and percent is not None and show_temp and temp is not None:
            if has_percent and has_temp:
                filtered.append(candidate)
        elif show_usage and percent is not None:
            if has_percent and not has_temp:
                filtered.append(candidate)
        elif show_temp and temp is not None:
            if has_temp and not has_percent:
                filtered.append(candidate)
    if filtered:
        return filtered
    return options


def render_segments(segment_options: list[list[str]], width: int = LINE_WIDTH, separator: str = ' ', compact_separator: str = '', special_chars: dict[str, bytes] | None = None) -> str:
    if not segment_options:
        return fit_text('', width, align='center', special_chars=special_chars)

    indexes = [0] * len(segment_options)
    while True:
        text = separator.join(options[index] for options, index in zip(segment_options, indexes))
        if get_vfd_text_width(text, special_chars) <= width:
            return fit_text(text, width, align='center', special_chars=special_chars)

        candidate = None
        best_delta = 0
        for idx, options in enumerate(segment_options):
            if indexes[idx] >= len(options) - 1:
                continue
            current_length = get_vfd_text_width(options[indexes[idx]], special_chars)
            next_length = get_vfd_text_width(options[indexes[idx] + 1], special_chars)
            delta = current_length - next_length
            if delta > best_delta:
                best_delta = delta
                candidate = idx

        if candidate is None:
            compact = compact_separator.join(options[-1] for options in segment_options)
            return fit_text(compact, width, align='center', special_chars=special_chars)

        indexes[candidate] += 1


def build_primary_segments(cfg: dict, default_metric_formats: dict, cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent, degree_char: str) -> list[list[str]]:
    segments = []
    cpu_options = build_usage_options(
        cfg.get("metric_formats", {}),
        default_metric_formats,
        'cpu',
        'CPU',
        'C',
        cpu_percent,
        cpu_temp,
        cfg.get("show_cpu_usage", True),
        cfg.get("show_cpu_temp", True),
        degree_char,
    )
    if cpu_options:
        segments.append(cpu_options)

    gpu_options = build_usage_options(
        cfg.get("metric_formats", {}),
        default_metric_formats,
        'gpu',
        'GPU',
        'G',
        gpu_percent,
        gpu_temp,
        cfg.get("show_gpu_usage", True),
        cfg.get("show_gpu_temp", True),
        degree_char,
    )
    if gpu_options:
        segments.append(gpu_options)

    if cfg.get("show_ram", True):
        ram_options = []
        for template in get_metric_templates(cfg.get("metric_formats", {}), default_metric_formats, "ram"):
            rendered = apply_template(template, value=ram_percent)
            if rendered and rendered not in ram_options:
                ram_options.append(rendered)
        if ram_options:
            segments.append(ram_options)
    return segments


def build_io_segments(cfg: dict, default_metric_formats: dict, disk_read, disk_write, net_in, net_out) -> list[list[str]]:
    segments = []
    if cfg.get("show_disk", True):
        disk_options = []
        for template in get_metric_templates(cfg.get("metric_formats", {}), default_metric_formats, "disk"):
            rendered = apply_template(template, read=fmt_v(disk_read), write=fmt_v(disk_write))
            if rendered and rendered not in disk_options:
                disk_options.append(rendered)
        if disk_options:
            segments.append(disk_options)
    if cfg.get("show_network", True):
        network_options = []
        for template in get_metric_templates(cfg.get("metric_formats", {}), default_metric_formats, "network"):
            rendered = apply_template(template, recv=fmt_v(net_in), send=fmt_v(net_out))
            if rendered and rendered not in network_options:
                network_options.append(rendered)
        if network_options:
            segments.append(network_options)
    return segments


def build_line1(cfg: dict, default_line_spacing: dict, default_metric_formats: dict, special_chars: dict[str, bytes], degree_char: str, cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent, width: int = LINE_WIDTH) -> str:
    spacing = deepcopy(default_line_spacing)
    spacing.update(cfg.get("line_spacing", {}) if isinstance(cfg.get("line_spacing"), dict) else {})
    return render_segments(
        build_primary_segments(cfg, default_metric_formats, cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent, degree_char),
        width=width,
        separator=spacing.get("primary", " "),
        compact_separator=spacing.get("primary_compact", ""),
        special_chars=special_chars,
    )


def build_line2(cfg: dict, default_line_spacing: dict, default_metric_formats: dict, special_chars: dict[str, bytes], disk_read, disk_write, net_in, net_out, width: int = LINE_WIDTH) -> str:
    spacing = deepcopy(default_line_spacing)
    spacing.update(cfg.get("line_spacing", {}) if isinstance(cfg.get("line_spacing"), dict) else {})
    return render_segments(
        build_io_segments(cfg, default_metric_formats, disk_read, disk_write, net_in, net_out),
        width=width,
        separator=spacing.get("secondary", " "),
        compact_separator=spacing.get("secondary_compact", ""),
        special_chars=special_chars,
    )
