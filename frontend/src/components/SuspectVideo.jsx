// 시연용: 원인 의심 판정이 나왔을 때 어떤 모습인지 짧은 영상으로 보여준다.
// 영상 파일은 frontend/public/ 아래에 둔다(빌드 때 그대로 복사됨).
const VIDEOS = {
  R8: { // 기생충 의심
    src: '/suspected_parasite.mp4',
    title: '기생충 의심 영상',
    text: '물고기가 그물이나 바닥에 몸을 비비고 갑자기 방향을 트는 모습이 평소보다 자주 보입니다.',
  },
};

export default function SuspectVideo({ causes }) {
  // 판정된 원인 중 영상이 있는 첫 번째 것. 대기 중(cause_pending)인 원인은 판정 전이라 넣지 않는다.
  const v = (causes ?? []).map((c) => VIDEOS[c.rule_id]).find(Boolean);
  if (!v) return null;
  return (
    <section className="card flat suspect-video">
      <h2>{v.title}</h2>
      <video src={v.src} controls muted loop playsInline preload="metadata" />
      <p className="hint">{v.text}</p>
    </section>
  );
}
