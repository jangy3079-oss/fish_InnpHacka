import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { DEFAULT_DEMO, MAX_T } from '../mocks/demo.js';

const DemoContext = createContext(null);
const STEP_MS = 1000; // 재생 중 1초에 1시간씩 진행

// 어민 화면·대시보드·알림이 같은 시나리오와 시각(t)을 따라가게 하는 공유 상태.
export function DemoProvider({ children }) {
  const [scenario, setScenario] = useState(DEFAULT_DEMO.scenario);
  const [t, setT] = useState(DEFAULT_DEMO.t);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return undefined;
    const id = setInterval(() => {
      setT((cur) => {
        if (cur >= MAX_T) {
          setPlaying(false);
          return cur;
        }
        return cur + 1;
      });
    }, STEP_MS);
    return () => clearInterval(id);
  }, [playing]);

  const value = useMemo(
    () => ({
      demo: { scenario, t },
      playing,
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
        if (t >= MAX_T) setT(0);
        setPlaying((p) => !p);
      },
    }),
    [scenario, t, playing],
  );
  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export const useDemo = () => useContext(DemoContext);
