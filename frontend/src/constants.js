// 경보 단계 표시 (정상/주의/경고/위험). 색만으로 구분하지 않도록 아이콘·글자를 함께 쓴다.
export const LEVELS = {
  정상: { color: '#1f8a4c', bg: '#e6f5ec', icon: '✔', message: '지금은 안정적입니다' },
  주의: { color: '#8a6d00', bg: '#fff6d6', icon: '!', message: '조금 살펴봐 주세요' },
  경고: { color: '#b45309', bg: '#ffe9d1', icon: '▲', message: '조치가 필요합니다' },
  위험: { color: '#b3261e', bg: '#fde3e1', icon: '⚠', message: '지금 바로 조치하세요' },
};

export const SOURCE_LABELS = {
  sonar: '소나',
  camera: '카메라',
  env: '수온·환경 센서',
};

export function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export const LEVEL_RANK = { 정상: 0, 주의: 1, 경고: 2, 위험: 3 };
