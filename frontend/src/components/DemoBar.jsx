import { useState } from 'react';
import { useDemo } from '../demo/DemoContext.jsx';
import { MAX_T, SCENARIOS } from '../mocks/demo.js';
import { formatTime } from '../constants.js';

const START = Date.UTC(2026, 7, 11, 6, 0);

// 시연용 패널: 시나리오 선택, 재생, 시간 슬라이더. 평소에는 접어 두어 사이트 화면을 가리지 않는다.
export default function DemoBar() {
  const { demo, playing, selectScenario, setT, togglePlay } = useDemo();
  const [open, setOpen] = useState(false);
  return (
    <div className="demo">
      {open && (
        <div className="demo-panel" id="demo-panel">
          <label>
            시나리오
            <select value={demo.scenario} onChange={(e) => selectScenario(e.target.value)}>
              {SCENARIOS.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
          </label>
          <div className="demo-row">
            <button onClick={togglePlay} aria-label={playing ? '일시정지' : '재생'}>{playing ? '❚❚' : '▶'}</button>
            <input type="range" min={0} max={MAX_T} value={demo.t} onChange={(e) => setT(Number(e.target.value))} aria-label="시간" />
          </div>
          <p className="demo-time">{formatTime(new Date(START + demo.t * 3600000).toISOString())} 기준</p>
        </div>
      )}
      <button className="demo-fab" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="demo-panel">
        시연 모드 {open ? '접기' : '열기'}
      </button>
    </div>
  );
}
