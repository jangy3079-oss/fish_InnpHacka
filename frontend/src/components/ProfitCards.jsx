import { useState } from 'react';
import { api } from '../api/client.js';

export function formatKrw(v) {
  if (v == null) return '계산 전';
  if (v === 0) return '비용 없음';
  if (v >= 10000) return `약 ${Math.round(v / 10000).toLocaleString('ko-KR')}만 원`;
  return `${v.toLocaleString('ko-KR')}원`;
}

// 조치별 예상 손해 비교. 손해가 적은 순으로 두고 막대로 크기를 견줄 수 있게 한다. 불가한 조치는 이유와 함께 맨 아래에 둔다.
export function ProfitCompare({ rec }) {
  const usable = rec.options
    .filter((o) => o.available)
    .sort((a, b) => (a.estimated_loss_krw ?? Infinity) - (b.estimated_loss_krw ?? Infinity));
  const blocked = rec.options.filter((o) => !o.available);
  const maxLoss = Math.max(1, ...usable.map((o) => o.estimated_loss_krw ?? 0));

  return (
    <section className="card flat">
      <h2>조치별 예상 손해</h2>
      <p className="hint">손해가 적은 순서입니다. 추정값이며 실제와 다를 수 있습니다.</p>
      <div className="loss-head" aria-hidden="true">
        <span>조치</span>
        <span>예상 손해</span>
      </div>
      <ul className="loss-list">
        {usable.map((o) => (
          <OptionRow key={o.id} option={o} recommended={o.id === rec.recommended_option} maxLoss={maxLoss} />
        ))}
        {blocked.map((o) => (
          <OptionRow key={o.id} option={o} recommended={false} maxLoss={maxLoss} />
        ))}
      </ul>
    </section>
  );
}

function OptionRow({ option, recommended, maxLoss }) {
  const [open, setOpen] = useState(false);
  const loss = option.estimated_loss_krw;
  const width = loss != null && option.available ? `${Math.max(loss > 0 ? 2 : 0, (loss / maxLoss) * 100)}%` : '0%';
  const cls = ['loss-row', recommended && 'recommended', !option.available && 'blocked'].filter(Boolean).join(' ');
  return (
    <li className={cls}>
      <div className="loss-name">
        <strong>{option.label}</strong>
        {recommended && <span className="badge">추천</span>}
        {/* 추천 조치의 이유는 위의 추천 카드에 이미 있어 반복하지 않는다 */}
        {!recommended && option.reason && <span className={option.available ? 'loss-reason' : 'loss-reason why-not'}>{option.reason}</span>}
      </div>
      <div className="loss-value">
        {option.available ? (
          <>
            <span className="loss-bar" aria-hidden="true">
              <span className="loss-fill" style={{ width }} />
            </span>
            <strong>{formatKrw(loss)}</strong>
          </>
        ) : (
          <strong className="loss-blocked">불가</strong>
        )}
      </div>
      {option.calc && (
        <div className="loss-calc">
          <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? '계산 접기' : '계산 보기'}
          </button>
          {open && (
            <div className="calc">
              <p className="formula">{option.calc.formula}</p>
              <dl className="inputs">
                {Object.entries(option.calc.inputs).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
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
      if (!rec.simulated && rec.id !== 0) await api.requestDiagnosis(rec.cage_id); // 시연 화면은 접수하지 않는다
      setState('done');
    } catch {
      setState('error');
    }
  };
  return (
    <section className="card key">
      <h2>질병이 의심됩니다</h2>
      <p>원인을 확정하지 못했습니다. 먼저 아래 조치를 해 주세요.</p>
      <ol className="aid-steps">
        {rec.first_aid.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {state === 'done' ? (
        <p className="done" role="status">
          {rec.simulated || rec.id === 0 ? '✔ 시연 화면이라 실제로 접수되지 않습니다.' : '✔ 진단을 요청했습니다.'}
        </p>
      ) : (
        <button className="primary" disabled={state === 'sending'} onClick={request}>
          {state === 'sending' ? '요청 중…' : rec.simulated || rec.id === 0 ? '시연: 진단 요청해 보기' : '진단 요청하기'}
        </button>
      )}
      {state === 'error' && <p className="error">요청하지 못했습니다. 다시 눌러 주세요.</p>}
    </section>
  );
}
