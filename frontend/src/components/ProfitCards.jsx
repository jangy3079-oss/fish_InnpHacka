import { useState } from 'react';
import { api } from '../api/client.js';

export function formatKrw(v) {
  if (v == null) return '계산 전';
  if (v === 0) return '비용 없음';
  if (v >= 10000) return `약 ${Math.round(v / 10000).toLocaleString('ko-KR')}만 원`;
  return `${v.toLocaleString('ko-KR')}원`;
}

// 선택지별 예상 손해 비교. 손해가 적은 순으로 보여주고, 불가한 항목은 이유와 함께 맨 아래에 둔다.
export function ProfitCompare({ rec }) {
  const usable = rec.options
    .filter((o) => o.available)
    .sort((a, b) => (a.estimated_loss_krw ?? Infinity) - (b.estimated_loss_krw ?? Infinity));
  const blocked = rec.options.filter((o) => !o.available);

  return (
    <section className="card">
      <h2>선택지별 예상 손해</h2>
      <p className="hint">손해가 적은 순서입니다. 추정값이며 실제와 다를 수 있습니다.</p>
      <ul className="options">
        {[...usable, ...blocked].map((o) => (
          <OptionRow key={o.id} option={o} recommended={o.id === rec.recommended_option} />
        ))}
      </ul>
    </section>
  );
}

function OptionRow({ option, recommended }) {
  const cls = ['option', recommended && 'recommended', !option.available && 'blocked'].filter(Boolean).join(' ');
  return (
    <li className={cls}>
      <div className="option-head">
        <strong>
          {option.label}
          {recommended && <span className="badge">추천</span>}
        </strong>
        <span className="loss">{option.available ? formatKrw(option.estimated_loss_krw) : '불가'}</span>
      </div>
      {option.reason && <p className={option.available ? 'reason' : 'reason why-not'}>{option.reason}</p>}
      {option.calc && (
        <details>
          <summary>계산 보기</summary>
          <p className="formula">{option.calc.formula}</p>
          <dl className="inputs">
            {Object.entries(option.calc.inputs).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </li>
  );
}

// 질병·기생충 의심: 손익 대신 공통 1차 조치와 진단 요청.
export function FirstAidCard({ rec }) {
  const [state, setState] = useState('idle'); // idle | sending | done | error
  const request = async () => {
    setState('sending');
    try {
      await api.requestDiagnosis(rec.cage_id);
      setState('done');
    } catch {
      setState('error');
    }
  };
  return (
    <section className="card recommend">
      <h2>질병이 의심됩니다</h2>
      <p>원인을 확정하지 못했습니다. 먼저 아래 조치를 해 주세요.</p>
      <ol className="steps">
        {rec.first_aid.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {state === 'done' ? (
        <p className="done" role="status">✔ 진단을 요청했습니다.</p>
      ) : (
        <button className="primary" disabled={state === 'sending'} onClick={request}>
          {state === 'sending' ? '요청 중…' : '진단 요청하기'}
        </button>
      )}
      {state === 'error' && <p className="error">요청하지 못했습니다. 다시 눌러 주세요.</p>}
    </section>
  );
}
