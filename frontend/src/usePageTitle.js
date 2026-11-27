import { useEffect } from 'react';
import { SITE_NAME } from './constants.js';

// 브라우저 탭 제목: "알림 | 서비스 이름". 제목이 없으면 서비스 이름만 쓴다.
export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} | ${SITE_NAME}` : SITE_NAME;
  }, [title]);
}
