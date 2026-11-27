import { useEffect, useState } from 'react';
import { api } from '../api/client.js';

const CAGE_ID = 3;

// 어민 화면 뼈대. 실제 UI는 #12에서 구현한다.
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

  if (error) return <p className="error">불러오기 실패: {error}</p>;
  if (!status || !rec) return <p>불러오는 중…</p>;

  return (
    <section>
      <h1>가두리 {status.cage_id}번</h1>
      <p>
        스트레스 {status.stress_score ?? '-'} · <strong>{status.level ?? '측정 중'}</strong>
      </p>
      <h2>추정 원인</h2>
      <ul>
        {status.cause.map((c) => (
          <li key={c.rule_id}>
            {c.name} {Math.round(c.probability * 100)}%
          </li>
        ))}
      </ul>
      <h2>권장 조치</h2>
      <ul>
        {rec.options.map((o) => (
          <li key={o.id} className={o.available ? '' : 'disabled'}>
            {o.label}
            {o.id === rec.recommended_option && ' (권장)'} — {o.reason}
          </li>
        ))}
      </ul>
    </section>
  );
}
