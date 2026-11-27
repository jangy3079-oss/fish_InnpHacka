// 불러오는 중·오류 상태를 모든 화면에서 같은 모양으로 보여준다.

// 레이아웃 모양의 자리표시. 블록 높이를 실제 내용과 비슷하게 주어 화면이 갑자기 늘어나지 않게 한다.
export function Skeleton({ height = 80, width = '100%', className = '' }) {
  return <div className={`skeleton ${className}`} style={{ height, width }} aria-hidden="true" />;
}

export function LoadingBlock({ label = '불러오는 중입니다', children }) {
  return (
    <div role="status" aria-live="polite" aria-label={label}>
      {children}
    </div>
  );
}

// 오류 안내. 다시 시도할 방법(버튼)을 함께 준다.
export function ErrorNotice({ message = '불러오지 못했습니다. 잠시 후 다시 시도해 주세요.', onRetry }) {
  return (
    <div className="notice-error" role="alert">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  );
}
