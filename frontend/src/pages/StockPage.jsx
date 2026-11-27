import { useState } from 'react';
import { api } from '../api/client.js';

const CAGE_ID = 3;
const SIZES = [
  { id: 'fry', label: '치어', hint: '아주 작은 어린 물고기' },
  { id: 'mid', label: '중간 크기', hint: '자라는 중인 물고기' },
  { id: 'market', label: '출하 크기', hint: '팔 수 있게 자란 물고기' },
];

// 손익 계산에 쓰이는 크기별 재고와 보험 가입 여부 입력 (FR-ADM-03). 어가 경영 정보라 본인에게만 보인다.
export default function StockPage() {
  const [rows, setRows] = useState(() => Object.fromEntries(SIZES.map((s) => [s.id, { count: '', weight: '' }])));
  const [insured, setInsured] = useState(null); // true | false | null(미선택)
  const [errors, setErrors] = useState({});
  const [state, setState] = useState('idle'); // idle | sending | done | error

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
      if (count === '' || !Number.isInteger(c) || c < 0) e[id] = '마릿수를 0 이상의 정수로 입력해 주세요';
      else if (c === 0) return; // 0마리는 보내지 않는다
      else if (weight === '' || !(w > 0)) e[id] = '평균 무게를 0보다 크게 입력해 주세요';
      if (!e[id]) filled += 1;
    });
    if (!Object.keys(e).length && filled === 0) e.form = '한 가지 이상 크기의 마릿수와 무게를 입력해 주세요';
    if (insured === null) e.insured = '보험 가입 여부를 골라 주세요';
    return e;
  };

  const submit = async (ev) => {
    ev.preventDefault();
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) return;
    const stock = SIZES.filter(({ id }) => Number(rows[id].count) > 0).map(({ id }) => ({
      size_group: id,
      count: Number(rows[id].count),
      avg_weight_kg: Number(rows[id].weight),
    }));
    setState('sending');
    try {
      await api.putStock(CAGE_ID, { stock, insured });
      setState('done');
    } catch {
      setState('error');
    }
  };

  return (
    <form className="farmer stock" onSubmit={submit} noValidate>
      <h1>가두리 {CAGE_ID}번 재고 입력</h1>
      <p className="hint">손해를 계산할 때 쓰입니다. 대략적인 숫자여도 괜찮습니다.</p>

      {SIZES.map(({ id, label, hint }) => (
        <fieldset key={id} className="card">
          <legend>{label}</legend>
          <p className="hint">{hint}</p>
          <label>
            마릿수
            <span className="input-unit">
              <input inputMode="numeric" value={rows[id].count} onChange={(e) => set(id, 'count', e.target.value)} placeholder="0" aria-invalid={!!errors[id]} />
              마리
            </span>
          </label>
          <label>
            평균 무게
            <span className="input-unit">
              <input inputMode="decimal" value={rows[id].weight} onChange={(e) => set(id, 'weight', e.target.value)} placeholder="0.0" aria-invalid={!!errors[id]} />
              kg
            </span>
          </label>
          {errors[id] && <p className="error" role="alert">{errors[id]}</p>}
        </fieldset>
      ))}

      <fieldset className="card">
        <legend>재해보험</legend>
        <div className="choices">
          <label><input type="radio" name="insured" checked={insured === true} onChange={() => { setInsured(true); setState('idle'); }} /> 가입했습니다</label>
          <label><input type="radio" name="insured" checked={insured === false} onChange={() => { setInsured(false); setState('idle'); }} /> 가입하지 않았습니다</label>
        </div>
        {errors.insured && <p className="error" role="alert">{errors.insured}</p>}
      </fieldset>

      {errors.form && <p className="error" role="alert">{errors.form}</p>}
      <button className="primary" type="submit" disabled={state === 'sending'}>
        {state === 'sending' ? '저장 중…' : '저장하기'}
      </button>
      {state === 'done' && <p className="done" role="status">✔ 저장했습니다.</p>}
      {state === 'error' && <p className="error" role="alert">저장하지 못했습니다. 다시 눌러 주세요.</p>}
    </form>
  );
}
