# ============================================================
# FILE: main_auto_trading.py — execute_sell Section (lines ~8313-8830)
# PURPOSE: 매도 실행 + TRADE_RESULT 로그
#   - position 청산, 손익 계산
#   - exit_reason → reason_tag 매핑
#   - [TRADE_RESULT] 로그: pnl, reason_tag, eq_grade, choch_grade, hold_min, r_pct
#   - entry_quality_analyzer.py / exit_performance_analyzer.py 파싱 대상
# ============================================================

    def execute_sell(self, stock_code: str, price: float, profit_pct: float, reason: str, use_market_order: bool = False):
        """매도 실행 (전량 청산)"""
        position = self.positions.get(stock_code)
        if not position:
            return

        # 🔧 CRITICAL FIX: 장 시간 체크 (장 종료 후 주문 방지)
        if not self.is_market_open():
            current_time = datetime.now().strftime('%H:%M:%S')
            console.print(f"[red]❌ 장 종료 시간입니다 ({current_time})[/red]")
            console.print(f"[red]   종목 {stock_code} ({position.get('name', '')}): 주문 불가[/red]")
            console.print(f"[yellow]⚠️  내일 장 시작 시 수동으로 처리하세요.[/yellow]")
            return

        # 🔧 FIX: 점심시간 수익 청산 차단 (12:00-14:00)
        # 손절(profit_pct < 0)은 허용, 수익 청산만 차단
        from datetime import time as time_class
        current_time = datetime.now().time()
        MIDDAY_START = time_class(12, 0, 0)
        MIDDAY_END = time_class(14, 0, 0)

        if MIDDAY_START <= current_time < MIDDAY_END and profit_pct > 0:
            console.print(f"[yellow]🚫 점심시간 수익 청산 차단 ({current_time.strftime('%H:%M:%S')})[/yellow]")
            console.print(f"[yellow]   {position.get('name', '')} ({stock_code}): 수익률 {profit_pct:+.2f}%[/yellow]")
            console.print(f"[yellow]   14:00 이후 재시도 또는 손절 시에만 허용[/yellow]")
            return

        # 🔧 FIX: 실제 보유 수량 확인 (부분 청산 후 불일치 방지)
        try:
            account_info = self.api.get_account_info()
            if account_info and account_info.get('return_code') == 0:
                # 🔧 CRITICAL FIX: 올바른 API 응답 키 사용 (ka01690 명세)
                holdings = account_info.get('day_bal_rt', [])  # 'holdings' → 'day_bal_rt'
                actual_qty = 0
                for holding in holdings:
                    # 🔧 FIX: 올바른 필드명 사용
                    if holding.get('stk_cd') == stock_code:  # 'stock_code' → 'stk_cd'
                        actual_qty = int(holding.get('rmnd_qty', 0))  # 'quantity' → 'rmnd_qty'
                        break

                if actual_qty > 0 and actual_qty != position['quantity']:
                    console.print(f"[yellow]⚠️  수량 불일치 감지: 시스템 {position['quantity']}주 → 실제 {actual_qty}주[/yellow]")
                    position['quantity'] = actual_qty
                elif actual_qty == 0:
                    console.print(f"[red]❌ 보유 수량 0주: 이미 전량 청산됨[/red]")
                    del self.positions[stock_code]
                    return
        except Exception as e:
            console.print(f"[yellow]⚠️  보유 수량 확인 실패, 시스템 수량 사용: {e}[/yellow]")

        # entry_time이 없을 수 있으므로 안전하게 처리
        entry_time = position.get('entry_time') or position.get('entry_date')
        if entry_time:
            holding_duration = (datetime.now() - entry_time).seconds
        else:
            holding_duration = 0  # entry_time 없으면 0으로 설정

        realized_profit = (price - position['entry_price']) * position['quantity']

        console.print()
        console.print("=" * 80, style="red")
        console.print(f"🔔 매도 신호 발생: {position['name']} ({stock_code})", style="bold red")
        console.print(f"   매수가: {position['entry_price']:,.0f}원")
        console.print(f"   매도가: {price:,.0f}원")
        console.print(f"   매도수량: {position['quantity']}주")
        console.print(f"   수익률: {profit_pct:+.2f}%")
        console.print(f"   실현손익: {realized_profit:+,.0f}원")
        console.print(f"   사유: {reason}")
        console.print(f"   보유시간: {holding_duration // 60}분")

        # DB에 매도 정보 저장 (매수 시 생성한 trade 업데이트)
        trade_id = position.get('trade_id')
        if trade_id:
            # 매도 거래 추가 (SELL) - numpy 타입을 Python 기본 타입으로 변환
            exit_time_dt = datetime.now()
            sell_trade = {
                'stock_code': stock_code,
                'stock_name': position['name'],
                'trade_type': 'SELL',
                'trade_time': exit_time_dt.isoformat(),
                'price': float(price),
                'quantity': int(position['quantity']),
                'amount': float(price * position['quantity']),
                'exit_reason': reason,
                'realized_profit': float(realized_profit),
                'profit_rate': float(profit_pct),
                'holding_duration': int(holding_duration),
                # 🔧 2026-03-08: trade_duration + MFE 분석용 필드
                'entry_time': entry_time.isoformat() if entry_time else None,
                'exit_time': exit_time_dt.isoformat(),
                'holding_minutes': int(holding_duration // 60),
                'exit_context': {
                    'mfe_pct': round((position.get('highest_price', position['entry_price']) - position['entry_price']) / position['entry_price'] * 100, 3),
                    'exit_vs_mfe': round((price - position.get('highest_price', price)) / position['entry_price'] * 100, 3)
                }
            }
            self.db.insert_trade(sell_trade)

        # 의사결정 추적 — 청산 신호 기록 + ml_dataset 자동 생성
        try:
            from database.decision_trace import record_exit_signal
            record_exit_signal(
                stock_code=stock_code,
                stock_name=position['name'],
                exit_reason=reason,
                price=float(price),
                trade_id=position.get('trade_id'),
                entry_price=float(position['entry_price']),
                quantity=int(position['quantity']),
                holding_minutes=int(holding_duration // 60),
            )
        except Exception:
            pass

        # 실제 키움 API 매도 주문
        # 🔧 2026-04-15: 토큰 만료(8005) 시 재발급 후 1회 재시도 [EOD 강제청산 보호]
        order_result = None  # 🔧 FIX: 초기화 (NoneType 에러 방지)
        order_no = None
        sell_price = None    # 스코프 선언 — 재시도 시 재계산 방지

        for _sell_attempt in range(2):
            try:
                if use_market_order:
                    # Emergency Hard Stop: 시장가 주문
                    console.print(f"[red]📡 긴급 시장가 매도 주문 전송 중...[/red]")
                    order_result = self.api.order_sell(
                        stock_code=stock_code,
                        quantity=position['quantity'],
                        price=0,  # 시장가
                        trade_type="3"  # 시장가
                    )
                else:
                    # 일반 청산: 현재가 -0.5% 지정가 주문
                    console.print(f"[yellow]📡 키움 API 매도 주문 전송 중...[/yellow]")

                    # 🔧 CRITICAL FIX: 현재가 그대로 → 현재가 -0.5% + 호가단위 적용
                    if sell_price is None:
                        target_price = price * 0.995  # 현재가 -0.5%
                        sell_price = self._adjust_price_to_tick(target_price)  # 호가단위 조정
                        console.print(f"[dim]  지정가 설정: {sell_price:,}원 (현재가 {price:,}원의 99.5% → 호가단위 조정)[/dim]")

                    order_result = self.api.order_sell(
                        stock_code=stock_code,
                        quantity=position['quantity'],
                        price=sell_price,  # int(price) → sell_price
                        trade_type="0"  # 지정가
                    )
                break  # 주문 성공 — 루프 탈출

            except Exception as e:
                if _sell_attempt == 0 and ('8005' in str(e) or 'Token이 유효하지 않습니다' in str(e)):
                    # 🔧 2026-04-15: 토큰 만료 감지 → 재발급 후 재시도
                    logger.warning(f"[SELL_TOKEN_EXPIRED] {stock_code} 토큰 만료(8005) — 재발급 후 재시도")
                    console.print(f"[yellow]🔄 토큰 만료 감지 — 재발급 후 매도 재시도[/yellow]")
                    if not self.refresh_access_token():
                        logger.critical(f"[SELL_TOKEN_REFRESH_FAIL] {stock_code} 토큰 재발급 실패 — 수동 처리 필요")
                        console.print(f"[red]❌ 토큰 재발급 실패 — 수동 처리 필요[/red]")
                        return
                    # _sell_attempt=1 로 재시도
                else:
                    logger.error(f"[SELL_ERROR] {stock_code}: {e}")
                    console.print(f"[red]❌ 매도 API 호출 실패: {e}[/red]")
                    console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
                    import traceback
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")
                    return

        # 🔧 FIX: order_result가 None인 경우 처리
        if order_result is None:
            console.print(f"[red]❌ 매도 주문 응답 없음 (API 오류)[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        if order_result.get('return_code') != 0:
            console.print(f"[red]❌ 매도 주문 실패: {order_result.get('return_msg')}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        order_no = order_result.get('ord_no')
        console.print(f"[green]✓ 매도 주문 성공 - 주문번호: {order_no}[/green]")

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=position['name'],
            trade_type='SELL',
            quantity=position['quantity'],
            price=price,
            realized_pnl=realized_profit,
            reason=reason  # 매도 이유 전달
        )

        # 🔧 2026-03-07: Daily Loss Limit 누적
        # 🔥 2026-03-27: include_overnight:false → 오버나이트 포지션은 당일 daily_pnl 제외
        _dr_cfg = self.config.get('risk_control.daily_risk', {})
        _include_overnight = _dr_cfg.get('include_overnight', True)
        _is_overnight = False
        if not _include_overnight:
            _entry_time = position.get('entry_time') or position.get('entry_date')
            if _entry_time:
                _entry_date = _entry_time.date() if hasattr(_entry_time, 'date') else None
                if _entry_date and _entry_date < datetime.now().date():
                    _is_overnight = True
                    logger.info(f"[OVERNIGHT_PNL_SKIP] {stock_code} 오버나이트 손익 {profit_pct:+.2f}% → daily_pnl 제외")
        if not _is_overnight:
            self._daily_pnl_pct += profit_pct
        if _dr_cfg.get('enabled', True) and not self._daily_loss_halted:
            _limit = _dr_cfg.get('daily_loss_limit_pct', -3.0)
            if self._daily_pnl_pct <= _limit:
                self._daily_loss_halted = True
                logger.critical(
                    f"[DAILY_LOSS_LIMIT] 일일 누적 손실 {self._daily_pnl_pct:.2f}% <= {_limit}% "
                    f"→ 당일 거래 종료"
                )
                console.print(
                    f"[bold red]🚨 [DAILY_LOSS_LIMIT] 일일 손실 {self._daily_pnl_pct:.2f}% — 오늘 거래 종료![/bold red]"
                )

        # 🔧 FIX: 손실 스트릭 업데이트 및 쿨다운 설정
        is_win = profit_pct > 0

        if is_win:
            # 승리 → 스트릭 리셋
            self.stock_loss_streak[stock_code] = 0
            console.print(f"[green]✅ {position['name']}: 수익 거래로 손실 스트릭 초기화[/green]")
        else:
            # 손실 → 스트릭 증가
            self.stock_loss_streak[stock_code] = self.stock_loss_streak.get(stock_code, 0) + 1
            current_streak = self.stock_loss_streak[stock_code]

            # 🔥 ChatGPT Fix: 손절 시 allow_overnight False 처리
            # (당일 손절된 종목은 EOD 보유 대상에서 제외)
            position['allow_overnight'] = False

            console.print(f"[yellow]📉 {position['name']}: 연속 손실 {current_streak}회 (손실률: {profit_pct:.2f}%)[/yellow]")

            # 🔧 강화된 금지 로직 (2025-11-28 추가)
            should_ban = False
            ban_reason = ""

            # 1. 일일 -5% 이상 → 즉시 당일 금지
            if profit_pct <= -5.0:
                should_ban = True
                ban_reason = f"단일 거래 대손실 ({profit_pct:.2f}%)"
                console.print(f"[red]🚨 {position['name']}: 대손실 {profit_pct:.2f}% 발생![/red]")

            # 2. 2회 연속 -3% 이상 → 당일 금지
            elif current_streak >= 2 and profit_pct <= -3.0:
                should_ban = True
                ban_reason = f"{current_streak}회 연속 -3% 이상 손실"
                console.print(f"[red]🚨 {position['name']}: {current_streak}회 연속 중손실![/red]")

            # 3. 3회 연속 손실 → 당일 진입 금지 + 쿨다운 파일 생성
            elif current_streak >= self.max_consecutive_losses:
                should_ban = True
                ban_reason = f'{current_streak}회 연속 손실'

            # 금지 실행
            if should_ban:
                self.stock_ban_list.add(stock_code)
                console.print(f"[red]🚫 {position['name']}: {ban_reason}로 당일 진입 금지[/red]")

                # 🔧 쿨다운 파일 생성 (프로세스 간 공유)
                from pathlib import Path
                import json
                from datetime import timedelta

                cooldown_file = Path('data/cooldown.lock')
                cooldown_file.parent.mkdir(exist_ok=True)

                cooldown_until = (datetime.now() + timedelta(days=1)).isoformat()

                cooldown_data = {
                    'stock_code': stock_code,
                    'stock_name': position['name'],
                    'triggered_at': datetime.now().isoformat(),
                    'cooldown_until': cooldown_until,
                    'consecutive_losses': current_streak,
                    'loss_rate': profit_pct,
                    'reason': ban_reason
                }

                cooldown_file.write_text(json.dumps(cooldown_data, indent=2, ensure_ascii=False))
                console.print(f"[red]🔒 쿨다운 활성화: {cooldown_until[:10]}까지 모든 거래 중지[/red]")

            # 🔧 2026-02-07 v2: exit_reason 기반 차등 쿨다운
            is_loss = profit_pct < 0
            self.stock_cooldown[stock_code] = (datetime.now(), is_loss, reason)

            # 🔧 2026-02-10: Market Sensor — EF 발동 시 시장 상태 업데이트
            reason_cat = self._categorize_exit_reason(reason)
            if reason_cat in ('ef_no_follow', 'ef_no_demand'):
                ef_subtype = 'no_follow' if reason_cat == 'ef_no_follow' else 'no_demand'
                ms_config = self.config.get('re_entry.reentry_cooldown.market_sensor', {})
                ms_result = self.reentry_metrics.record_ef_event(ef_subtype, ms_config)
                if ms_result.get('message'):
                    console.print(f"[bold red]🔴 {ms_result['message']}[/bold red]")

            # 🔧 2026-02-16: Conservative Mode — Hard Stop 발동 시 보수 모드 활성화
            if reason_cat == 'hard_stop':
                cm_config = self.config.get('risk_control.conservative_mode', {})
                cm_result = self.reentry_metrics.record_hard_stop_event(
                    cm_config, symbol=position['name'], pnl_pct=profit_pct
                )
                if cm_result.get('message'):
                    console.print(f"[bold red]{cm_result['message']}[/bold red]")
                if cm_result.get('trading_halted'):
                    console.print(f"[bold red]🚨 당일 거래 종료! (Hard Stop {cm_result.get('hard_stop_count', 0)}회)[/bold red]")

            # 🔧 2026-02-19: Loss Streak Guard 상태 업데이트 (매도 후 연패 수 변동)
            self._check_loss_streak_guard()

            # v2: config 기반 쿨다운 시간 표시
            if self._cooldown_v2_enabled and self._cooldown_by_reason:
                reason_category = self._categorize_exit_reason(reason)
                cooldown_time = self._cooldown_by_reason.get(
                    reason_category,
                    self._cooldown_by_reason.get('default', 30)
                )
                console.print(f"[yellow]⏸️  {position['name']}: [{reason_category}] 쿨다운 {cooldown_time}분 시작[/yellow]")
            else:
                cooldown_time = self.loss_cooldown_minutes if is_loss else self.cooldown_minutes
                console.print(f"[yellow]⏸️  {position['name']}: 쿨다운 {cooldown_time}분 시작 ({'손절' if is_loss else '익절'})[/yellow]")

        # ✅ TradeStateManager에 매도 기록
        strategy_tag = position.get('strategy_tag', self.default_strategy_tag)  # ✅ 동적 기본값

        # 손절 여부 판단 (손실 + 특정 사유)
        is_stoploss = is_loss and any(keyword in reason.lower() for keyword in ['손절', 'stop', '하락', 'emergency'])

        if is_stoploss:
            # 손절 기록
            self.state_manager.mark_stoploss(
                stock_code=stock_code,
                stock_name=position['name'],
                entry_price=position['entry_price'],
                exit_price=price,
                reason=reason
            )
        else:
            # 일반 매도 기록
            self.state_manager.mark_traded(
                stock_code=stock_code,
                stock_name=position['name'],
                action=TradeAction.SELL,
                price=price,
                quantity=position['quantity'],
                strategy_tag=strategy_tag,
                reason=reason
            )

        # 포지션 제거
        del self.positions[stock_code]
        self._save_positions_state()  # 재시작 복원용 상태 갱신

        # ── 2026-03-31: DriftDetector + TradeStats 기록 ──────────────────────
        try:
            _entry_reason_str = position.get('entry_reason', '') or ''
            if _entry_reason_str.startswith('DEFENSIVE:'):
                _strat_tag = 'defensive'
            elif _entry_reason_str.startswith('SHORT:'):
                _strat_tag = 'short'
            elif 'TREND' in _entry_reason_str.upper():
                _strat_tag = 'trend'
            elif _entry_reason_str.startswith('RS:'):
                _strat_tag = 'rs'
            else:
                _strat_tag = 'smc'

            # 🔧 2026-04-03: 레짐 엔진 성과 기록 (DEF/RS 동적 사이징용)
            if _strat_tag == 'defensive':
                self.regime_engine.record_trade('def', profit_pct)
            elif _strat_tag == 'rs':
                self.regime_engine.record_trade('rs', profit_pct)

            # 🔧 2026-04-03: Drawdown 엔진 — 당일 누적 PnL 갱신 (전략별 분리)
            if self.config.get("drawdown_engine", {}).get("enabled", True):
                self.drawdown_engine.record_pnl(profit_pct, strategy=_strat_tag)

            self.drift_detector.record_trade(
                pnl_pct=profit_pct,
                strategy=_strat_tag,
                stock=stock_code,
                equity=self.current_cash,
            )
            self._trade_stats.record(profit_pct)
            logger.info(
                f"[DRIFT_REC] {stock_code} pnl={profit_pct:+.2f}% strat={_strat_tag} "
                f"drift={self.drift_detector.get_drift_level()[0].value}"
            )
        except Exception as _re:
            logger.debug(f"[DRIFT_REC_ERR] {stock_code}: {_re}")
        # ─────────────────────────────────────────────────────────────────────

        # ── 2026-04-01: A+ 결과 추적 ─────────────────────────────────────────
        try:
            if position.get('a_plus_mode', False) or _entry_reason_str.startswith('A+:'):
                _ap_result = 'WIN' if profit_pct > 0.3 else ('LOSS' if profit_pct < -0.3 else 'BE')
                logger.info(
                    f"[A_PLUS_RESULT:{_ap_result}] {stock_code} | "
                    f"pnl={profit_pct:+.2f}% | reason={reason[:30]} | "
                    f"today_count={self._daily_a_plus_count}"
                )
                console.print(
                    f"[{'green' if _ap_result == 'WIN' else 'red' if _ap_result == 'LOSS' else 'yellow'}]"
                    f"🎯 [A+ {_ap_result}] {position.get('name', stock_code)}: "
                    f"{profit_pct:+.2f}%[/]"
                )
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        # ── 2026-03-31: EXPLORATION 전용 통계 + 자동 kill ────────────────────
        try:
            if _entry_reason_str.startswith('EXPLORATION:'):
                _kill_cfg = self.config.get('exploration', {})
                _kill_wr_thr = _kill_cfg.get('auto_kill_winrate', 0.30)
                _kill_min_n  = _kill_cfg.get('auto_kill_min_count', 10)
                self._exploration_stats['count'] += 1
                if profit_pct > 0:
                    self._exploration_stats['wins'] += 1
                self._exploration_stats['total_pnl'] = (
                    self._exploration_stats.get('total_pnl', 0.0) + profit_pct
                )
                _expl_cnt = self._exploration_stats['count']
                _expl_wr  = self._exploration_stats['wins'] / _expl_cnt
                _expl_avg = self._exploration_stats['total_pnl'] / _expl_cnt
                logger.info(
                    f"[EXPLORATION_STATS] {stock_code} count={_expl_cnt} "
                    f"WR={_expl_wr:.0%} avg={_expl_avg:+.2f}%"
                )
                # 자동 kill 조건: 10건 이상 + 승률 30% 미만
                if (_expl_cnt >= _kill_min_n
                        and _expl_wr < _kill_wr_thr
                        and not self._exploration_killed):
                    self._exploration_killed = True
                    logger.critical(
                        f"[EXPLORATION_KILLED] 승률 {_expl_wr:.0%} < {_kill_wr_thr:.0%} "
                        f"({_expl_cnt}건) → EXPLORATION 자동 비활성화"
                    )
                    console.print(
                        f"[red bold]💀 [EXPLORATION_KILLED] 승률 {_expl_wr:.0%} "
                        f"→ 자동 OFF (YAML 확인 필요)[/red bold]"
                    )
                # 파일 저장 (세션 간 유지)
                try:
                    import json as _json_es
                    from pathlib import Path as _Path_es
                    _Path_es('data').mkdir(exist_ok=True)
                    _Path_es('data/exploration_stats.json').write_text(
                        _json_es.dumps({
                            'stats': self._exploration_stats,
                            'killed': self._exploration_killed,
                            'updated': datetime.now().isoformat(),
                        }, indent=2, ensure_ascii=False)
                    )
                except Exception:
                    pass
        except Exception as _expl_rec_e:
            logger.debug(f"[EXPLORATION_STATS_ERR] {stock_code}: {_expl_rec_e}")
        # ─────────────────────────────────────────────────────────────────────

        # ── capture_exit: 청산 지표 기록 ─────────────────────────────────────
        _cap_entry_time = position.get('entry_time') or position.get('entry_date')
        capture_exit(
            stock_code=stock_code,
            stock_name=position.get('name', ''),
            exit_price=price,
            entry_price=position.get('entry_price'),
            pnl_pct=profit_pct,
            exit_reason=reason,
            entry_time=_cap_entry_time,
        )
        # ─────────────────────────────────────────────────────────────────────

        # 🔧 2026-04-16: TRADE_RESULT 로그 (eq/choch 등급 포함 → entry_quality_analyzer 파싱용)
        try:
            import re as _re
            _reason_tag = 'OTHER'
            for _tag in ('TIME_EXIT', 'R_TP2', 'R_TP1', 'NO_MOVE_EXIT', 'HARD_STOP',
                         'TRAILING', 'VWAP', 'TIME', 'Early Failure'):
                if _tag.lower() in reason.lower():
                    _reason_tag = _tag
                    break
            logger.info(
                f"[TRADE_RESULT] {stock_code} | "
                f"pnl={profit_pct:+.2f}% | "
                f"reason_tag={_reason_tag} | "
                f"eq={position.get('eq_grade', '-')} | "
                f"choch={position.get('choch_grade_log', position.get('choch_grade', '-'))} | "
                f"hold={int(holding_duration // 60)}m | "
                f"r_pct={position.get('r_pct', 0):.2f}"
            )
        except Exception:
            pass

        console.print(f"✅ 매도 완료 (주문번호: {order_no})")
        console.print("=" * 80, style="red")
        console.print()

        # 잔고 업데이트 (비동기 실행)
        asyncio.create_task(self.update_account_balance())

    def load_candidates_from_db(self):
        """DB에서 활성 감시 종목 로드"""
        try:
            candidates = self.db.get_active_candidates(limit=100)

            if not candidates:
                console.print("  ⚠️  DB에 활성 감시 종목이 없습니다. 조건검색을 먼저 실행하세요.", style="yellow")
                return

            console.print(f"  ✅ DB에서 {len(candidates)}개 활성 종목 로드", style="green")

            # watchlist 및 validated_stocks 구성
            for candidate in candidates:
                stock_code = candidate['stock_code']
                stock_name = candidate['stock_name']
