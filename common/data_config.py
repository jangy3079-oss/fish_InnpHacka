"""One validated configuration snapshot for a data batch; no implicit defaults."""

from dataclasses import dataclass
from common.config_loader import ConfigLoader, ConfigSnapshot


@dataclass(frozen=True)
class FixedSnapshot:
    value: ConfigSnapshot

    def snapshot(self):
        return self.value


def snapshot_or_current(snapshot=None):
    return ConfigLoader().snapshot() if snapshot is None else snapshot


def fusion_parameters(snapshot):
    t = snapshot['thresholds']
    return {'q_min': t['fusion']['gate_low'], 'q_full': t['fusion']['full_at'],
            'w_max': t['fusion']['max_camera_weight'], 'q_camera_only': t['quality']['camera_only_min']}
