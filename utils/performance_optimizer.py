#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
performance_optimizer.py

시스템 성능 최적화 및 메모리 관리 도구 - 개선된 버전
"""

import asyncio
import gc
import time
import weakref
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from functools import wraps
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# 성능 모니터링 라이브러리
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# 메모리 프로파일링
try:
    import tracemalloc
    TRACEMALLOC_AVAILABLE = True
except ImportError:
    TRACEMALLOC_AVAILABLE = False

from utils.logger import get_logger

@dataclass
class PerformanceMetrics:
    """성능 지표"""
    timestamp: datetime
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    disk_io_read: int = 0
    disk_io_write: int = 0
    network_sent: int = 0
    network_recv: int = 0
    active_threads: int = 0
    gc_collections: Dict[int, int] = None

    @property
    def memory_usage_mb(self) -> float:
        """memory_mb와 동일한 값을 반환하는 호환성 속성"""
        return self.memory_mb

    def __post_init__(self):
        if self.gc_collections is None:
            self.gc_collections = {0: 0, 1: 0, 2: 0}

@dataclass
class CacheEntry:
    """캐시 엔트리"""
    value: Any
    timestamp: datetime
    access_count: int = 0
    size_bytes: int = 0

@dataclass
class MemoryProfile:
    """메모리 프로파일"""
    current_mb: float
    peak_mb: float
    available_mb: float
    usage_pct: float
    largest_objects: List[Dict[str, Any]]

@dataclass
class TaskProfile:
    """작업 프로파일"""
    task_id: str
    start_time: datetime
    duration_ms: float
    memory_delta_mb: float
    status: str  # completed, failed, running
    error_msg: Optional[str] = None

class MemoryOptimizer:
    """메모리 최적화 관리자"""

    def __init__(self, max_memory_mb: float = 1024):
        """메모리 최적화 관리자 초기화"""
        self.logger = get_logger("MemoryOptimizer")
        self.max_memory_mb = max_memory_mb
        self.current_memory_mb = 0
        self.peak_memory_mb = 0

        # 메모리 추적 시작
        if TRACEMALLOC_AVAILABLE:
            try:
                tracemalloc.start()
            except RuntimeError:
                pass  # 이미 시작된 경우 무시

        # 약한 참조 캐시
        self._weak_cache: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

        # 메모리 정리 콜백
        self._cleanup_callbacks: List[Callable] = []

    def monitor_memory(self) -> MemoryProfile:
        """메모리 사용량 모니터링"""
        try:
            current_mb = 0
            available_mb = 0

            if PSUTIL_AVAILABLE:
                # 시스템 메모리 정보
                memory = psutil.virtual_memory()
                process = psutil.Process()
                process_memory = process.memory_info()

                # 현재 프로세스 메모리 사용량
                current_mb = process_memory.rss / 1024 / 1024
                available_mb = memory.available / 1024 / 1024
            else:
                # psutil이 없는 경우 기본값
                current_mb = 100  # 추정값
                available_mb = 1000

            self.current_memory_mb = current_mb

            if current_mb > self.peak_memory_mb:
                self.peak_memory_mb = current_mb

            # 큰 객체 추적
            largest_objects = []
            if TRACEMALLOC_AVAILABLE:
                try:
                    if tracemalloc.is_tracing():
                        snapshot = tracemalloc.take_snapshot()
                        top_stats = snapshot.statistics('lineno')[:10]

                        for stat in top_stats:
                            largest_objects.append({
                                "file": str(stat.traceback.format()[-1])[:100],  # 파일명 제한
                                "size_mb": stat.size / 1024 / 1024,
                                "count": stat.count
                            })
                except Exception:
                    pass  # tracemalloc 오류 무시

            profile = MemoryProfile(
                current_mb=current_mb,
                peak_mb=self.peak_memory_mb,
                available_mb=available_mb,
                usage_pct=(current_mb / self.max_memory_mb) * 100,
                largest_objects=largest_objects
            )

            # 메모리 사용량이 임계치를 초과하면 정리
            if profile.usage_pct > 80:
                self._trigger_cleanup()

            return profile

        except Exception as e:
            self.logger.error(f"❌ 메모리 모니터링 실패: {e}")
            return MemoryProfile(0, 0, 0, 0, [])

    def _trigger_cleanup(self):
        """메모리 정리 트리거"""
        try:
            self.logger.warning("⚠️ 메모리 사용량이 높아 정리를 수행합니다")

            # 등록된 정리 콜백 실행
            for callback in self._cleanup_callbacks:
                try:
                    callback()
                except Exception as e:
                    self.logger.error(f"❌ 정리 콜백 실행 실패: {e}")

            # 가비지 컬렉션 강제 실행
            collected = gc.collect()
            self.logger.info(f"🗑️ 가비지 컬렉션으로 {collected}개 객체 정리")

            # 캐시 정리
            self._weak_cache.clear()

        except Exception as e:
            self.logger.error(f"❌ 메모리 정리 실패: {e}")

    def register_cleanup_callback(self, callback: Callable):
        """메모리 정리 콜백 등록"""
        self._cleanup_callbacks.append(callback)

    def create_weak_cache(self, key: str, value: Any) -> bool:
        """약한 참조 캐시 생성"""
        try:
            self._weak_cache[key] = value
            return True
        except Exception:
            return False

    def get_weak_cache(self, key: str) -> Optional[Any]:
        """약한 참조 캐시 조회"""
        return self._weak_cache.get(key)

class AsyncTaskOptimizer:
    """비동기 작업 최적화 관리자"""

    def __init__(self, max_concurrent_tasks: int = 50):
        """비동기 작업 최적화 관리자 초기화"""
        self.logger = get_logger("AsyncTaskOptimizer")
        self.max_concurrent_tasks = max_concurrent_tasks

        # 작업 추적
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.task_profiles: List[TaskProfile] = []
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

        # 스레드 풀
        self.thread_pool = ThreadPoolExecutor(max_workers=4)

        # 성능 메트릭
        self.total_completed = 0
        self.total_failed = 0
        self.response_times: List[float] = []

    async def run_optimized_task(
        self,
        task_func: Callable,
        task_id: Optional[str] = None,
        timeout: Optional[float] = None,
        use_thread_pool: bool = False,
        *args,
        **kwargs
    ) -> Any:
        """
        최적화된 작업 실행

        Args:
            task_func: 실행할 함수
            task_id: 작업 ID (자동 생성 가능)
            timeout: 타임아웃 (초)
            use_thread_pool: 스레드 풀 사용 여부
            *args, **kwargs: 함수 인자

        Returns:
            작업 결과
        """
        if task_id is None:
            task_id = f"task_{datetime.now().timestamp()}"

        start_time = datetime.now()
        start_memory = 0

        if PSUTIL_AVAILABLE:
            start_memory = psutil.Process().memory_info().rss / 1024 / 1024

        async with self.semaphore:
            try:
                # 실행 방식 선택
                if use_thread_pool:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        self.thread_pool, task_func, *args
                    )
                else:
                    if asyncio.iscoroutinefunction(task_func):
                        if timeout:
                            result = await asyncio.wait_for(
                                task_func(*args, **kwargs), timeout=timeout
                            )
                        else:
                            result = await task_func(*args, **kwargs)
                    else:
                        result = task_func(*args, **kwargs)

                # 성공 메트릭 업데이트
                end_time = datetime.now()
                duration_ms = (end_time - start_time).total_seconds() * 1000
                end_memory = start_memory

                if PSUTIL_AVAILABLE:
                    end_memory = psutil.Process().memory_info().rss / 1024 / 1024

                profile = TaskProfile(
                    task_id=task_id,
                    start_time=start_time,
                    duration_ms=duration_ms,
                    memory_delta_mb=end_memory - start_memory,
                    status="completed"
                )

                self.task_profiles.append(profile)
                self.total_completed += 1
                self.response_times.append(duration_ms)

                # 최근 100개만 유지
                if len(self.response_times) > 100:
                    self.response_times = self.response_times[-100:]

                return result

            except Exception as e:
                # 실패 메트릭 업데이트
                end_time = datetime.now()
                duration_ms = (end_time - start_time).total_seconds() * 1000
                end_memory = start_memory

                if PSUTIL_AVAILABLE:
                    end_memory = psutil.Process().memory_info().rss / 1024 / 1024

                profile = TaskProfile(
                    task_id=task_id,
                    start_time=start_time,
                    duration_ms=duration_ms,
                    memory_delta_mb=end_memory - start_memory,
                    status="failed",
                    error_msg=str(e)
                )

                self.task_profiles.append(profile)
                self.total_failed += 1

                self.logger.error(f"❌ 작업 {task_id} 실패: {e}")
                raise

    def get_performance_stats(self) -> Dict[str, Any]:
        """성능 통계 조회"""
        try:
            active_count = len(self.active_tasks)

            if self.response_times:
                avg_response_time = sum(self.response_times) / len(self.response_times)
            else:
                avg_response_time = 0

            return {
                "active_tasks": active_count,
                "completed_tasks": self.total_completed,
                "failed_tasks": self.total_failed,
                "avg_response_time_ms": avg_response_time,
                "success_rate": (
                    self.total_completed / (self.total_completed + self.total_failed) * 100
                    if (self.total_completed + self.total_failed) > 0 else 0
                ),
                "recent_profiles": [
                    {
                        "task_id": p.task_id,
                        "duration_ms": p.duration_ms,
                        "status": p.status,
                        "memory_delta_mb": p.memory_delta_mb
                    }
                    for p in self.task_profiles[-10:]  # 최근 10개
                ]
            }

        except Exception as e:
            self.logger.error(f"❌ 성능 통계 조회 실패: {e}")
            return {}

    def cleanup(self):
        """리소스 정리"""
        try:
            self.thread_pool.shutdown(wait=True)
            self.logger.info("✅ 작업 최적화 관리자 정리 완료")
        except Exception as e:
            self.logger.error(f"❌ 작업 최적화 관리자 정리 실패: {e}")

class PerformanceProfiler:
    """성능 프로파일러"""

    def __init__(self, save_interval_minutes: int = 5):
        """성능 프로파일러 초기화"""
        self.logger = get_logger("PerformanceProfiler")
        self.save_interval = timedelta(minutes=save_interval_minutes)

        self.memory_optimizer = MemoryOptimizer()
        self.task_optimizer = AsyncTaskOptimizer()

        self.metrics_history: List[PerformanceMetrics] = []
        self.last_save_time = datetime.now()

        # 자동 저장 태스크
        self._auto_save_task: Optional[asyncio.Task] = None

    async def start_profiling(self):
        """프로파일링 시작"""
        try:
            self.logger.info("📊 성능 프로파일링 시작")
            self._auto_save_task = asyncio.create_task(self._auto_save_loop())
        except Exception as e:
            self.logger.error(f"❌ 프로파일링 시작 실패: {e}")

    async def stop_profiling(self):
        """프로파일링 정지"""
        try:
            if self._auto_save_task:
                self._auto_save_task.cancel()
                try:
                    await self._auto_save_task
                except asyncio.CancelledError:
                    pass

            # 최종 메트릭 저장
            await self.save_metrics()

            self.task_optimizer.cleanup()
            self.logger.info("✅ 성능 프로파일링 정지 완료")
        except Exception as e:
            self.logger.error(f"❌ 프로파일링 정지 실패: {e}")

    def collect_metrics(self) -> PerformanceMetrics:
        """현재 성능 메트릭 수집"""
        try:
            # 메모리 프로파일
            memory_profile = self.memory_optimizer.monitor_memory()

            # 작업 통계
            task_stats = self.task_optimizer.get_performance_stats()

            # CPU 사용률
            cpu_usage = 0
            if PSUTIL_AVAILABLE:
                cpu_usage = psutil.cpu_percent(interval=0.1)

            metrics = PerformanceMetrics(
                timestamp=datetime.now(),
                memory_usage_mb=memory_profile.current_mb,
                cpu_usage_pct=cpu_usage,
                active_tasks=task_stats.get("active_tasks", 0),
                completed_tasks=task_stats.get("completed_tasks", 0),
                failed_tasks=task_stats.get("failed_tasks", 0),
                avg_response_time_ms=task_stats.get("avg_response_time_ms", 0),
                peak_memory_mb=memory_profile.peak_mb,
                gc_collections=len(gc.get_stats())
            )

            self.metrics_history.append(metrics)

            # 히스토리 크기 제한 (최근 1000개)
            if len(self.metrics_history) > 1000:
                self.metrics_history = self.metrics_history[-1000:]

            return metrics

        except Exception as e:
            self.logger.error(f"❌ 메트릭 수집 실패: {e}")
            return PerformanceMetrics(
                datetime.now(), 0, 0, 0, 0, 0, 0, 0, 0
            )

    async def _auto_save_loop(self):
        """자동 저장 루프"""
        try:
            while True:
                await asyncio.sleep(60)  # 1분마다 체크

                current_time = datetime.now()
                if current_time - self.last_save_time >= self.save_interval:
                    await self.save_metrics()
                    self.last_save_time = current_time

        except asyncio.CancelledError:
            self.logger.info("자동 저장 루프가 취소되었습니다")
        except Exception as e:
            self.logger.error(f"❌ 자동 저장 루프 오류: {e}")

    async def save_metrics(self):
        """메트릭을 파일에 저장"""
        try:
            if not self.metrics_history:
                return

            # 최근 메트릭을 JSON으로 저장
            metrics_data = []
            for metric in self.metrics_history[-100:]:  # 최근 100개만
                metrics_data.append({
                    "timestamp": metric.timestamp.isoformat(),
                    "memory_usage_mb": metric.memory_usage_mb,
                    "cpu_usage_pct": metric.cpu_usage_pct,
                    "active_tasks": metric.active_tasks,
                    "completed_tasks": metric.completed_tasks,
                    "failed_tasks": metric.failed_tasks,
                    "avg_response_time_ms": metric.avg_response_time_ms,
                    "peak_memory_mb": metric.peak_memory_mb,
                    "gc_collections": metric.gc_collections
                })

            # 데이터 디렉토리 생성
            data_dir = Path("data")
            data_dir.mkdir(exist_ok=True)

            # 파일 저장
            metrics_file = data_dir / "performance_metrics.json"
            with open(metrics_file, 'w', encoding='utf-8') as f:
                json.dump(metrics_data, f, ensure_ascii=False, indent=2)

            self.logger.info(f"📊 성능 메트릭 저장 완료: {len(metrics_data)}건")

        except Exception as e:
            self.logger.error(f"❌ 메트릭 저장 실패: {e}")

    def get_performance_report(self) -> Dict[str, Any]:
        """성능 보고서 생성"""
        try:
            if not self.metrics_history:
                return {"status": "no_data"}

            recent_metrics = self.metrics_history[-10:]  # 최근 10개

            # 평균값 계산
            avg_memory = sum(m.memory_usage_mb for m in recent_metrics) / len(recent_metrics)
            avg_cpu = sum(m.cpu_usage_pct for m in recent_metrics) / len(recent_metrics)
            avg_response_time = sum(m.avg_response_time_ms for m in recent_metrics) / len(recent_metrics)

            # 최대값
            peak_memory = max(m.peak_memory_mb for m in recent_metrics)
            max_cpu = max(m.cpu_usage_pct for m in recent_metrics)

            # 작업 통계
            total_completed = recent_metrics[-1].completed_tasks if recent_metrics else 0
            total_failed = recent_metrics[-1].failed_tasks if recent_metrics else 0
            success_rate = (
                total_completed / (total_completed + total_failed) * 100
                if (total_completed + total_failed) > 0 else 0
            )

            return {
                "status": "available",
                "metrics_count": len(self.metrics_history),
                "averages": {
                    "memory_mb": avg_memory,
                    "cpu_pct": avg_cpu,
                    "response_time_ms": avg_response_time
                },
                "peaks": {
                    "memory_mb": peak_memory,
                    "cpu_pct": max_cpu
                },
                "tasks": {
                    "completed": total_completed,
                    "failed": total_failed,
                    "success_rate": success_rate
                },
                "recommendations": self._generate_recommendations(recent_metrics)
            }

        except Exception as e:
            self.logger.error(f"❌ 성능 보고서 생성 실패: {e}")
            return {"status": "error", "message": str(e)}

    def _generate_recommendations(self, metrics: List[PerformanceMetrics]) -> List[str]:
        """성능 개선 권고사항 생성"""
        recommendations = []

        if not metrics:
            return recommendations

        avg_memory = sum(m.memory_usage_mb for m in metrics) / len(metrics)
        avg_cpu = sum(m.cpu_usage_pct for m in metrics) / len(metrics)
        avg_response_time = sum(m.avg_response_time_ms for m in metrics) / len(metrics)

        # 메모리 사용량이 높은 경우
        if avg_memory > 512:  # 512MB 초과
            recommendations.append("메모리 사용량이 높습니다. 캐시 크기를 줄이거나 가비지 컬렉션을 더 자주 실행하세요.")

        # CPU 사용률이 높은 경우
        if avg_cpu > 80:
            recommendations.append("CPU 사용률이 높습니다. 작업을 더 작은 단위로 나누거나 비동기 처리를 개선하세요.")

        # 응답 시간이 긴 경우
        if avg_response_time > 1000:  # 1초 초과
            recommendations.append("응답 시간이 깁니다. 알고리즘 최적화나 캐싱을 고려하세요.")

        # 작업 실패율이 높은 경우
        if metrics:
            recent_metric = metrics[-1]
            if recent_metric.failed_tasks > 0:
                total_tasks = recent_metric.completed_tasks + recent_metric.failed_tasks
                failure_rate = recent_metric.failed_tasks / total_tasks * 100 if total_tasks > 0 else 0

                if failure_rate > 10:  # 10% 초과
                    recommendations.append("작업 실패율이 높습니다. 에러 처리와 재시도 로직을 개선하세요.")

        if not recommendations:
            recommendations.append("시스템이 양호한 성능을 보이고 있습니다.")

        return recommendations

# 데코레이터
def monitor_performance(profiler: PerformanceProfiler):
    """성능 모니터링 데코레이터"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if asyncio.iscoroutinefunction(func):
                return await profiler.task_optimizer.run_optimized_task(
                    func, f"{func.__name__}_{time.time()}", None, False, *args, **kwargs
                )
            else:
                return await profiler.task_optimizer.run_optimized_task(
                    func, f"{func.__name__}_{time.time()}", None, True, *args, **kwargs
                )
        return wrapper
    return decorator

