"""데이터 파이프라인 일괄 실행과 API용 산출물 (#83).

    python -m pipeline                    # 시나리오 생성 → M5 학습 → 채점 → 경보 단계 → API 파일
    from pipeline.timeline import Timeline
    tl = Timeline.load("heatwave_typhoon")
    tl.status_at(t=120.0)                 # /demo/scenario/{name}?t= , /cages/{id}/status
    tl.timeseries("activity", "2026-08-09T00:00:00Z", "2026-08-10T00:00:00Z")
"""
