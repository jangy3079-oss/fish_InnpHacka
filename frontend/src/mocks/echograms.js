// 시연(파일) 모드의 소나 에코그램. 시나리오별 실제 합성 Sv를 PNG로 내보낸 것이다(scripts/export_frontend_echograms.py).
import heatwave_typhoon from './echograms/heatwave_typhoon.png';
import disease_suspect from './echograms/disease_suspect.png';
import parasite_suspect from './echograms/parasite_suspect.png';
import turbidity_event from './echograms/turbidity_event.png';
import feeding_drop from './echograms/feeding_drop.png';
import meta from './echograms/echograms.json';

const IMAGES = { heatwave_typhoon, disease_suspect, parasite_suspect, turbidity_event, feeding_drop };

// 시나리오의 에코그램과, 주어진 시각이 가로축 어디(0~1)인지. 없는 시나리오는 null.
export function echogramFor(scenario, ts) {
  const m = meta[scenario];
  const url = IMAGES[scenario];
  if (!m || !url) return null;
  const at = Date.parse(ts);
  const frac = Math.min(1, Math.max(0, (at - m.start_ms) / (m.end_ms - m.start_ms)));
  return { url, ...m, frac };
}