# 글로벌 프로파일러 인스턴스
_global_profiler: Optional[PerformanceProfiler] = None

def get_global_profiler() -> PerformanceProfiler:
    """글로벌 프로파일러 인스턴스 반환"""
    global _global_profiler
    if _global_profiler is None:
        _global_profiler = PerformanceProfiler()
    return _global_profiler

# 호환성을 위한 별칭
PerformanceOptimizer = PerformanceProfiler

# 통합 사용 예시
async def demo_performance_optimization():
    """성능 최적화 시스템 데모"""
    logger = get_logger("PerformanceDemo")

    # 프로파일러 초기화
    profiler = PerformanceProfiler()

    try:
        # 프로파일링 시작
        await profiler.start_profiling()
        logger.info("🚀 성능 프로파일링 시작")

        # 테스트 작업 실행
        async def test_task(duration: float):
            await asyncio.sleep(duration)
            return f"작업 완료: {duration}초"

        # 여러 작업 동시 실행
        tasks = [
            profiler.task_optimizer.run_optimized_task(
                test_task, f"test_{i}", None, False, 0.1
            )
            for i in range(10)
        ]

        results = await asyncio.gather(*tasks)
        logger.info(f"✅ 작업 결과: {len(results)}개 완료")

        # 성능 메트릭 수집
        metrics = profiler.collect_metrics()
        logger.info(f"📊 메모리 사용량: {metrics.memory_usage_mb:.2f}MB")
        logger.info(f"⚡ CPU 사용률: {metrics.cpu_usage_pct:.2f}%")

        # 성능 보고서
        report = profiler.get_performance_report()
        logger.info(f"📈 성능 보고서: {report}")

        # 메모리 최적화 실행
        memory_profile = profiler.memory_optimizer.monitor_memory()
        logger.info(f"🧹 메모리 프로파일: {memory_profile.usage_pct:.1f}% 사용")

        return True

    except Exception as e:
        logger.error(f"❌ 성능 최적화 데모 실패: {e}")
        return False

    finally:
        # 프로파일링 정지
        await profiler.stop_profiling()
        logger.info("⏹️ 성능 프로파일링 정지 완료")

