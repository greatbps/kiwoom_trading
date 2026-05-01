# ============================================================
# FILE: main_auto_trading.py — SMC Entry Section (lines ~5700-6120)
# PURPOSE: SMC 진입 로직 핵심 구간
#   - CHoCH grade (A/A+/A-/B) 판별
#   - Grade별 시간 제한 (B급: 11:30, A급: 12:30)
#   - structure_stop_price → R 계산 (1R, TP1=1.5R, TP2=3R)
#   - ENTRY_QUALITY 등급 (A/B/C) 산출 + [ENTRY_QUALITY] 로그
#   - position dict에 eq_grade, choch_grade, r_pct, r_tp1_price, r_tp2_price 저장
# ============================================================

                        logger.info(
                            f"{_fallback_tag} {stock_code} | CHoCH+OB | "
                            f"size_mult={position_size_mult:.2f} | vol_ok={_vol_ok} | htf={_htf_alive} | "
                            f"daily={self._daily_fallback_count}/{_max_fb}"
                            + (f" | c_daily={self._daily_c_fallback_count}" if _is_c_fallback else "")
                        )
                        console.print(
                            f"[yellow]⚡ {_fallback_tag} {stock_name}: CHoCH+OB 진입 "
                            f"({'C급 reclaim✓' if _is_c_fallback else 'sweep없음'}, "
                            f"size×{position_size_mult:.2f}, {self._daily_fallback_count}/{_max_fb})[/yellow]"
                        )

                    # 등급 정보 로깅
                    choch_grade_info = details.get('choch_grade', {})
                    choch_grade = choch_grade_info.get('grade', 'B')
                    if weight_multiplier < 1.0:
                        logger.debug(f"[GRADE_WEIGHT] {stock_code} {choch_grade}급 {weight_multiplier*100:.0f}%")

                    # ── 2026-04-01: A+ 분류 + A급 품질 필터 ─────────────────────
                    _aplus_cfg  = self.config.get('smc.choch_grade.grade_a_plus',  {})
                    _afilt_cfg  = self.config.get('smc.choch_grade.grade_a_filter', {})
                    _has_sweep  = bool(details.get('has_sweep', False))
                    _is_a_plus  = False

                    # RVOL + 3봉 수익률 계산 (5분봉 기준)
                    _rvol_ag      = 0.0
                    _3bar_rise_ag = 0.0
                    try:
                        _df_ag = df.copy()
                        _df_ag.columns = [c.lower() for c in _df_ag.columns]
                        if 'volume' in _df_ag.columns and len(_df_ag) >= 21:
                            _vol_avg_ag = _df_ag['volume'].iloc[-21:-1].mean()
                            _vol_cur_ag = _df_ag['volume'].iloc[-1]
                            if _vol_avg_ag > 0:
                                _rvol_ag = _vol_cur_ag / _vol_avg_ag
                        if 'close' in _df_ag.columns and len(_df_ag) >= 4:
                            _c_now = float(_df_ag['close'].iloc[-1])
                            _c_3b  = float(_df_ag['close'].iloc[-4])
                            if _c_3b > 0:
                                _3bar_rise_ag = (_c_now / _c_3b) - 1.0
                    except Exception:
                        pass

                    if choch_grade == 'A' and _aplus_cfg.get('enabled', True):
                        _ap_conf_thr  = _aplus_cfg.get('conf_threshold', 0.75)
                        _ap_rvol_thr  = _aplus_cfg.get('min_rvol', 1.8)
                        _ap_sweep_req = _aplus_cfg.get('require_sweep', True)
                        _is_a_plus = (
                            entry_confidence >= _ap_conf_thr
                            and _rvol_ag >= _ap_rvol_thr
                            and (not _ap_sweep_req or _has_sweep)
                        )
                        if _is_a_plus:
                            # ── A+ 가드 1: 최근 3봉 급등 → 꼭대기 물림 방지 ──────
                            _ap_max_rise = _aplus_cfg.get('max_3bar_rise', 0.04)
                            if _3bar_rise_ag > _ap_max_rise:
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_REJECT_OVEREXTENDED] {stock_code} | "
                                    f"3봉수익={_3bar_rise_ag*100:.1f}% > {_ap_max_rise*100:.0f}% → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"3봉급등 {_3bar_rise_ag*100:.1f}% → 꼭대기 물림 방지, A급 처리[/yellow]"
                                )

                        if _is_a_plus and _aplus_cfg.get('require_bull_market', True):
                            # ── A+ 가드 2: 시장 레짐 TREND 아니면 강등 ──────────
                            try:
                                _ap_regime, _ap_regime_reason = self.market_context.get_regime()
                            except Exception:
                                _ap_regime = "NEUTRAL"
                                _ap_regime_reason = "오류"
                            if _ap_regime != "TREND":
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_REJECT_MARKET] {stock_code} | "
                                    f"regime={_ap_regime} ({_ap_regime_reason}) → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"시장 {_ap_regime} (TREND 아님) → A급 처리[/yellow]"
                                )

                        if _is_a_plus:
                            # ── A+ 쿨다운: RVOL 기반 동적 조정 ──────────────────
                            # 시장 속도(RVOL)에 따라 쿨다운 길이 조절
                            _ap_cd_default  = _aplus_cfg.get('cooldown_min', 15)
                            _ap_cd_high_vol = _aplus_cfg.get('cooldown_min_high_vol', 10)  # RVOL≥2.0
                            _ap_cd_low_vol  = _aplus_cfg.get('cooldown_min_low_vol', 25)   # RVOL≤1.2
                            if _rvol_ag >= 2.0:
                                _ap_cooldown_min = _ap_cd_high_vol
                            elif _rvol_ag <= 1.2:
                                _ap_cooldown_min = _ap_cd_low_vol
                            else:
                                _ap_cooldown_min = _ap_cd_default

                            if self._last_a_plus_time is not None:
                                _ap_elapsed = (datetime.now() - self._last_a_plus_time).total_seconds() / 60
                                if _ap_elapsed < _ap_cooldown_min:
                                    _is_a_plus = False
                                    logger.info(
                                        f"[A_PLUS_COOLDOWN] {stock_code} | "
                                        f"직전 A+ 후 {_ap_elapsed:.0f}분 < {_ap_cooldown_min}분 "
                                        f"(rvol={_rvol_ag:.1f}x) → A로 강등"
                                    )
                                    console.print(
                                        f"[yellow]⚠️ [A+→A] {stock_name}: "
                                        f"쿨다운 {_ap_elapsed:.0f}/{_ap_cooldown_min}분 (RVOL={_rvol_ag:.1f}x)[/yellow]"
                                    )

                        if _is_a_plus:
                            # ── A+ 일일 한도 초과 → A로 강등 ────────────────────
                            _ap_max_per_day = _aplus_cfg.get('max_per_day', 2)
                            if self._daily_a_plus_count >= _ap_max_per_day:
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_DAILY_LIMIT] {stock_code} | "
                                    f"일일 A+ 한도 ({self._daily_a_plus_count}/{_ap_max_per_day}) → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"A+ 일일 한도 {_ap_max_per_day}회 → 과열 방지, A급 처리[/yellow]"
                                )

                        if _is_a_plus:
                            choch_grade = 'A+'
                            position_size_mult = 1.0   # Tier 1: Full Size
                            # 카운터는 execute_buy 성공 후 증가 (entry_reason A+ prefix로 판단)
                            logger.info(
                                f"[A_PLUS] {stock_code} | conf={entry_confidence:.2f} "
                                f"rvol={_rvol_ag:.1f}x sweep={_has_sweep} "
                                f"3bar={_3bar_rise_ag*100:.1f}% → A+ 1.0R "
                                f"(today={self._daily_a_plus_count}/{_ap_max_per_day})"
                            )
                            console.print(
                                f"[bold green]🔥 [A+] {stock_name}: "
                                f"conf={entry_confidence:.2f} RVOL={_rvol_ag:.1f}x → 1.0R FULL SIZE[/bold green]"
                            )
                        else:
                            # A급 품질 필터 → A / A- 분리
                            _a_min_rvol = _afilt_cfg.get('min_rvol', 1.5)
                            if _rvol_ag > 0 and _rvol_ag < _a_min_rvol:
                                logger.info(
                                    f"[A_DOWNGRADE] {stock_code} | RVOL={_rvol_ag:.1f}x < {_a_min_rvol} → B급 강등"
                                )
                                choch_grade = 'B'
                            elif not _has_sweep:
                                # sweep 없는 A급 → A- (0.3R, skip 아님, 흐름 유지)
                                _no_sweep_mult = _afilt_cfg.get('no_sweep_size_mult', 0.3)
                                position_size_mult = _no_sweep_mult
                                choch_grade = 'A-'
                                logger.info(
                                    f"[A_MINUS] {stock_code} | sweep없음 → A- {_no_sweep_mult*100:.0f}%"
                                )
                            else:
                                # A / A- 분리: conf + RVOL 기준
                                _a_tier_conf  = _afilt_cfg.get('tier_a_conf_threshold', 0.70)
                                _a_tier_rvol  = _afilt_cfg.get('tier_a_rvol_threshold', 1.5)
                                _is_real_a = (entry_confidence >= _a_tier_conf and _rvol_ag >= _a_tier_rvol)
                                if _is_real_a:
                                    position_size_mult = _afilt_cfg.get('tier_a_size_mult', 0.5)
                                    logger.info(
                                        f"[A_TIER2] {stock_code} | conf={entry_confidence:.2f} "
                                        f"rvol={_rvol_ag:.1f}x → A 0.5R"
                                    )
                                else:
                                    position_size_mult = _afilt_cfg.get('no_sweep_size_mult', 0.3)
                                    choch_grade = 'A-'
                                    logger.info(
                                        f"[A_MINUS] {stock_code} | conf={entry_confidence:.2f} "
                                        f"rvol={_rvol_ag:.1f}x (기준 미달) → A- 0.3R"
                                    )

                    # B급 Tier 3: 0.2R (흐름 유지 + 데이터 축적)
                    if choch_grade == 'B':
                        _b_tier_mult = _afilt_cfg.get('tier_b_size_mult', 0.2)
                        position_size_mult = _b_tier_mult
                        logger.info(f"[B_TIER3] {stock_code} | B급 Tier3 → {_b_tier_mult*100:.0f}% size")
                    # ─────────────────────────────────────────────────────────────

                    # 🔧 2026-02-19: B급 CHoCH 시간 제한
                    if choch_grade == 'B':
                        grade_b_cutoff_str = self.config.get('smc.choch_grade.grade_b_cutoff', '11:00')
                        h_cut, m_cut = map(int, grade_b_cutoff_str.split(':'))
                        from datetime import time as time_class
                        if datetime.now().time() >= time_class(h_cut, m_cut, 0):
                            logger.debug(f"[B_CUTOFF] {stock_code} {grade_b_cutoff_str} 이후 차단")
                            return

                    # 🔧 2026-02-26: HTF❌ + B급 CHoCH → 진입 금지 (관찰 전환)
                    if choch_grade == 'B' and self.config.get('smc.choch_grade.htf_b_block', True):
                        htf_alive = details.get('htf_trend_alive', True)
                        if not htf_alive:
                            logger.debug(f"[HTF_B_BLOCK] {stock_code}")
                            logger.info(f"[HTF_B_BLOCK] {stock_code} {stock_name}: B급 CHoCH + HTF 미정렬 → 진입 차단")
                            self._record_blocked_entry(stock_code, stock_name, "HTF_B_BLOCK", "B급 CHoCH + HTF 미정렬", "SMC")
                            return

                    # 🔧 2026-02-06: 구조 기반 손절가 저장
                    structure_stop_price = details.get('structure_stop_price')
                    if structure_stop_price:
                        console.print(f"[cyan]📍 구조 손절가: {structure_stop_price:,.0f}원[/cyan]")

                    # 진입 이유 생성 (A+ → prefix로 execute_buy bypass 전달)
                    choch_info = details.get('choch', {})
                    _reason_base = f"{datetime.now().strftime('%H:%M')} SMC {reason}"
                    entry_reason = f"A+:{_reason_base}" if _is_a_plus else _reason_base

                except Exception as e:
                    console.print(f"[red]❌ {stock_name}: SMC 전략 처리 실패 - {e}[/red]")
                    import traceback
                    traceback.print_exc()
                    return

            elif entry_mode == "legacy_only":
                # ========================================
                # 모드 2: 기존 필터만 사용 (스퀴즈 무시)
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 기존 필터 (L0-L6)[/cyan]")

                signal_result = self.signal_orchestrator.evaluate_signal(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    current_price=current_price,
                    df=df,
                    market=market,
                    current_cash=self.current_cash,
                    daily_pnl=self.calculate_daily_pnl()
                )

                if not signal_result['allowed']:
                    level = signal_result['rejection_level']
                    reason = signal_result['rejection_reason']
                    logger.debug(f"[LEVEL_BLOCK] {stock_code}: {level} - {reason[:40]}")
                    return

                entry_confidence = signal_result['confidence']
                position_size_mult = signal_result['position_size_multiplier']

            else:  # hybrid (기본값)
                # ========================================
                # 모드 3: 하이브리드 (기존 필터 + 스퀴즈)
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 하이브리드 (기존 + 스퀴즈)[/cyan]")

                # 기존 필터 체크
                signal_result = self.signal_orchestrator.evaluate_signal(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    current_price=current_price,
                    df=df,
                    market=market,
                    current_cash=self.current_cash,
                    daily_pnl=self.calculate_daily_pnl()
                )

                if not signal_result['allowed']:
                    level = signal_result['rejection_level']
                    reason = signal_result['rejection_reason']
                    logger.debug(f"[LEVEL_BLOCK] {stock_code}: {level} - {reason[:40]}")
                    return

                # 추가로 스퀴즈 모멘텀 체크
                if squeeze_config.get('enabled', False) and squeeze_config.get('entry_filter', {}).get('enabled', False):
                    from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter

                    sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=True)

                    if not sqz_passed:
                        logger.debug(f"[SQZ_BLOCK] {stock_code}: {sqz_reason}")
                        console.print(f"[dim]  색상: {sqz_details.get('color', 'N/A')}, 모멘텀: {sqz_details.get('momentum', 0):.2f}[/dim]")
                        return
                    else:
                        console.print(f"[green]✅ {stock_name}: Squeeze 통과 - {sqz_reason}[/green]")

                entry_confidence = signal_result['confidence']
                position_size_mult = signal_result['position_size_multiplier']

            # 4. 매수 실행 (Phase 1: Confidence-based)
            console.print(f"[green]✅ {stock_name} ({stock_code}): 매수 시그널 발생![/green]")
            console.print(f"  신뢰도: {entry_confidence*100:.0f}%, 포지션 조정: {position_size_mult*100:.0f}%")

            # entry_reason이 설정되지 않은 모드는 기본값 사용
            if entry_reason is None:
                entry_reason = f"{datetime.now().strftime('%H:%M')} {entry_mode} 모드 진입"

            # 🔧 2026-03-08: SMC OB Pullback Entry — 즉시 매수 대신 OB zone 대기
            # CHoCH 신호 확인 후 OB(Order Block) 레벨까지 pullback 대기 → 반응 확인 후 진입
            # 데이터 근거: 10~30분 승률 7.2% (CHoCH 직후 추격) vs 30~60분 승률 61.5% (OB retest)
            if entry_mode == 'smc':
                ob_cfg = self.config.get('smc.ob_pullback_entry', {})
                ob_info = details.get('order_block') if details else None
                if ob_cfg.get('enabled', True) and ob_info and stock_code not in self.smc_pending:
                    # OB 정보 저장 후 대기 (execute_buy 미호출)
                    self.smc_pending[stock_code] = {
                        'ob_high':           ob_info['high'],
                        'ob_low':            ob_info['low'],
                        'choch_price':       details.get('choch', {}).get('price', current_price),
                        'detected_at':       datetime.now(),
                        'grade':             choch_grade,
                        'position_size_mult': position_size_mult,
                        'confidence':        entry_confidence,
                    }
                    console.print(
                        f"[cyan]⏳ [SMC_PENDING] {stock_name}: {choch_grade}급 CHoCH 확인, "
                        f"OB({ob_info['low']:,.0f}~{ob_info['high']:,.0f}) pullback 대기[/cyan]"
                    )
                    logger.info(
                        f"[SMC_PENDING] {stock_code} {stock_name}: {choch_grade}급, "
                        f"OB({ob_info['low']:.0f}~{ob_info['high']:.0f}), "
                        f"CHoCH={details.get('choch',{}).get('price',0):.0f}"
                    )
                    return  # execute_buy 호출 안 함 (OB 대기)
                # OB 없거나 이미 pending 중이면 기존 즉시 진입

            # 🔧 2026-04-06: 신호 품질 메타 — capture_entry 에서 signal_meta JSONB로 저장
            _cm_rpt = self.reentry_metrics.generate_report()
            self._pending_signal_meta = {
                "choch_grade":   choch_grade if 'choch_grade' in dir() else None,
                "htf_bias":      (details.get('mtf_bias', {}).get('is_uptrend') if details else None),
                "sweep":         bool(details.get('liquidity_sweep')) if details else None,
                "guard_state":   (
                    "lsg"          if _cm_rpt.get("loss_streak_guard_active") else
                    "conservative" if _cm_rpt.get("conservative_mode_active") else
                    "normal"
                ),
                "conf":          round(entry_confidence, 3),
                "size_mult":     round(position_size_mult, 3),
            }

            # 신호 큐에 추가 (detect→execute 분리)
            self._emit_signal(stock_code, stock_name, current_price, df, position_size_mult, entry_confidence, entry_reason, stop_loss=structure_stop_price, strategy=entry_mode.upper())

            # 🔧 2026-03-18: SMC ENTRY 결정 로그
            if entry_mode == "smc" and stock_code in self.positions:
                try:
                    from analyzers.smc.smc_decision_logger import get_smc_logger as _gsmc
                    _ob = self.smc_pending.get(stock_code, {})
                    _gsmc().log_entry(
                        stock_code, stock_name, current_price,
                        ob_low=_ob.get('ob_low', 0), ob_high=_ob.get('ob_high', 0),
                        grade=choch_grade if 'choch_grade' in dir() else '',
                    )
                except Exception:
                    pass

            # 🔧 2026-02-06: SMC 진입 시 추가 정보를 포지션에 저장
            if entry_mode == "smc" and stock_code in self.positions:
                try:
                    # 구조 기반 손절가 저장 (structure_stop_price는 SMC 분기에서 details로부터 추출됨)
                    if structure_stop_price is not None:
                        self.positions[stock_code]['structure_stop_price'] = structure_stop_price
                        console.print(f"[cyan]📍 {stock_name}: 구조 손절가 {structure_stop_price:,.0f}원 저장[/cyan]")

                    # HTF 추세 일치 여부 저장 (조건부 보유 시간 연장용)
                    mtf_bias_info = details.get('mtf_bias', {})
                    self.positions[stock_code]['htf_trend_aligned'] = mtf_bias_info.get('is_uptrend', False)
                    self.positions[stock_code]['direction'] = 'long'

                    # 🔧 2026-02-15: CHoCH 등급 저장 (오버나이트 강제 청산 판단용)
                    self.positions[stock_code]['choch_grade'] = choch_grade  # 'A' or 'B'

                    # 🔧 2026-02-07: 진입 시 ATR 저장 (Early Failure Structure 필터용)
                    if 'atr' in df_5min.columns and len(df_5min) > 0:
                        self.positions[stock_code]['atr_at_entry'] = float(df_5min['atr'].iloc[-1])

                    # 🔧 2026-04-16: R-기반 TP 계산 (SL=1R, TP1=1.5R, TP2=3R)
                    if structure_stop_price is not None and current_price > structure_stop_price:
                        _r_tp_cfg = self.config.get('smc.r_tp', {})
                        _tp1_mult = _r_tp_cfg.get('tp1_r_mult', 1.5)
                        _tp2_mult = _r_tp_cfg.get('tp2_r_mult', 3.0)
                        _r_pct = (current_price - structure_stop_price) / current_price * 100
                        _r_tp1 = current_price * (1 + _tp1_mult * _r_pct / 100)
                        _r_tp2 = current_price * (1 + _tp2_mult * _r_pct / 100)
                        self.positions[stock_code]['r_pct'] = round(_r_pct, 3)
                        self.positions[stock_code]['r_tp1_price'] = round(_r_tp1)
                        self.positions[stock_code]['r_tp2_price'] = round(_r_tp2)

                    # 🔧 2026-04-16: ENTRY QUALITY 로그 (EMA9 눌림 메트릭)
                    _eq = details.get('ema9_wait', {})
                    if _eq.get('passed'):
                        _r_pct_log   = self.positions[stock_code].get('r_pct', 0)
                        _tp1_log     = self.positions[stock_code].get('r_tp1_price', 0)
                        _tp2_log     = self.positions[stock_code].get('r_tp2_price', 0)
                        _atr_dist    = _eq.get('atr_distance', 999)
                        _depth       = _eq.get('depth_ratio', 0)
                        _vol_r       = _eq.get('vol_ratio', 999)
                        if _atr_dist <= 0.15 and _depth >= 0.4 and _vol_r < 0.6:
                            _eq_grade = 'A'
                        elif _atr_dist <= 0.25 and _depth >= 0.3:
                            _eq_grade = 'B'
                        else:
                            _eq_grade = 'C'
                        # position에 저장 → execute_sell 시 TRADE_RESULT 로그에 사용
                        self.positions[stock_code]['eq_grade'] = _eq_grade
                        self.positions[stock_code]['choch_grade_log'] = choch_grade
                        logger.info(
                            f"[ENTRY_QUALITY] {stock_code} {stock_name} | "
                            f"choch={choch_grade} eq={_eq_grade} | "
                            f"bars_since={_eq.get('bars_since_choch', '?')} | "
                            f"atr_dist={_atr_dist} | "
                            f"depth={_depth:.0%} | "
                            f"vol_ratio={_vol_r} | "
                            f"1R={_r_pct_log:.2f}% | "
                            f"TP1={_tp1_log:,.0f} TP2={_tp2_log:,.0f}"
                        )
                except Exception:
                    self.positions[stock_code]['htf_trend_aligned'] = False
                    self.positions[stock_code]['direction'] = 'long'

        except Exception as e:
            console.print(f"[red]❌ {stock_code} 매수 신호 체크 실패: {e}[/red]")
            import traceback
            traceback.print_exc()

    def check_exit_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
        """매도 신호 체크 - 최적화된 청산 로직 사용"""
        try:
            logger.debug(f"[EXIT_CHECK] {stock_code} 매도 신호 체크 시작")

