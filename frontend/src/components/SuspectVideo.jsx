// 시연용: 원인 의심 시나리오에서 어떤 모습인지 짧은 영상으로 보여준다.
// 영상 파일은 frontend/public/ 아래에 둔다(빌드 때 그대로 복사됨).
const VIDEOS = {
  parasite_suspect: {
    src: '/suspected_parasite.mp4',
    title: '기생충 의심 영상',
    text: '물고기가 그물이나 바닥에 몸을 비비고 갑자기 방향을 트는 모습이 평소보다 자주 보입니다.',
  },
};

export default function SuspectVideo({ scenario }) {
  const v = VIDEOS[scenario];
  if (!v) return null;
  return (
    <section className="card flat suspect-video">
      <h2>{v.title}</h2>
      <video src={v.src} controls muted loop playsInline preload="metadata" />
      <p className="hint">{v.text}</p>
    </section>
  );
}
