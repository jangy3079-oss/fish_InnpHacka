import { useState } from 'react';
import { api } from '../api/client.js';
import { useDemo } from '../demo/DemoContext.jsx';
import { SCENARIOS, frameTime } from '../mocks/demo.js';
import { formatTime } from '../constants.js';

// 시연용 패널: 시나리오 선택, 재생, 시간 슬라이더. 평소에는 접어 두어 사이트 화면을 가리지 않는다.
export default function DemoBar() {
  const { demo, maxT, playing, selectScenario, setT, togglePlay, toggleFile } = useDemo();
  const [open, setOpen] = useState(false);
  if (api.live() && !demo.file) return <div className="demo"><span>실제 관측 데이터</span> <button onClick={toggleFile}>파일 시연 보기</button></div>;
  return (
    <div className="demo">
      {api.live() && demo.file && <div className="notice">파일 시연 데이터입니다. <button onClick={toggleFile}>실제 관측 보기</button></div>}
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
            <input type="range" min={0} max={maxT} value={demo.t} onChange={(e) => setT(Number(e.target.value))} aria-label="시간" />
          </div>
          <p className="demo-time">{formatTime(frameTime(demo))} 기준</p>
          {(!api.isMock || api.live()) && (
            <div className="demo-source">
              <p>{demo.file ? '지금은 파일에 든 시연 데이터를 봅니다.' : '지금은 서버 응답을 봅니다.'}</p>
              <button type="button" onClick={toggleFile}>{demo.file ? '서버 응답으로 보기' : '파일 재생으로 보기'}</button>
              {!demo.file && <small>서버가 느리거나 안 될 때 눌러 주세요. 자동으로 바뀌지 않습니다.</small>}
            </div>
          )}
        </div>
      )}
      <button className="demo-fab" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="demo-panel">
        시연 모드 {open ? '접기' : '열기'}
      </button>
    </div>
  );
}
