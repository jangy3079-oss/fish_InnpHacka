import { useEffect, useState } from 'react';
import { api } from '../api/client.js';

// 지자체 대시보드 뼈대. 실제 UI는 #15에서 구현한다.
export default function DashboardPage() {
  const [map, setMap] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getStressMap().then(setMap).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">불러오기 실패: {error}</p>;
  if (!map) return <p>불러오는 중…</p>;

  return (
    <section>
      <h1>해역 스트레스 현황</h1>
      <table>
        <thead>
          <tr>
            <th>해역</th>
            <th>단계</th>
            <th>스트레스</th>
            <th>가두리 수</th>
          </tr>
        </thead>
        <tbody>
          {map.areas.map((a) => (
            <tr key={a.area_id}>
              <td>{a.name}</td>
              <td>{a.level}</td>
              <td>{a.stress_score}</td>
              <td>{a.cage_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
