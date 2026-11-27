import { useState } from 'react';
import { api } from '../api/client.js';
import RecommendationDeadlines from './RecommendationDeadlines.jsx';

export function formatKrw(v) {
  if (v == null) return '계산 전';
  if (v === 0) return '비용 없음';
  // 지원금이 손실보다 커서 음수인 경우. 부호만 붙이면 뜻이 전달되지 않는다. 확정 수익이 아니라 가정 계산이다.
  if (v < 0) return `이익 ${formatKrw(-v)}`;
  if (v >= 10000) return `약 ${Math.round(v / 10000).toLocaleString('ko-KR')}만 원`;
  return `${v.toLocaleString('ko-KR')}원`;
}

// 서버가 준 금액 문자열("1200000.000")을 다시 계산하지 않고 정수 원으로 표시만 한다.
const won = (s) => (s == null ? '-' : `${Math.round(Number(s)).toLocaleString('ko-KR')}원`);
const SOURCE_TEXT = { config_assumption: '가정값', stock_input: '입력값', input: '입력값' };
const SIZE_TEXT = { fry: '치어', mid: '중간어', market: '상품어' };

// 방류 한도를 넘을 때의 대안(구조형). 문구는 프론트가 만든다.
function alternativeText(a) {
  if (a.type === 'sequential') return `${a.interval_days}일 간격으로 나누어 방류`;
  if (a.type === 'spread') return `${a.min_distance_km}km 이상 떨어뜨려 방류`;
  return null;
}

// 조치별 예상 손해 비교. 손해가 적은 순으로 두고 막대로 크기를 견줄 수 있게 한다. 불가한 조치는 이유와 함께 맨 아래에 둔다.
export function ProfitCompare({ rec }) {
  const usable = rec.options
    .filter((o) => o.available)
    .sort((a, b) => (a.estimated_loss_krw ?? Infinity) - (b.estimated_loss_krw ?? Infinity));
  const blocked = rec.options.filter((o) => !o.available);
  const maxLoss = Math.max(1, ...usable.map((o) => o.estimated_loss_krw ?? 0));
  const alternatives = (rec.release_check?.alternatives ?? []).map(alternativeText).filter(Boolean);
  const releaseBlocked = rec.options.find((o) => o.id === 'release' && !o.available);

  return (
    <section className="card flat">
      <h2>조치별 예상 손해</h2>
      <p className="hint">손해가 적은 순서입니다. 추정값이며 실제와 다를 수 있습니다.</p>
      <RecommendationDeadlines rec={rec} />
      <div className="loss-head" aria-hidden="true">
        <span>조치</span>
        <span>예상 손해</span>
      </div>
      <ul className="loss-list">
        {usable.map((o) => (
          <OptionRow revision={rec.config_revision} key={o.id} option={o} recommended={o.id === rec.recommended_option} lowest={o.id === rec.details?.lowest_loss_option && o.id !== rec.recommended_option} management={rec.details?.recommendation_basis === 'R7_feeding_management'} maxLoss={maxLoss} />
        ))}
        {blocked.map((o) => (
          <OptionRow revision={rec.config_revision} key={o.id} option={o} recommended={false} maxLoss={maxLoss} />
        ))}
      </ul>
      {releaseBlocked && alternatives.length > 0 && (
        <p className="hint">방류가 한도를 넘는 경우의 대안: {alternatives.join(', ')}.</p>
      )}
    </section>
  );
}

function OptionRow({ option, recommended, lowest, management, maxLoss, revision }) {
  const [open, setOpen] = useState(false);
  const loss = option.estimated_loss_krw;
  const width = loss != null && option.available && loss > 0 ? `${Math.max(2, (loss / maxLoss) * 100)}%` : '0%'; // 음수·0은 막대를 그리지 않는다
  const reasons = option.reasons?.length ? option.reasons.map((r) => r.message).filter(Boolean) : option.reason ? [option.reason] : [];
  const cls = ['loss-row', recommended && 'recommended', !option.available && 'blocked'].filter(Boolean).join(' ');
  return (
    <li className={cls}>
      <div className="loss-name">
        <strong>{option.label}</strong>
        {recommended && <span className="badge">{management ? '권고' : '추천'}</span>}
        {lowest && <span className="badge badge-plain">손해 최소</span>}
        {/* 추천 조치의 이유는 위의 추천 카드에 이미 있어 반복하지 않는다 */}
        {!recommended && reasons.map((m) => <span key={m} className={option.available ? 'loss-reason' : 'loss-reason why-not'}>{m}</span>)}
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
      {(option.calculation || option.calc) && (
        <div className="loss-calc">
          <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? '계산 접기' : '계산 보기'}
          </button>
          {open && (
            option.calculation ? (
              <CalculationDetail option={option} revision={revision} />
            ) : (
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
            )
          )}
        </div>
      )}
    </li>
  );
}

