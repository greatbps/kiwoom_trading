"""
SQLite → PostgreSQL 데이터 마이그레이션 스크립트
"""
import sqlite3
import psycopg2
from psycopg2.extras import execute_batch
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def migrate_data():
    """SQLite 데이터를 PostgreSQL로 마이그레이션"""

    # SQLite 연결
    sqlite_conn = sqlite3.connect('data/trading.db')
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    # PostgreSQL 연결
    pg_conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD')
    )
    pg_cursor = pg_conn.cursor()

    print("=" * 80)
    print("SQLite → PostgreSQL 데이터 마이그레이션")
    print("=" * 80)

    # 1. trades 테이블 마이그레이션
    print("\n📊 1. trades 테이블 마이그레이션...")
    try:
        sqlite_cursor.execute("SELECT * FROM trades")
        trades = sqlite_cursor.fetchall()
        print(f"   SQLite에서 {len(trades)}건 조회")

        if len(trades) > 0:
            insert_query = """
                INSERT INTO trades (
                    stock_code, stock_name, trade_type, trade_time,
                    price, quantity, amount,
                    condition_name, strategy_config, entry_reason, exit_reason,
                    vwap_validation_score, sim_win_rate, sim_avg_profit,
                    sim_trade_count, sim_profit_factor,
                    news_sentiment, news_impact, news_keywords, news_titles,
                    realized_profit, profit_rate, holding_duration,
                    entry_context, exit_context, filter_scores,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """

            data = []
            skipped = 0
            for row in trades:
                # 타입 변환 함수
                def safe_float(val, default=0.0):
                    if val is None:
                        return default
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return default

                def safe_int(val, default=0):
                    if val is None:
                        return default
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return default

                # 필수 필드 검증
                price = safe_float(row['price'])
                quantity = safe_int(row['quantity'])
                amount = safe_float(row['amount'])

                if price == 0.0 or quantity == 0:
                    skipped += 1
                    continue

                data.append((
                    row['stock_code'],
                    row['stock_name'],
                    row['trade_type'],
                    row['trade_time'],
                    price,
                    quantity,
                    amount,
                    row['condition_name'],
                    row['strategy_config'],
                    row['entry_reason'],
                    row['exit_reason'],
                    safe_float(row['vwap_validation_score'], None),
                    safe_float(row['sim_win_rate'], None),
                    safe_float(row['sim_avg_profit'], None),
                    safe_int(row['sim_trade_count'], None),
                    safe_float(row['sim_profit_factor'], None),
                    row['news_sentiment'],
                    row['news_impact'],
                    row['news_keywords'],
                    row['news_titles'],
                    safe_float(row['realized_profit'], None),
                    safe_float(row['profit_rate'], None),
                    safe_int(row['holding_duration'], None),
                    row['entry_context'],
                    row['exit_context'],
                    row['filter_scores'],
                    row['created_at']
                ))

            execute_batch(pg_cursor, insert_query, data)
            pg_conn.commit()
            print(f"   ✅ {len(data)}건 마이그레이션 완료")
        else:
            print("   ⚠️  데이터 없음")
    except sqlite3.OperationalError:
        print("   ⚠️  테이블이 존재하지 않음")
    except Exception as e:
        print(f"   ❌ 실패: {e}")
        pg_conn.rollback()

    # 2. filter_history 테이블 마이그레이션
    print("\n📊 2. filter_history 테이블 마이그레이션...")
    try:
        sqlite_cursor.execute("SELECT * FROM filter_history")
        records = sqlite_cursor.fetchall()
        print(f"   SQLite에서 {len(records)}건 조회")

        if len(records) > 0:
            insert_query = """
                INSERT INTO filter_history (
                    filter_time, filter_type, condition_name,
                    stocks_found, stock_codes,
                    stocks_passed, stocks_failed, passed_stocks,
                    schedule_type, is_new_stock,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            data = []
            for row in records:
                data.append((
                    row['filter_time'],
                    row['filter_type'],
                    row['condition_name'],
                    row['stocks_found'],
                    row['stock_codes'],
                    row['stocks_passed'],
                    row['stocks_failed'],
                    row['passed_stocks'],
                    row['schedule_type'],
                    row['is_new_stock'],
                    row['created_at']
                ))

            execute_batch(pg_cursor, insert_query, data)
            pg_conn.commit()
            print(f"   ✅ {len(data)}건 마이그레이션 완료")
        else:
            print("   ⚠️  데이터 없음")
    except sqlite3.OperationalError:
        print("   ⚠️  테이블이 존재하지 않음")
    except Exception as e:
        print(f"   ❌ 실패: {e}")
        pg_conn.rollback()

    # 3. simulations 테이블 마이그레이션
    print("\n📊 3. simulations 테이블 마이그레이션...")
    try:
        sqlite_cursor.execute("SELECT * FROM simulations")
        records = sqlite_cursor.fetchall()
        print(f"   SQLite에서 {len(records)}건 조회")

        if len(records) > 0:
            insert_query = """
                INSERT INTO simulations (
                    stock_code, stock_name, simulation_time, lookback_days,
                    total_trades, win_rate, avg_profit_rate,
                    profit_factor, max_profit, max_loss,
                    news_sentiment, news_impact, news_keywords, news_titles, news_score,
                    trade_details,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            data = []
            for row in records:
                data.append((
                    row['stock_code'],
                    row['stock_name'],
                    row['simulation_time'],
                    row['lookback_days'],
                    row['total_trades'],
                    row['win_rate'],
                    row['avg_profit_rate'],
                    row['profit_factor'],
                    row['max_profit'],
                    row['max_loss'],
                    row['news_sentiment'],
                    row['news_impact'],
                    row['news_keywords'],
                    row['news_titles'],
                    row['news_score'],
                    row['trade_details'],
                    row['created_at']
                ))

            execute_batch(pg_cursor, insert_query, data)
            pg_conn.commit()
            print(f"   ✅ {len(data)}건 마이그레이션 완료")
        else:
            print("   ⚠️  데이터 없음")
    except sqlite3.OperationalError:
        print("   ⚠️  테이블이 존재하지 않음")
    except Exception as e:
        print(f"   ❌ 실패: {e}")
        pg_conn.rollback()

    # 4. validation_scores 테이블 마이그레이션
    print("\n📊 4. validation_scores 테이블 마이그레이션...")
    try:
        sqlite_cursor.execute("SELECT * FROM validation_scores")
        records = sqlite_cursor.fetchall()
        print(f"   SQLite에서 {len(records)}건 조회")

        if len(records) > 0:
            insert_query = """
                INSERT INTO validation_scores (
                    stock_code, stock_name, validation_time,
                    vwap_win_rate, vwap_avg_profit, vwap_trade_count,
                    vwap_profit_factor, vwap_max_profit, vwap_max_loss,
                    news_sentiment_score, news_impact_type, news_keywords, news_titles, news_count,
                    total_score, weight_vwap, weight_news, is_passed,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            data = []
            for row in records:
                data.append((
                    row['stock_code'],
                    row['stock_name'],
                    row['validation_time'],
                    row['vwap_win_rate'],
                    row['vwap_avg_profit'],
                    row['vwap_trade_count'],
                    row['vwap_profit_factor'],
                    row['vwap_max_profit'],
                    row['vwap_max_loss'],
                    row['news_sentiment_score'],
                    row['news_impact_type'],
                    row['news_keywords'],
                    row['news_titles'],
                    row['news_count'],
                    row['total_score'],
                    row['weight_vwap'],
                    row['weight_news'],
                    row['is_passed'],
                    row['created_at']
                ))

            execute_batch(pg_cursor, insert_query, data)
            pg_conn.commit()
            print(f"   ✅ {len(data)}건 마이그레이션 완료")
        else:
            print("   ⚠️  데이터 없음")
    except sqlite3.OperationalError:
        print("   ⚠️  테이블이 존재하지 않음")
    except Exception as e:
        print(f"   ❌ 실패: {e}")
        pg_conn.rollback()

    # 5. filtered_candidates 테이블 마이그레이션
    print("\n📊 5. filtered_candidates 테이블 마이그레이션...")
    try:
        sqlite_cursor.execute("SELECT * FROM filtered_candidates")
        records = sqlite_cursor.fetchall()
        print(f"   SQLite에서 {len(records)}건 조회")

        if len(records) > 0:
            columns = [description[0] for description in sqlite_cursor.description]
            print(f"   컬럼 수: {len(columns)}")

            # INSERT 쿼리 동적 생성
            placeholders = ', '.join(['%s'] * len(columns))
            insert_query = f"INSERT INTO filtered_candidates ({', '.join(columns)}) VALUES ({placeholders})"

            data = []
            for row in records:
                data.append(tuple(row))

            execute_batch(pg_cursor, insert_query, data, page_size=100)
            pg_conn.commit()
            print(f"   ✅ {len(data)}건 마이그레이션 완료")
        else:
            print("   ⚠️  데이터 없음")
    except sqlite3.OperationalError:
        print("   ⚠️  테이블이 존재하지 않음")
    except Exception as e:
        print(f"   ❌ 실패: {e}")
        pg_conn.rollback()

    # 연결 종료
    sqlite_conn.close()
    pg_cursor.close()
    pg_conn.close()

    print("\n" + "=" * 80)
    print("✅ 마이그레이션 완료!")
    print("=" * 80)


if __name__ == "__main__":
    migrate_data()
