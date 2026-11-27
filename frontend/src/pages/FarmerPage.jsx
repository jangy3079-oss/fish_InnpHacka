import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import EvidenceCharts from '../components/EvidenceCharts.jsx';
import { LEVELS, SOURCE_LABELS, formatTime } from '../constants.js';

const CAGE_ID = 3;

// 경고·위험이면 현재 시점을 경보 시점으로 표시한다. (과거 경보 이력 API는 아직 없음)
const isAlert = (s) => s.level === '경고' || s.level === '위험';

export default function FarmerPage() {
  const [status, setStatus] = useState(null);
  const [rec, setRec] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.getStatus(CAGE_ID), api.getRecommendation(CAGE_ID)])
      .then(([s, r]) => {
        setStatus(s);
        setRec(r);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error big">불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>;
  if (!status || !rec) return <p className="big">불러오는 중…</p>;

  return (
    <div className="farmer">
      <LevelCard status={status} />
      <SourceNotice status={status} />
      <CauseList causes={status.cause} />
      <Recommendation rec={rec} />
      <EvidenceCharts cageId={CAGE_ID} alertAt={isAlert(status) ? status.ts : undefined} />
    </div>
  );
}

function LevelCard({ status }) {
  if (status.baseline_status === 'warming_up' || status.level == null) {
    return (
      <section className="card level" style={{ background: '#eef2f6' }}>
        <h1>가두리 {status.cage_id}번</h1>
        <p className="level-name">준비 중</p>
        <p>평소 상태를 파악하는 중입니다. 잠시만 기다려 주세요.</p>
      </section>
    );
  }
  const lv = LEVELS[status.level];
  return (
    <section className="card level" style={{ background: lv.bg, color: lv.color }}>
      <h1>가두리 {status.cage_id}번</h1>
      <p className="level-name">
        <span aria-hidden="true">{lv.icon}</span> {status.level}
      </p>
      <p>{lv.message}</p>
      <p className="score">
        스트레스 지수 <strong>{Math.round(status.stress_score)}</strong> / 100
      </p>
      <p className="time">{formatTime(status.ts)} 기준</p>
    </section>
  );
}

// 근거 센서가 안 보여도 경보 단계는 유지된다(#61). 그 사실을 알려준다.
function SourceNotice({ status }) {
  if (!status.missing_sources?.length) return null;
  const names = status.missing_sources.map((s) => SOURCE_LABELS[s] ?? s).join(', ');
  return (
    <section className="notice" role="status">
      {names} 신호가 지금 없습니다. 단계는 마지막으로 확인한 값({formatTime(status.ts)})을 유지하고 있습니다.
    </section>
  );
}

function CauseList({ causes }) {
  if (!causes.length) return null;
  return (
    <section className="card">
      <h2>의심되는 원인</h2>
      <ul className="causes">
        {causes.map((c) => (
          <li key={c.rule_id}>
            <span>{c.name} <em>의심</em></span>
            <strong>{Math.round(c.probability * 100)}%</strong>
          </li>
        ))}
      </ul>
      <p className="hint">확정이 아니라 추정입니다.</p>
    </section>
  );
}

function Recommendation({ rec }) {
  const [done, setDone] = useState(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);

  const main = rec.options.find((o) => o.id === rec.recommended_option);
  const others = rec.options.filter((o) => o.id !== rec.recommended_option);
  if (!main) return null;

  const choose = async (option) => {
    setSending(true);
    setError(null);
    try {
      await api.chooseOption(rec.id, option.id);
      setDone(option.label);
    } catch {
      setError('기록하지 못했습니다. 다시 눌러 주세요.');
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="card recommend">
      <h2>지금 추천하는 조치</h2>
      <p className="rec-title">{main.label}</p>
      {main.reason && <p>{main.reason}</p>}
      {done ? (
        <p className="done" role="status">✔ “{done}” 선택을 기록했습니다.</p>
      ) : (
        <button className="primary" disabled={sending || !main.available} onClick={() => choose(main)}>
          {sending ? '기록 중…' : `${main.label} 하기`}
        </button>
      )}
      {error && <p className="error">{error}</p>}

      {others.length > 0 && (
        <details>
          <summary>다른 방법 보기</summary>
          <ul className="others">
            {others.map((o) => (
              <li key={o.id} className={o.available ? '' : 'disabled'}>
                <strong>{o.label}</strong>
                {!o.available && ' (지금은 선택할 수 없음)'}
                {o.reason && <span> — {o.reason}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