class MemoryManager:
    """메모리 관리자"""

    def __init__(self, max_memory_mb: float = 500):
        """
        메모리 관리자 초기화

        Args:
            max_memory_mb: 최대 메모리 사용량 (MB)
        """
        self.logger = get_logger("MemoryManager")
        self.max_memory_mb = max_memory_mb

        # 메모리 모니터링
        self.memory_history = deque(maxlen=100)
        self.weak_refs = weakref.WeakSet()

        # GC 통계
        self.gc_stats = {0: 0, 1: 0, 2: 0}

        # 메모리 임계값
        self.warning_threshold = 0.8  # 80%
        self.critical_threshold = 0.9  # 90%

    def get_memory_usage(self) -> Dict[str, float]:
        """현재 메모리 사용량 조회"""
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}

        try:
            import os
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()

            usage = {
                "rss_mb": memory_info.rss / 1024 / 1024,
                "vms_mb": memory_info.vms / 1024 / 1024,
                "percent": process.memory_percent(),
                "available_mb": psutil.virtual_memory().available / 1024 / 1024
            }

            self.memory_history.append({
                "timestamp": datetime.now(),
                "usage": usage
            })

            return usage

        except Exception as e:
            self.logger.error(f"❌ 메모리 사용량 조회 실패: {e}")
            return {"error": str(e)}

    def optimize_memory(self) -> Dict[str, Any]:
        """메모리 최적화 실행"""
        try:
            before_usage = self.get_memory_usage()

            # 1. 가비지 컬렉션
            gc_result = self.force_gc_collection()

            # 2. 메모리 압축 (Python 3.7+)
            if hasattr(gc, 'freeze'):
                gc.freeze()

            after_usage = self.get_memory_usage()

            saved_mb = before_usage.get("rss_mb", 0) - after_usage.get("rss_mb", 0)

            result = {
                "before_mb": before_usage.get("rss_mb", 0),
                "after_mb": after_usage.get("rss_mb", 0),
                "saved_mb": saved_mb,
                "gc_collected": gc_result,
                "weak_refs_count": len(self.weak_refs)
            }

            self.logger.info(f"🔧 메모리 최적화 완료: {saved_mb:.2f}MB 절약")
            return result

        except Exception as e:
            self.logger.error(f"❌ 메모리 최적화 실패: {e}")
            return {"error": str(e)}

    def force_gc_collection(self) -> Dict[str, int]:
        """강제 가비지 컬렉션"""
        try:
            collected = {}

            for generation in range(3):
                collected_count = gc.collect(generation)
                collected[f"gen_{generation}"] = collected_count
                self.gc_stats[generation] += collected_count

            self.logger.info(f"🗑️ 가비지 컬렉션 완료: {collected}")
            return collected

        except Exception as e:
            self.logger.error(f"❌ 가비지 컬렉션 실패: {e}")
            return {"error": str(e)}

