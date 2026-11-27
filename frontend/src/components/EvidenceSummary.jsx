// 판단 근거를 몇 가지 핵심 수치로 줄여 보여준다. API가 준 값 중 아는 것만 쓰고, 없는 값은 건너뛴다.
const ITEMS = [
  { key: 'mid_layer_ratio_x', label: '중층에 있는 물고기', fmt: (v) => `평소의 ${v}배` },
  {
    key: 'activity_change',
    label: '움직임',
    fmt: (v) => (v < 0 ? `평소보다 ${Math.round(-v * 100)}% 줄었음` : v > 0 ? `평소보다 ${Math.round(v * 100)}% 늘었음` : '평소와 같음'),
  },
  { key: 'temp_surface', label: '표층 수온', fmt: (v) => `${v.toFixed(1)}℃` },
  { key: 'temp_mid', label: '중층 수온', fmt: (v) => `${v.toFixed(1)}℃` },
];

export default function EvidenceSummary({ evidence }) {
  const rows = ITEMS.filter((i) => typeof evidence?.[i.key] === 'number');
  if (!rows.length) return null;
  return (
    <section className="card flat">
      <h2>판단 근거 요약</h2>
      <dl className="facts">
        {rows.map((i) => (
          <div key={i.key}>
            <dt>{i.label}</dt>
            <dd>{i.fmt(evidence[i.key])}</dd>
          </div>
        ))}
      </dl>
      <p className="hint"><a href="#evidence">그래프로 자세히 보기</a></p>
    </section>
  );
}
