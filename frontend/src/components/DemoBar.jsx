import { useDemo } from '../demo/DemoContext.jsx';
import { MAX_T, SCENARIOS } from '../mocks/demo.js';
import { formatTime } from '../constants.js';

const START = Date.UTC(2026, 7, 11, 6, 0);

// 시연용 조작줄: 시나리오 선택, 재생, 시간 슬라이더.
export default function DemoBar() {
  const { demo, playing, selectScenario, setT, togglePlay } = useDemo();
  return (
    <div className="demobar">
      <label>
        시나리오
        <select value={demo.scenario} onChange={(e) => selectScenario(e.target.value)}>
          {SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>{s.label}</option>
          ))}
        </select>
      </label>
      <button onClick={togglePlay} aria-label={playing ? '일시정지' : '재생'}>{playing ? '❚❚' : '▶'}</button>
      <input
        type="range"
        min={0}
        max={MAX_T}
        value={demo.t}
        onChange={(e) => setT(Number(e.target.value))}
        aria-label="시간"
      />
      <span className="demotime">{formatTime(new Date(START + demo.t * 3600000).toISOString())}</span>
    </div>
  );
}