# 글로벌 인스턴스
_memory_manager = None
_performance_monitor = None

def get_memory_manager() -> MemoryManager:
    """메모리 관리자 싱글톤 인스턴스"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager

def performance_profile(func):
    """성능 프로파일링 데코레이터"""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = 0

        if PSUTIL_AVAILABLE:
            import os
            process = psutil.Process(os.getpid())
            start_memory = process.memory_info().rss / 1024 / 1024

        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            end_time = time.time()
            execution_time = end_time - start_time

            end_memory = 0
            if PSUTIL_AVAILABLE:
                end_memory = process.memory_info().rss / 1024 / 1024

            logger = get_logger("PerformanceProfiler")
            logger.info(
                f"⚡ {func.__name__} 실행 완료: "
                f"시간={execution_time:.3f}s, "
                f"메모리={end_memory-start_memory:+.2f}MB"
            )

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = 0

        if PSUTIL_AVAILABLE:
            import os
            process = psutil.Process(os.getpid())
            start_memory = process.memory_info().rss / 1024 / 1024

        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.time()
            execution_time = end_time - start_time

            end_memory = 0
            if PSUTIL_AVAILABLE:
                end_memory = process.memory_info().rss / 1024 / 1024

            logger = get_logger("PerformanceProfiler")
            logger.info(
                f"⚡ {func.__name__} 실행 완료: "
                f"시간={execution_time:.3f}s, "
                f"메모리={end_memory-start_memory:+.2f}MB"
            )

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

# 사용 예시
async def main():
    """테스트 함수"""
    # 메모리 관리
    memory_mgr = get_memory_manager()
    memory_usage = memory_mgr.get_memory_usage()
    print(f"메모리 사용량: {memory_usage}")

    # 메모리 최적화
    optimization_result = memory_mgr.optimize_memory()
    print(f"최적화 결과: {optimization_result}")

if __name__ == "__main__":
    asyncio.run(main())