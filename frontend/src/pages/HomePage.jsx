import { Link } from 'react-router-dom';
import HeroEchogram from '../components/HeroEchogram.jsx';
import { LEVELS } from '../constants.js';
import { usePageTitle } from '../usePageTitle.js';

// 경보 단계 기준 (config/thresholds.yaml): 정상 0~39, 주의 40~59, 경고 60~79, 위험 80~100
const SCALE = [
  { level: '정상', from: 0, to: 39 },
  { level: '주의', from: 40, to: 59 },
  { level: '경고', from: 60, to: 79 },
  { level: '위험', from: 80, to: 100 },
];

const STEPS = [
  { title: '관측', text: '소나와 카메라가 5분마다 물고기의 깊이, 움직임, 모여 있는 정도를 잽니다.' },
  { title: '판단', text: '평소와 비교해 스트레스 지수(0~100)를 계산하고 의심되는 원인을 추정합니다.' },
  { title: '알림', text: '지수가 올라 단계가 바뀌면 바로 알려드립니다.' },
  { title: '대응', text: '방법별 예상 손해를 비교해 가장 덜 잃는 대응을 추천합니다.' },
];

export default function HomePage() {
  usePageTitle('');
  return (
    <>
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-copy">
            <h1>가두리 물고기의 움직임으로 폐사 위험을 먼저 알려드립니다</h1>
            <p>
              소나와 카메라로 조피볼락이 평소와 다르게 움직이는 것을 찾아내고, 그 이유와 손해가 가장 적은 대응을 알려줍니다.
            </p>
            <div className="hero-actions">
              <Link to="/farmer" className="btn btn-light">어민 화면 열기</Link>
              <Link to="/dashboard" className="btn btn-ghost">지자체 대시보드 열기</Link>
            </div>
          </div>
          <HeroEchogram />
        </div>
      </section>

      <section className="section">
        <div className="section-inner">
          <h2>알림이 오기까지</h2>
          <ol className="steps">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <span className="step-n" aria-hidden="true">{i + 1}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section section-alt">
        <div className="section-inner">
          <h2>경보는 네 단계입니다</h2>
          <p className="lead">스트레스 지수가 0에서 100 사이로 나오고, 구간에 따라 단계가 정해집니다.</p>
          <div className="scale" role="list">
            {SCALE.map((s) => {
              const lv = LEVELS[s.level];
              return (
                <div key={s.level} role="listitem" className="scale-seg" style={{ flexGrow: s.to - s.from + 1, background: lv.bg, color: lv.color }}>
                  <strong><span aria-hidden="true">{lv.icon}</span> {s.level}</strong>
                  <span className="scale-range">{s.from}~{s.to}</span>
                  <span className="scale-msg">{lv.message}</span>
                </div>
              );
            })}
          </div>
          <p className="note">색만으로 구분하지 않도록 단계마다 기호와 문구를 함께 보여줍니다.</p>
        </div>
      </section>

      <section className="section">
        <div className="section-inner">
          <h2>이용하는 분에 따라 두 가지 화면이 있습니다</h2>
          <div className="audiences">
            <article>
              <h3>어민</h3>
              <p>내 가두리의 단계와 의심 원인, 지금 하면 좋은 조치 하나를 큰 글자로 보여줍니다. 판단 근거 그래프와 방법별 예상 손해도 펼쳐 볼 수 있습니다.</p>
              <p>재고를 입력하면 조치별 예상 손해를 계산합니다. 보험은 보상 적용이 확인된 경우에만 반영됩니다.</p>
              <Link to="/farmer" className="btn btn-dark">어민 화면 열기</Link>
            </article>
            <article>
              <h3>지자체 담당자</h3>
              <p>해역별 스트레스 지도와 원인 분포, 방류 수요 예측을 한 화면에서 봅니다.</p>
              <p>해역 단위 집계만 보여주고 개별 어가의 손익과 재고는 보이지 않습니다.</p>
              <Link to="/dashboard" className="btn btn-dark">지자체 대시보드 열기</Link>
            </article>
          </div>
        </div>
      </section>
    </>
  );
}
