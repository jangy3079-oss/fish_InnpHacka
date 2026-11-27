import { useEffect, useRef, useState } from 'react';
import { FarmerTabs } from '../components/SiteChrome.jsx';
import { api } from '../api/client.js';
import { usePageTitle } from '../usePageTitle.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { useDemo } from '../demo/DemoContext.jsx';

const SIZES = [
  { id: 'fry', label: '치어', hint: '아주 작은 어린 물고기' },
  { id: 'mid', label: '중간 크기', hint: '자라는 중인 물고기' },
  { id: 'market', label: '출하 크기', hint: '팔 수 있게 자란 물고기' },
];

// 손익 계산에 쓰이는 크기별 재고와 보험 가입 여부 입력 (FR-ADM-03). 어가 경영 정보라 본인에게만 보인다.
export default function StockPage() {
  const { cageId: CAGE_ID, user } = useAuth();
  const { demo } = useDemo();
  const [rows, setRows] = useState(() => Object.fromEntries(SIZES.map((s) => [s.id, { count: '', weight: '' }])));
  const [insured, setInsured] = useState(null); // true | false | null(미선택)
  const [errors, setErrors] = useState({});
  usePageTitle('재고 입력');
  const [state, setState] = useState('idle'); // idle | sending | done | error
  const [loaded, setLoaded] = useState(false);
  const [mixed, setMixed] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  const live = api.live() && !api.files(demo);
  const writable = !live || user?.cages?.find((c) => c.id === CAGE_ID)?.can_write_stock;
  useEffect(() => {
    let active = true;
    generation.current += 1;
    setLoaded(false); setErrors({}); setState('idle'); setInsured(null); setMixed(false); setSavedAt(null);
    setRows(Object.fromEntries(SIZES.map((s) => [s.id, { count: '', weight: '' }])));
    if (CAGE_ID == null) return;
    api.getStock(CAGE_ID, demo).then((r) => {
      if (!active) return;
      const next = Object.fromEntries(SIZES.map((s) => [s.id, { count: '', weight: '' }]));
      for (const row of r.stock) {
        if (!next[row.size_group] || !Number.isSafeInteger(row.count)) throw new Error('Invalid stock');
        next[row.size_group] = { count: String(row.count), weight: String(row.avg_weight_kg) };
      }
      setRows(next); setInsured(r.insured); setMixed(r.insured_mixed);
      setSavedAt(r.stock.map((s) => s.updated_at).filter(Boolean).sort().at(-1) ?? null); setLoaded(true);
    }).catch(() => { if (active) setErrors({ form: '현재 재고를 읽지 못해 저장을 막았습니다. 다시 조회해 주세요.' }); });
    return () => { active = false; generation.current += 1; };
  }, [CAGE_ID, live, attempt]);

  const set = (id, key, value) => {
    setRows((r) => ({ ...r, [id]: { ...r[id], [key]: value } }));
    setState('idle');
  };

  const validate = () => {
    const e = {};
    let filled = 0;
    SIZES.forEach(({ id }) => {
      const { count, weight } = rows[id];
      if (count === '' && weight === '') return;
      const c = Number(count);
      const w = Number(weight);
      if (count === '' || !Number.isSafeInteger(c) || c < 0) e[id] = '마릿수를 정확한 0 이상의 정수로 입력해 주세요';
      else if (weight === '' || !(w > 0)) e[id] = '평균 무게를 0보다 크게 입력해 주세요';
      if (!e[id]) filled += 1;
    });
    if (!Object.keys(e).length && filled === 0) e.form = '한 가지 이상 크기의 마릿수와 무게를 입력해 주세요';
    if (insured === null) e.insured = '보험 가입 여부를 골라 주세요';
    return e;
  };

  const submit = async (ev) => {
    ev.preventDefault();
    if (!loaded || !writable) return;
    const started = generation.current;
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) return;
    const stock = SIZES.filter(({ id }) => rows[id].count !== '' && !e[id]).map(({ id }) => ({
      size_group: id,
      count: Number(rows[id].count),
      avg_weight_kg: Number(rows[id].weight),
    }));
    setState('sending');
    try {
      if (live && !window.confirm('입력한 전체 재고로 교체합니다. 비운 크기 그룹은 삭제되고, 보험 선택은 모든 그룹에 적용됩니다. 계속할까요?')) { setState('idle'); return; }
      const result = await api.putStock(CAGE_ID, { stock, insured }, demo);
      if (started === generation.current) {
        setState('done'); setMixed(false);
        setSavedAt(result.stock?.map((s) => s.updated_at).filter(Boolean).sort().at(-1) ?? null);
      }
    } catch {
      if (started === generation.current) setState('error');
    }
  };

  // 입력한 값으로 크기별 합계 무게와 전체 합계를 보여준다. 숫자로 읽히는 값만 센다.
  const totals = SIZES.map(({ id }) => {
    const c = Number(rows[id].count);
    const w = Number(rows[id].weight);
    return Number.isInteger(c) && c > 0 && w > 0 ? { count: c, kg: c * w } : { count: 0, kg: 0 };
  });
  const sum = totals.reduce((a, t) => ({ count: a.count + t.count, kg: a.kg + t.kg }), { count: 0, kg: 0 });
  const fmt = (n) => Math.round(n).toLocaleString('ko-KR');

  return (
    <div className="stock-page">
      <FarmerTabs />
      <header className="page-head">
        <h1>{CAGE_ID == null ? '접근 가능한 가두리가 없습니다' : `가두리 ${CAGE_ID}번 재고 입력`}</h1>
        <p className="lead">크기별로 몇 마리가 있고 평균 무게가 얼마인지 알려 주세요. 대략적인 숫자여도 괜찮습니다.</p>
      </header>
      <p className="hint">{live ? '실제 재고 · 마지막 저장이 우선하며 생략한 그룹은 삭제됩니다.' : '시연 입력이며 실제 재고로 저장하지 않습니다.'}</p>
      {savedAt && <p className="hint">마지막 저장: {new Date(savedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}</p>}
      {mixed && <p className="notice">그룹별 보험 상태가 다릅니다. 새 선택은 전체 그룹에 적용됩니다.</p>}
      {!loaded && <button type="button" onClick={() => setAttempt((a) => a + 1)}>재고 다시 조회</button>}

      <div className="stock-grid">
        <form className="card stock" onSubmit={submit} noValidate>
          <fieldset style={{ border: 0, padding: 0, minWidth: 0 }} disabled={!loaded || !writable || state === 'sending'}>
          <div className="stock-head" aria-hidden="true">
            <span>크기</span>
            <span>마릿수</span>
            <span>평균 무게</span>
            <span>합계 무게</span>
          </div>

          {SIZES.map(({ id, label, hint }, i) => (
            <div key={id} className="stock-row">
              <div className="stock-name">
                <strong>{label}</strong>
                <span>{hint}</span>
              </div>
              <label data-label="마릿수">
                <span className="input-unit">
                  <input
                    inputMode="numeric"
                    aria-label={`${label} 마릿수`}
                    value={rows[id].count}
                    onChange={(e) => set(id, 'count', e.target.value)}
                    placeholder="0"
                    aria-invalid={!!errors[id]}
                  />
                  마리
                </span>
              </label>
              <label data-label="평균 무게">
                <span className="input-unit">
                  <input
                    inputMode="decimal"
                    aria-label={`${label} 평균 무게`}
                    value={rows[id].weight}
                    onChange={(e) => set(id, 'weight', e.target.value)}
                    placeholder="0.0"
                    aria-invalid={!!errors[id]}
                  />
                  kg
                </span>
              </label>
              <p className="stock-sum" data-label="합계 무게">{totals[i].kg ? `${fmt(totals[i].kg)}kg` : '-'}</p>
              {errors[id] && <p className="error stock-error" role="alert">{errors[id]}</p>}
            </div>
          ))}

          <p className="stock-total">
            전체 합계 <strong>{fmt(sum.count)}마리</strong>, 약 <strong>{fmt(sum.kg)}kg</strong>
          </p>

          <fieldset className="insured">
            <legend>재해보험</legend>
            <div className="choices">
              <label><input type="radio" name="insured" checked={insured === true} onChange={() => { setInsured(true); setState('idle'); }} /> 가입했습니다</label>
              <label><input type="radio" name="insured" checked={insured === false} onChange={() => { setInsured(false); setState('idle'); }} /> 가입하지 않았습니다</label>
            </div>
            {errors.insured && <p className="error" role="alert">{errors.insured}</p>}
          </fieldset>

          {errors.form && <p className="error" role="alert">{errors.form}</p>}
          <div className="form-actions">
            <button className="primary" type="submit" disabled={state === 'sending'}>
              {state === 'sending' ? '저장 중…' : '저장하기'}
            </button>
            {state === 'done' && <p className="done" role="status">{live ? '✔ 저장했습니다.' : '✔ 시연 입력을 확인했습니다. 실제로 저장하지 않습니다.'}</p>}
            {state === 'error' && <p className="error" role="alert">저장하지 못했습니다. 다시 눌러 주세요.</p>}
          </div>
          </fieldset>
        </form>

        <aside className="card aside">
          <h2>입력한 값은 이렇게 쓰입니다</h2>
          <ul>
            <li>조치별 예상 손해를 계산할 때 마릿수와 평균 무게가 들어갑니다.</li>
            <li>보험 가입 여부는 기록해 두며, 실제 보험금은 보상 적용이 확인된 경우에만 계산에 반영됩니다.</li>
            <li>우리 어가의 경영 정보라서 본인과 권한을 받은 담당자만 볼 수 있습니다.</li>
            <li>숫자가 바뀌면 언제든 다시 입력해 저장하면 됩니다.</li>
          </ul>
        </aside>
      </div>
    </div>
  );
}
