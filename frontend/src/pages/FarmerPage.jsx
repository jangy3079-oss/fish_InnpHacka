import { useEffect, useState } from 'react';
import { useDemo } from '../demo/DemoContext.jsx';
import { usePageTitle } from '../usePageTitle.js';
import { ErrorNotice, LoadingBlock, Skeleton } from '../components/States.jsx';
import { api } from '../api/client.js';
import { FarmerTabs } from '../components/SiteChrome.jsx';
import EvidenceCharts from '../components/EvidenceCharts.jsx';
import { FirstAidCard, ProfitCompare } from '../components/ProfitCards.jsx';
import { BASELINE_WINDOWS, LEVELS, SOURCE_LABELS, formatTime } from '../constants.js';

const CAGE_ID = 3;

// 경고·위험이면 현재 시점을 경보 시점으로 표시한다. (과거 경보 이력 API는 아직 없음)
const isAlert = (s) => s.level === '경고' || s.level === '위험';

export default function FarmerPage() {
  const [status, setStatus] = useState(null);
  const [rec, setRec] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  usePageTitle('어민 현황');

  const { demo } = useDemo();

  // 시나리오·시각이 바뀌면 다시 불러온다. 이전 화면은 새 값이 올 때까지 유지해 깜빡임을 줄인다.
  useEffect(() => {
    let alive = true;
    Promise.all([api.getStatus(CAGE_ID, demo), api.getRecommendation(CAGE_ID, demo)])
      .then(([s, r]) => {
        if (!alive) return;
        setStatus(s);
        setRec(r);
        setError(null);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [demo, attempt]);

  if (error && (!status || !rec)) {
    return (
      <div className="farmer-page">
        <ErrorNotice onRetry={() => { setError(null); setAttempt((a) => a + 1); }} />
      </div>
    );
  }
  if (!status || !rec) {
    return (
      <div className="farmer-page">
        <FarmerTabs />
        <LoadingBlock label="어민 현황을 불러오는 중입니다">
          <div className="farmer-grid">
            <div className="farmer">
              <Skeleton height={230} />
              <Skeleton height={130} />
              <Skeleton height={190} />
            </div>
            <div className="farmer">
              <Skeleton height={420} />
              <Skeleton height={300} />
            </div>
          </div>
        </LoadingBlock>
      </div>
    );
  }

  const isProfit = rec.kind !== 'none' && rec.kind !== 'first_aid';

  // 넓은 화면에서는 왼쪽에 지금 상태와 조치, 오른쪽에 판단 근거를 둔다.
  return (
    <div className="farmer-page">
      <FarmerTabs />
      <div className="farmer-grid">
        <div className="farmer">
          <LevelCard status={status} />
          <SourceNotice status={status} />
          <CauseList causes={status.cause} />
          {rec.kind === 'none' && (
            <section className="card"><h2>지금 추천하는 조치</h2><p>지금은 따로 할 조치가 없습니다.</p></section>
          )}
          {rec.kind === 'first_aid' && <FirstAidCard key={rec.ts} rec={rec} />}
          {isProfit && <Recommendation key={`${rec.ts}-${rec.recommended_option}`} rec={rec} />}
        </div>
        <div className="farmer">
          {isProfit && <ProfitCompare rec={rec} />}
          <EvidenceCharts cageId={CAGE_ID} alertAt={isAlert(status) ? status.ts : undefined} />
        </div>
      </div>
    </div>
  );
}

function LevelCard({ status }) {
  const warming = status.baseline_status === 'warming_up';
  const lv = LEVELS[status.level];

  // 단계 자체가 없으면(null) 준비 중 카드만 보여준다.
  if (!lv) {
    return (
      <section className="card level" style={{ background: '#eef2f6' }}>
        <h1>가두리 {status.cage_id}번</h1>
        <p className="level-name">준비 중</p>
        <p>평소 상태를 파악하는 중입니다. 잠시만 기다려 주세요.</p>
        <Warmup status={status} />
      </section>
    );
  }
  return (
    <section className="card level" style={{ background: lv.bg, color: lv.color }}>
      <h1>가두리 {status.cage_id}번</h1>
      <p className="level-name">
        <span aria-hidden="true">{lv.icon}</span> {status.level}
      </p>
      <p>{lv.message}</p>
      {warming || status.stress_score == null ? (
        <>
          <p className="score">스트레스 지수는 평소 상태를 파악한 뒤에 나옵니다.</p>
          <Warmup status={status} />
        </>
      ) : (
        <p className="score">
          스트레스 지수 <strong>{Math.round(status.stress_score)}</strong> / 100
        </p>
      )}
      <p className="time">{formatTime(status.ts)} 기준</p>
    </section>
  );
}

// 기준선 학습 진행률 (FR-DIA-06). baseline_windows가 없으면 문구만 보여준다.
function Warmup({ status }) {
  if (status.baseline_status !== 'warming_up') return null;
  const n = status.baseline_windows;
  if (n == null) return <p className="warmup">평소 상태를 학습하는 중입니다.</p>;
  const done = Math.min(n, BASELINE_WINDOWS);
  return (
    <div className="warmup">
      <p>평소 상태 학습 {done}/{BASELINE_WINDOWS}</p>
      <progress value={done} max={BASELINE_WINDOWS} aria-label="평소 상태 학습 진행률" />
    </div>
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

    </section>
  );
}
