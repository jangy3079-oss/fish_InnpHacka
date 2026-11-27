const dateFormat = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
});

export default function RecommendationDeadlines({ rec }) {
  const entries = rec.deadlines ?? [];
  if (!entries.length) {
    return <p className="hint">실행 기한 정보가 없습니다. 신청·출하 기한을 확인해 주세요.</p>;
  }
  return (
    <aside className="recommendation-deadlines" aria-label="추천 실행 기한">
      <h3>실행 기한과 다음 판단</h3>
      <dl>
        {entries.map((entry) => (
          <div key={entry.kind}>
            <dt>{entry.label}</dt>
            <dd>
              {entry.at && Number.isFinite(Date.parse(entry.at)) ? (
                <time dateTime={entry.at}>{dateFormat.format(new Date(entry.at))} (한국 시각)</time>
              ) : '확인 필요'}
              {entry.source_status === 'assumed' && ' · 가정 기준'}
              {entry.expired && ' · 시각 지남 — 다시 확인해 주세요'}
              {entry.applicable === false && ' · 현재 선택 불가'}
              {entry.source_status === 'pending' && ' · 확인된 마감 정보 없음'}
              {entry.source && <span className="deadline-source">근거: {entry.source}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
