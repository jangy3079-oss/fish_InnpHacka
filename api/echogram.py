"""Render a cage-scoped #7 NPZ asset, never a fabricated metric heatmap."""

import io
import json
import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from threading import Lock
from zipfile import BadZipFile, ZipFile

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from .farmer_data import data_error

_render_lock = Lock()


@dataclass(frozen=True)
class EchogramRenderer:
    root: Path
    producer_ids: dict[str, str]

    def render(self, cage_id: int, start=None, end=None) -> bytes:
        root = self.root.resolve()
        path = (root / str(cage_id) / "echogram.npz").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise data_error(404, "echogram_not_found", "No echogram asset for this cage")
        try:
            with ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
                    raise ValueError
            with np.load(path, allow_pickle=False) as asset:
                sv, ts, depth = asset["Sv"], asset["ping_time"], asset["depth"]
                metadata = json.loads(str(asset["meta"]))
            expected = self.producer_ids.get(str(cage_id), str(cage_id))
            if not isinstance(metadata, dict) or str(metadata.get("cage_id")) != expected:
                raise ValueError
            if (sv.ndim != 2 or ts.ndim != 1 or depth.ndim != 1 or sv.shape != (ts.size, depth.size)
                    or ts.size == 0 or depth.size == 0 or sv.dtype.kind not in "fi" or depth.dtype.kind not in "fi"
                    or ts.dtype.kind not in "iu" or not np.isfinite(sv).all() or not np.isfinite(depth).all()
                    or np.any(depth < 0) or np.any(np.diff(depth) <= 0) or np.any(ts[1:] <= ts[:-1])):
                raise ValueError
            # NPZ ping_time is Unix seconds UTC as written by simulator.echogram.save_npz.
            times = ts.astype("datetime64[s]")
            mask = np.ones(ts.size, dtype=bool)
            if start is not None:
                mask &= ts >= start.timestamp()
            if end is not None:
                mask &= ts <= end.timestamp()
            sv, times = sv[mask].astype(np.float64), times[mask]
        except (OSError, ValueError, TypeError, KeyError, BadZipFile, OverflowError):
            raise data_error(503, "invalid_echogram_asset", "Echogram asset is invalid or unavailable") from None
        if not times.size:
            raise data_error(404, "echogram_range_empty", "No pings in the requested time range")
        # Downsample in linear intensity space, with stable dB arithmetic.
        groups = np.array_split(np.arange(len(sv)), min(len(sv), 600))
        maxima = np.stack([sv[group].max(axis=0) for group in groups])
        display = np.stack([maximum + 10 * np.log10(np.mean(10 ** ((sv[group] - maximum) / 10), axis=0))
                            for group, maximum in zip(groups, maxima)])
        if depth.size > 300:
            bins = np.array_split(np.arange(depth.size), 300)
            display = np.stack([10 * np.log10(np.mean(10 ** ((display[:, b] - display[:, b].max(axis=1)[:, None]) / 10), axis=1))
                                + display[:, b].max(axis=1) for b in bins], axis=1)
        try:
            # Matplotlib rendering is serialized; no pyplot global figure state is used.
            import matplotlib.dates as mdates
            from matplotlib.ticker import FixedLocator
            with _render_lock:
                first, last = mdates.date2num(times[0].astype("O")), mdates.date2num(times[-1].astype("O"))
                if first == last:
                    first, last = first - 0.5 / 86400, last + 0.5 / 86400
                top, bottom = float(depth[0]), float(depth[-1])
                if top == bottom:
                    top, bottom = max(0, top - 0.05), bottom + 0.05
                figure = Figure(figsize=(6, 2), dpi=150, layout="constrained")
                canvas = FigureCanvasAgg(figure)
                axes = figure.subplots()
                image = axes.imshow(display.T, aspect="auto", origin="upper", cmap="viridis",
                    vmin=-80, vmax=-50, extent=[first, last, bottom, top], interpolation="nearest")
                axes.xaxis_date()
                axes.xaxis.set_major_locator(FixedLocator([(first + last) / 2]) if times.size == 1
                                            else mdates.AutoDateLocator(minticks=3, maxticks=5))
                time_format = "%m-%d\n%H:%M:%S" if last - first < 1 / 24 else "%m-%d\n%H:%M"
                axes.xaxis.set_major_formatter(mdates.DateFormatter(time_format, tz=timezone(timedelta(hours=9))))
                axes.set(xlabel="Time (KST)", ylabel="Depth (m)", title=f"Cage {cage_id} echogram")
                figure.colorbar(image, ax=axes, label="Sv (dB)")
                output = io.BytesIO()
                canvas.print_png(output)
                figure.clear()
                return output.getvalue()
        except (ValueError, TypeError, OverflowError, OSError):
            raise data_error(503, "echogram_render_failed", "Echogram preview is unavailable") from None


def get_echogram_renderer():
    root = os.getenv("ECHOGRAM_ROOT", "")
    try:
        mapping = json.loads(os.getenv("ECHOGRAM_PRODUCER_IDS", "{}"))
        if not root or not isinstance(mapping, dict) or any(
            not key.isdecimal() or not isinstance(value, str) or not value for key, value in mapping.items()
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise data_error(503, "echogram_not_configured", "Echogram storage is not configured") from None
    return EchogramRenderer(Path(root), mapping)
