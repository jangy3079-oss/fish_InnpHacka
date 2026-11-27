import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_DEMO, maxTOf } from '../mocks/demo.js';

const DemoContext = createContext(null);
const STEP_MS = 1000; // 재생 중 1초에 한 프레임씩 진행

// 어민 화면·대시보드·알림이 같은 시나리오와 시각(t, 프레임 번호)을 따라가게 하는 공유 상태.
export function DemoProvider({ children }) {
  const [scenario, setScenario] = useState(DEFAULT_DEMO.scenario);
  const [t, setT] = useState(DEFAULT_DEMO.t);
  const [playing, setPlaying] = useState(false);
  // 화면이 데이터를 불러오는 중이면 재생이 다음 칸으로 가지 않는다(서버는 요청을 하나씩 계산한다).
  const busyRef = useRef(false);
  const setBusy = useCallback((v) => {
    busyRef.current = v;
  }, []);
  const maxT = maxTOf(scenario); // 시나리오마다 프레임 수가 다를 수 있다

  useEffect(() => {
    if (!playing) return undefined;
    const id = setInterval(() => {
      if (busyRef.current) return;
      setT((cur) => {
        if (cur >= maxT) {
          setPlaying(false);
          return cur;
        }
        return cur + 1;
      });
    }, STEP_MS);
    return () => clearInterval(id);
  }, [playing, maxT]);

  const value = useMemo(
    () => ({
      demo: { scenario, t },
      maxT,
      playing,
      setBusy,
      selectScenario: (s) => {
        setScenario(s);
        setT(0);
        setPlaying(false);
      },
      setT: (v) => {
        setT(v);
        setPlaying(false);
      },
      togglePlay: () => {
        if (t >= maxT) setT(0);
        setPlaying((p) => !p);
      },
    }),
    [scenario, t, playing, maxT, setBusy],
  );
  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export const useDemo = () => useContext(DemoContext);