// 서버의 calculation을 그대로 풀어 보여 준다. 경제 산식을 브라우저에서 다시 계산하지 않는다.
function CalculationDetail({ option, revision }) {
  const c = option.calculation;
  const loss = option.estimated_loss_krw;
  return (
    <div className="calc">
      <p className="hint">가정한 시세와 손실률로 계산한 추정값입니다. 실제 지급이나 확정 수익이 아닙니다.</p>
      <dl className="inputs">
        <div><dt>재고 손실</dt><dd>{won(c.stock_loss_krw)}</dd></div>
        <div><dt>조치 비용</dt><dd>{won(c.action_cost_krw)}</dd></div>
        <div><dt>보험금(−)</dt><dd>{won(c.insurance_krw)}</dd></div>
        <div><dt>지원금(−)</dt><dd>{won(c.subsidy_krw)}</dd></div>
        <div><dt>예상 손해</dt><dd>{loss == null ? '-' : `${loss.toLocaleString('ko-KR')}원`}</dd></div>
      </dl>
      {loss != null && loss < 0 && <p className="hint">지원금이 손실보다 커서 이익으로 계산됩니다. 확정 수익이 아니라 가정 계산입니다.</p>}
      {c.insurance_confirmed === false && <p className="hint">보험은 가입 신고만으로 차감하지 않고, 확인된 보상 적용이 있을 때만 반영합니다.</p>}
      {c.groups?.length > 0 && (
        <table className="calc-groups">
          <thead><tr><th>크기</th><th>마릿수</th><th>kg당 시세</th><th>평균 무게</th><th>손실률</th></tr></thead>
          <tbody>
            {c.groups.map((g) => (
              <tr key={g.size_group}>
                <td>{SIZE_TEXT[g.size_group] ?? g.size_group}</td>
                <td>{g.count?.toLocaleString('ko-KR')}</td>
                <td>{won(g.price_per_kg)} <small>({SOURCE_TEXT[g.price_source] ?? g.price_source})</small></td>
                <td>{g.avg_weight_kg}kg <small>({SOURCE_TEXT[g.weight_source] ?? g.weight_source})</small></td>
                <td>{Math.round(Number(g.loss_rate) * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {revision && <p className="hint">계산 기준 버전 {revision.slice(0, 8)}</p>}
    </div>
  );
}

// 질병·기생충 의심: 손익 대신 공통 1차 조치와 진단 요청.
export function FirstAidCard({ rec, rule }) {
  const [state, setState] = useState('idle'); // idle | sending | done | error
  const [error, setError] = useState('');
  const simulated = rec.simulated || rec.id === 0;
  const request = async () => {
    setState('sending');
    try {
      if (!simulated) await api.requestDiagnosis(rec.cage_id); // 시연 화면은 접수하지 않는다
      setState('done');
    } catch (e) {
      setError(
        e.status === 409
          ? '지금은 요청할 수 있는 최신 관측이 없습니다. 잠시 뒤 다시 확인해 주세요.'
          : e.status === 403
            ? '이 가두리의 소유자만 요청할 수 있습니다.'
            : e.status === 422 || e.status === 503
              ? '요청을 처리하지 못했습니다. 잠시 뒤 다시 눌러 주세요.'
              : '요청하지 못했습니다. 다시 눌러 주세요.',
      );
      setState('error');
    }
  };
  return (
    <section className="card key">
      <h2>{rule === 'R8' ? '기생충이 의심됩니다' : '질병이 의심됩니다'}</h2>
      <p>원인을 확정하지 못했습니다. 먼저 아래 조치를 해 주세요.</p>
      <RecommendationDeadlines rec={rec} />
      <ol className="aid-steps">
        {rec.first_aid.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {state === 'done' ? (
        <p className="done" role="status">
          {simulated ? '✔ 시연 화면이라 실제로 접수되지 않습니다.' : '✔ 진단 요청을 접수했습니다. 담당자에게 전달 대기 중입니다.'}
        </p>
      ) : (
        <button className="primary" disabled={state === 'sending'} onClick={request}>
          {state === 'sending' ? '요청 중…' : simulated ? '시연: 진단 요청해 보기' : '진단 요청 접수하기'}
        </button>
      )}
      {state === 'error' && <p className="error">{error}</p>}
    </section>
  );
}
