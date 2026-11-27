// 원인 판정을 보류하는 이유(status.cause_pending). 서버가 준 값만 쓰고, 없는 진행 시간은 만들지 않는다.
// 숫자(DO 등)는 쓰지 않는다. 모르는 reason 값은 보여 주지 않는다.
const NAME = { R5: '질병', R8: '기생충' };

function progressText(p) {
  if (!p || typeof p.done !== 'number' || typeof p.needed !== 'number') return '';
  const unit = p.unit === 'hour' ? '시간' : p.unit === 'minute' ? '분' : '';
  // 서버 값이 소수(61.9166...)로 올 수 있다. 완료한 것처럼 보이지 않게 내림해서 정수로 보여 준다.
  return `(현재 ${Math.floor(p.done)}/${Math.floor(p.needed)}${unit})`;
}

export function pendingText(item) {
  const name = NAME[item?.rule_id];
  switch (item?.reason) {
    case 'trend_waiting':
      return `${name ?? '질병'} 의심 여부는 일정 시간 지켜본 뒤 판단합니다${progressText(item.progress) && ` ${progressText(item.progress)}`}.`;
    case 'observing':
      return `${name ?? '기생충'} 의심 여부는 영상이 잘 보이는 시간에 관찰하는 중입니다${progressText(item.progress) && ` ${progressText(item.progress)}`}.`;
    case 'observation_gap':
      return `${name ?? '기생충'} 의심은 영상이 잘 보이는 시간에 판단합니다.`;
    case 'advisory_unknown':
      return '특보 정보를 확인하지 못해 질병·기생충 의심 판단을 보류했습니다.';
    default:
      return null;
  }
}

export default function CausePending({ items }) {
  const lines = [...new Set((items ?? []).map(pendingText).filter(Boolean))];
  if (!lines.length) return null;
  return (
    <ul className="pending-notes">
      {lines.map((t) => (
        <li key={t} className="hint">{t}</li>
      ))}
    </ul>
  );
}
