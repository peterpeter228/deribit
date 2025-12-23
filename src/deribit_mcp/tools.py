"""
MCP Tools implementation for Deribit API.

All tools return compact JSON (≤2KB target).
Each tool handles errors gracefully with degraded responses.
"""

import logging
import time
from typing import Any, Literal

from .analytics import (
    OptionData,
    SkewMetrics,
    aggregate_oi_by_strike,
    analyze_term_structure_shape,
    calculate_butterfly,
    calculate_expected_move,
    calculate_gamma_exposure_profile,
    calculate_imbalance,
    calculate_iv_term_structure_slope,
    calculate_max_pain,
    calculate_risk_reversal,
    days_to_expiry_from_ts,
    determine_skew_direction,
    determine_skew_trend,
    dvol_to_decimal,
    estimate_25d_strike,
    find_oi_peak_range,
    find_oi_peaks,
    spread_in_bps,
)
from .analytics import (
    TermStructurePoint as TSPoint,
)
from .client import DeribitError, DeribitJsonRpcClient, get_client
from .config import Currency, InstrumentKind, get_settings
from .models import (
    AccountSummaryResponse,
    DvolResponse,
    ExpectedMoveResponse,
    FundingEntry,
    FundingResponse,
    GammaExposureResponse,
    GreeksCompact,
    InstrumentCompact,
    InstrumentsResponse,
    IVTermStructureResponse,
    MaxPainResponse,
    OIPeakInfo,
    OpenInterestByStrikeResponse,
    OpenOrdersResponse,
    OptionChainResponse,
    OptionStrikeData,
    OrderBookSummaryResponse,
    OrderCompact,
    PainCurvePoint,
    PlaceOrderRequest,
    PlaceOrderResponse,
    PositionCompact,
    PositionsResponse,
    PriceLevel,
    SkewMetricsResponse,
    StatusResponse,
    StrikeGEX,
    StrikeOIData,
    SurfaceResponse,
    TenorIV,
    TenorSkew,
    TermStructurePoint,
    TickerResponse,
)
from .models import (
    ErrorResponseLegacy as ErrorResponse,
)

logger = logging.getLogger(__name__)


def _current_ts_ms() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely convert value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_or_none(value: float | None, decimals: int = 6) -> float | None:
    """Round value if not None."""
    if value is None:
        return None
    return round(value, decimals)


# =============================================================================
# Tool 1: deribit_status
# =============================================================================


async def deribit_status(
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Check Deribit API connectivity and status.

    Returns:
        StatusResponse with environment, connectivity status, and server time.
    """
    client = client or get_client()
    settings = get_settings()
    notes: list[str] = []
    api_ok = False
    server_time_ms = 0

    try:
        # Get server time (validates connectivity)
        time_result = await client.call_public("public/get_time")
        server_time_ms = time_result
        api_ok = True

        # Try to get status info
        try:
            status_result = await client.call_public("public/status")
            if status_result.get("locked"):
                notes.append("platform_locked")
        except DeribitError:
            # Status endpoint might not be available, that's ok
            pass

        # Check cache stats
        cache_stats = client.get_cache_stats()
        if cache_stats["total_entries"] > 0:
            notes.append(f"cache_entries:{cache_stats['total_entries']}")

    except DeribitError as e:
        notes.append(f"error:{e.code}")
        notes.append(e.message[:50])
    except Exception as e:
        notes.append(f"connection_error:{type(e).__name__}")

    return StatusResponse(
        env=settings.env.value,
        api_ok=api_ok,
        server_time_ms=server_time_ms,
        notes=notes[:6],
    ).model_dump()


# =============================================================================
# Tool 2: deribit_instruments
# =============================================================================


async def deribit_instruments(
    currency: Currency,
    kind: InstrumentKind = "option",
    expired: bool = False,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get available instruments for a currency.

    Returns compact instrument list (max 50 items), prioritizing
    nearest expirations for options.

    Args:
        currency: BTC or ETH
        kind: option or future
        expired: Include expired instruments

    Returns:
        InstrumentsResponse with trimmed instrument list.
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        result = await client.call_public(
            "public/get_instruments",
            {
                "currency": currency,
                "kind": kind,
                "expired": expired,
            },
        )

        instruments_raw = result if isinstance(result, list) else []
        total_count = len(instruments_raw)

        if total_count > 50:
            notes.append(f"truncated_from:{total_count}")

            # For options, prioritize nearest expirations
            if kind == "option":
                current_ts = _current_ts_ms()
                # Group by expiration
                by_expiry: dict[int, list] = {}
                for inst in instruments_raw:
                    exp = inst.get("expiration_timestamp", 0)
                    if exp not in by_expiry:
                        by_expiry[exp] = []
                    by_expiry[exp].append(inst)

                # Sort expirations and take nearest 3
                sorted_expiries = sorted([e for e in by_expiry if e > current_ts])[:3]

                # Collect instruments from these expirations
                filtered = []
                for exp in sorted_expiries:
                    filtered.extend(by_expiry[exp])

                instruments_raw = filtered[:50]
                notes.append(f"nearest_{len(sorted_expiries)}_expiries")
            else:
                instruments_raw = instruments_raw[:50]

        # Convert to compact format
        instruments = []
        for inst in instruments_raw:
            instruments.append(
                InstrumentCompact(
                    name=inst.get("instrument_name", ""),
                    exp_ts=inst.get("expiration_timestamp", 0),
                    strike=_safe_float(inst.get("strike")),
                    type=inst.get("option_type"),
                    tick=inst.get("tick_size", 0),
                    size=inst.get("contract_size", 0),
                )
            )

        return InstrumentsResponse(
            count=total_count,
            instruments=instruments,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", f"kind:{kind}"],
        ).model_dump()


# =============================================================================
# Tool 3: deribit_ticker
# =============================================================================


async def deribit_ticker(
    instrument_name: str,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get compact ticker snapshot for an instrument.

    Args:
        instrument_name: Full instrument name (e.g., BTC-PERPETUAL, BTC-28JUN24-70000-C)

    Returns:
        TickerResponse with essential market data.
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        result = await client.call_public("public/ticker", {"instrument_name": instrument_name})

        # Extract greeks if available
        greeks = None
        greeks_data = result.get("greeks")
        if greeks_data:
            greeks = GreeksCompact(
                delta=_round_or_none(_safe_float(greeks_data.get("delta")), 4),
                gamma=_round_or_none(_safe_float(greeks_data.get("gamma")), 6),
                vega=_round_or_none(_safe_float(greeks_data.get("vega")), 4),
                theta=_round_or_none(_safe_float(greeks_data.get("theta")), 4),
            )

        # Calculate mid price
        bid = _safe_float(result.get("best_bid_price"))
        ask = _safe_float(result.get("best_ask_price"))
        mid = None
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2

        # Get IV (convert from percentage to decimal if needed)
        iv = _safe_float(result.get("mark_iv"))
        if iv is not None and iv > 1:
            iv = iv / 100  # Convert from percentage
            notes.append("iv_pct_converted")

        # Get funding rate for perpetuals
        funding = None
        next_funding_ts = None
        if "PERPETUAL" in instrument_name.upper():
            funding = _safe_float(result.get("current_funding"))
            next_funding_ts = result.get("funding_8h")

        return TickerResponse(
            inst=instrument_name,
            bid=_round_or_none(bid, 2),
            ask=_round_or_none(ask, 2),
            mid=_round_or_none(mid, 2),
            mark=_round_or_none(_safe_float(result.get("mark_price")), 4),
            idx=_round_or_none(_safe_float(result.get("index_price")), 2),
            und=_round_or_none(_safe_float(result.get("underlying_price")), 2),
            iv=_round_or_none(iv, 4),
            greeks=greeks,
            oi=_round_or_none(_safe_float(result.get("open_interest")), 2),
            vol_24h=_round_or_none(_safe_float(result.get("stats", {}).get("volume")), 2),
            funding=_round_or_none(funding, 8),
            next_funding_ts=next_funding_ts,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"instrument:{instrument_name}"],
        ).model_dump()


# =============================================================================
# Tool 4: deribit_orderbook_summary
# =============================================================================


async def deribit_orderbook_summary(
    instrument_name: str,
    depth: int = 20,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get order book summary with top levels and depth metrics.

    Does NOT return full orderbook - only key metrics for LLM consumption.

    Args:
        instrument_name: Full instrument name
        depth: Depth to fetch (max 20, we return max 5 levels)

    Returns:
        OrderBookSummaryResponse with top levels and imbalance metrics.
    """
    client = client or get_client()
    notes: list[str] = []
    depth = min(depth, 20)  # Cap at 20

    try:
        result = await client.call_public(
            "public/get_order_book",
            {
                "instrument_name": instrument_name,
                "depth": depth,
            },
        )

        # Extract bids/asks
        raw_bids = result.get("bids", [])
        raw_asks = result.get("asks", [])

        # Top 5 levels only
        bids = [PriceLevel(p=round(b[0], 4), q=round(b[1], 4)) for b in raw_bids[:5]]
        asks = [PriceLevel(p=round(a[0], 4), q=round(a[1], 4)) for a in raw_asks[:5]]

        # Calculate depth sums
        bid_depth = sum(b[1] for b in raw_bids[:depth])
        ask_depth = sum(a[1] for a in raw_asks[:depth])

        # Best bid/ask
        best_bid = _safe_float(result.get("best_bid_price"))
        best_ask = _safe_float(result.get("best_ask_price"))

        # Spread calculations
        spread_pts = None
        spread_bps_val = None
        if best_bid and best_ask and best_bid > 0:
            spread_pts = best_ask - best_bid
            spread_bps_val = spread_in_bps(best_bid, best_ask)

        # Imbalance
        imbalance = calculate_imbalance(bid_depth, ask_depth)

        if len(raw_bids) > 5 or len(raw_asks) > 5:
            notes.append(f"levels_truncated_from:{max(len(raw_bids), len(raw_asks))}")

        return OrderBookSummaryResponse(
            inst=instrument_name,
            bid=_round_or_none(best_bid, 4),
            ask=_round_or_none(best_ask, 4),
            spread_pts=_round_or_none(spread_pts, 4),
            spread_bps=_round_or_none(spread_bps_val, 2),
            bids=bids,
            asks=asks,
            bid_depth=round(bid_depth, 4),
            ask_depth=round(ask_depth, 4),
            imbalance=_round_or_none(imbalance, 4),
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"instrument:{instrument_name}"],
        ).model_dump()


# =============================================================================
# Tool 5: dvol_snapshot
# =============================================================================


async def dvol_snapshot(
    currency: Currency,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get DVOL (Deribit Volatility Index) snapshot.

    DVOL represents the 30-day implied volatility derived from
    Deribit's options market.

    Args:
        currency: BTC or ETH

    Returns:
        DvolResponse with current DVOL and metrics.
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Try to get DVOL index data
        result = None
        try:
            result = await client.call_public(
                "public/get_volatility_index_data",
                {
                    "currency": currency,
                    "resolution": "1D",  # Daily resolution
                    "start_timestamp": _current_ts_ms() - 86400000,  # Last 24h
                    "end_timestamp": _current_ts_ms(),
                },
            )
        except DeribitError:
            # Fallback: try ticker for DVOL instrument
            try:
                ticker = await client.call_public(
                    "public/ticker", {"instrument_name": f"{currency}_DVOL"}
                )
                if ticker:
                    dvol_value = _safe_float(ticker.get("mark_price"))
                    if dvol_value:
                        return DvolResponse(
                            ccy=currency,
                            dvol=round(dvol_value, 2),
                            dvol_chg_24h=None,
                            percentile=None,
                            ts=_current_ts_ms(),
                            notes=["source:ticker_fallback"],
                        ).model_dump()
            except DeribitError:
                pass

        if result and result.get("data"):
            data = result["data"]
            # Data is array of [timestamp, open, high, low, close]
            if len(data) > 0:
                latest = data[-1]
                dvol_now = latest[4] if len(latest) > 4 else latest[-1]

                # Calculate 24h change if we have enough data
                dvol_chg = None
                if len(data) >= 2:
                    prev_close = data[0][4] if len(data[0]) > 4 else data[0][-1]
                    if prev_close and prev_close > 0:
                        dvol_chg = dvol_now - prev_close

                return DvolResponse(
                    ccy=currency,
                    dvol=round(dvol_now, 2),
                    dvol_chg_24h=_round_or_none(dvol_chg, 2),
                    percentile=None,  # Would need historical data
                    ts=_current_ts_ms(),
                    notes=notes[:6],
                ).model_dump()

        # If we get here, no DVOL data available
        notes.append("dvol_unavailable")
        notes.append("try_options_surface_for_iv")

        return DvolResponse(
            ccy=currency,
            dvol=0,
            dvol_chg_24h=None,
            percentile=None,
            ts=_current_ts_ms(),
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", "dvol_fetch_failed"],
        ).model_dump()


# =============================================================================
# Tool 6: options_surface_snapshot
# =============================================================================


async def options_surface_snapshot(
    currency: Currency,
    tenor_days: list[int] | None = None,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get volatility surface snapshot with ATM IV, risk reversal, and butterfly
    for key tenors.

    This is a derived metric that requires multiple API calls. Results are
    cached aggressively to minimize API load.

    Args:
        currency: BTC or ETH
        tenor_days: Target tenors in days (default: [7, 14, 30, 60])

    Returns:
        SurfaceResponse with IV by tenor and skew metrics.
    """
    client = client or get_client()
    notes: list[str] = []
    tenor_days = tenor_days or [7, 14, 30, 60]

    try:
        # Get index price first
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            notes.append("spot_price_unavailable")
            return SurfaceResponse(
                ccy=currency,
                spot=0,
                tenors=[],
                confidence=0,
                ts=_current_ts_ms(),
                notes=notes[:6],
            ).model_dump()

        # Get options instruments
        instruments_result = await client.call_public(
            "public/get_instruments", {"currency": currency, "kind": "option", "expired": False}
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []
        current_ts = _current_ts_ms()

        # Group by expiration
        by_expiry: dict[int, list] = {}
        for opt in all_options:
            exp = opt.get("expiration_timestamp", 0)
            if exp <= current_ts:
                continue
            if exp not in by_expiry:
                by_expiry[exp] = []
            by_expiry[exp].append(opt)

        # Find expirations closest to target tenors
        tenors_result: list[TenorIV] = []
        matched_expiries = 0

        for target_days in tenor_days[:4]:  # Max 4 tenors
            # Find closest expiration
            best_exp = None
            best_distance = float("inf")

            for exp_ts in by_expiry:
                days = days_to_expiry_from_ts(exp_ts, current_ts)
                distance = abs(days - target_days)
                if distance < best_distance and distance < target_days * 0.5:
                    best_distance = distance
                    best_exp = exp_ts

            if best_exp is None:
                tenors_result.append(
                    TenorIV(
                        days=target_days,
                        atm_iv=None,
                        rr25=None,
                        fly25=None,
                        fwd=None,
                    )
                )
                continue

            matched_expiries += 1
            actual_days = days_to_expiry_from_ts(best_exp, current_ts)
            expiry_options = by_expiry[best_exp]

            # Find ATM option (closest strike to spot)
            atm_strike = None
            atm_iv = None
            min_distance = float("inf")

            for opt in expiry_options:
                strike = _safe_float(opt.get("strike"))
                if strike is None:
                    continue
                distance = abs(strike - spot)
                if distance < min_distance:
                    min_distance = distance
                    atm_strike = strike

            # Get ATM IV from ticker
            if atm_strike:
                atm_call_name = f"{currency}-{_format_expiry(best_exp)}-{int(atm_strike)}-C"
                try:
                    atm_ticker = await client.call_public(
                        "public/ticker", {"instrument_name": atm_call_name}
                    )
                    iv = _safe_float(atm_ticker.get("mark_iv"))
                    if iv and iv > 1:
                        iv = iv / 100
                    atm_iv = iv
                except DeribitError:
                    notes.append(f"atm_ticker_failed:{target_days}d")

            # Estimate 25d options for RR/Fly (simplified)
            # In practice, would need to find actual 25d strikes
            rr25 = None
            fly25 = None

            # For simplicity, estimate 25d strikes as ATM ± 5-10%
            if atm_strike and atm_iv:
                call_25d_strike = int(atm_strike * 1.05)
                put_25d_strike = int(atm_strike * 0.95)

                try:
                    call_name = f"{currency}-{_format_expiry(best_exp)}-{call_25d_strike}-C"
                    put_name = f"{currency}-{_format_expiry(best_exp)}-{put_25d_strike}-P"

                    call_ticker = await client.call_public(
                        "public/ticker", {"instrument_name": call_name}
                    )
                    put_ticker = await client.call_public(
                        "public/ticker", {"instrument_name": put_name}
                    )

                    call_iv = _safe_float(call_ticker.get("mark_iv"))
                    put_iv = _safe_float(put_ticker.get("mark_iv"))

                    if call_iv and call_iv > 1:
                        call_iv = call_iv / 100
                    if put_iv and put_iv > 1:
                        put_iv = put_iv / 100

                    if call_iv and put_iv:
                        rr25 = calculate_risk_reversal(call_iv, put_iv)
                        fly25 = calculate_butterfly(call_iv, put_iv, atm_iv)
                except DeribitError:
                    pass

            tenors_result.append(
                TenorIV(
                    days=int(actual_days),
                    atm_iv=_round_or_none(atm_iv, 4),
                    rr25=_round_or_none(rr25, 4),
                    fly25=_round_or_none(fly25, 4),
                    fwd=_round_or_none(spot, 2),  # Simplified: use spot as forward
                )
            )

        # Calculate confidence based on data coverage
        confidence = matched_expiries / len(tenor_days) if tenor_days else 0
        if confidence < 0.5:
            notes.append("low_confidence_sparse_data")

        return SurfaceResponse(
            ccy=currency,
            spot=round(spot, 2),
            tenors=tenors_result,
            confidence=round(confidence, 2),
            ts=current_ts,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", "surface_calc_failed"],
        ).model_dump()


def _format_expiry(ts_ms: int) -> str:
    """Format expiration timestamp to Deribit format (e.g., 28JUN24)."""
    import datetime

    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.UTC)
    return dt.strftime("%d%b%y").upper()


# =============================================================================
# Tool 7: expected_move_iv
# =============================================================================


async def expected_move_iv(
    currency: Currency,
    horizon_minutes: int = 60,
    method: Literal["dvol", "atm_iv"] = "dvol",
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Calculate expected price move based on implied volatility.

    Uses the formula: expected_move = spot × IV × √(T_years)
    where T_years = horizon_minutes / 525600

    This gives the 1σ (one standard deviation) expected move,
    meaning ~68.3% of moves should fall within this range.

    Args:
        currency: BTC or ETH
        horizon_minutes: Time horizon in minutes (default: 60)
        method: IV source - 'dvol' or 'atm_iv'

    Returns:
        ExpectedMoveResponse with calculated bands.
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            notes.append("spot_unavailable")
            return ExpectedMoveResponse(
                ccy=currency,
                spot=0,
                iv_used=0,
                iv_source=method,
                horizon_min=horizon_minutes,
                move_1s_pts=0,
                move_1s_bps=0,
                up_1s=0,
                down_1s=0,
                confidence=0,
                notes=notes[:6],
            ).model_dump()

        # Get IV based on method
        iv_used = None
        iv_source = method
        confidence = 1.0

        if method == "dvol":
            # Try DVOL first
            dvol_result = await dvol_snapshot(currency, client)
            if not dvol_result.get("error") and dvol_result.get("dvol", 0) > 0:
                # DVOL is in percentage form (e.g., 80 = 80%)
                iv_used = dvol_to_decimal(dvol_result["dvol"])
                notes.append(f"dvol_raw:{dvol_result['dvol']}")
            else:
                notes.append("dvol_unavailable_fallback_atm")
                method = "atm_iv"
                confidence = 0.7

        if method == "atm_iv" or iv_used is None:
            # Get ATM IV from nearest expiry
            iv_source = "atm_iv"

            # Get options
            instruments = await client.call_public(
                "public/get_instruments", {"currency": currency, "kind": "option", "expired": False}
            )

            if instruments:
                current_ts = _current_ts_ms()

                # Find nearest expiry
                nearest_exp = None
                min_days = float("inf")
                for opt in instruments:
                    exp = opt.get("expiration_timestamp", 0)
                    if exp <= current_ts:
                        continue
                    days = days_to_expiry_from_ts(exp, current_ts)
                    if 1 < days < min_days:
                        min_days = days
                        nearest_exp = exp

                if nearest_exp:
                    # Find ATM strike
                    atm_strike = None
                    min_dist = float("inf")
                    for opt in instruments:
                        if opt.get("expiration_timestamp") != nearest_exp:
                            continue
                        strike = _safe_float(opt.get("strike"))
                        if strike:
                            dist = abs(strike - spot)
                            if dist < min_dist:
                                min_dist = dist
                                atm_strike = strike

                    if atm_strike:
                        atm_name = f"{currency}-{_format_expiry(nearest_exp)}-{int(atm_strike)}-C"
                        try:
                            ticker = await client.call_public(
                                "public/ticker", {"instrument_name": atm_name}
                            )
                            iv = _safe_float(ticker.get("mark_iv"))
                            if iv:
                                if iv > 1:
                                    iv = iv / 100
                                iv_used = iv
                                notes.append(f"atm_from:{atm_name}")
                        except DeribitError as e:
                            notes.append(f"atm_ticker_error:{e.code}")

        # Calculate expected move
        if iv_used is None or iv_used <= 0:
            notes.append("iv_unavailable_cannot_calculate")
            return ExpectedMoveResponse(
                ccy=currency,
                spot=round(spot, 2),
                iv_used=0,
                iv_source=iv_source,
                horizon_min=horizon_minutes,
                move_1s_pts=0,
                move_1s_bps=0,
                up_1s=spot,
                down_1s=spot,
                confidence=0,
                notes=notes[:6],
            ).model_dump()

        result = calculate_expected_move(
            spot=spot,
            iv_annualized=iv_used,
            horizon_minutes=horizon_minutes,
            iv_source=iv_source,
            confidence=confidence,
        )

        return ExpectedMoveResponse(
            ccy=currency,
            spot=round(result.spot, 2),
            iv_used=round(result.iv_used, 4),
            iv_source=result.iv_source,
            horizon_min=result.horizon_minutes,
            move_1s_pts=result.move_points,
            move_1s_bps=result.move_bps,
            up_1s=result.up_1sigma,
            down_1s=result.down_1sigma,
            confidence=round(result.confidence, 2),
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", "expected_move_failed"],
        ).model_dump()


# =============================================================================
# Tool 8: funding_snapshot
# =============================================================================


async def funding_snapshot(
    currency: Currency,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get perpetual funding rate snapshot.

    Args:
        currency: BTC or ETH

    Returns:
        FundingResponse with current rate and recent history.
    """
    client = client or get_client()
    notes: list[str] = []
    perp_name = f"{currency}-PERPETUAL"

    try:
        # Get current funding rate from ticker
        ticker = await client.call_public("public/ticker", {"instrument_name": perp_name})

        current_funding = _safe_float(ticker.get("current_funding"))

        # Get funding rate history (last 5 periods)
        history: list[FundingEntry] = []
        try:
            current_ts = _current_ts_ms()
            history_result = await client.call_public(
                "public/get_funding_rate_history",
                {
                    "instrument_name": perp_name,
                    "start_timestamp": current_ts - (8 * 3600 * 1000 * 5),  # ~5 periods
                    "end_timestamp": current_ts,
                },
            )

            if history_result:
                for entry in history_result[-5:]:  # Last 5 entries
                    history.append(
                        FundingEntry(
                            ts=entry.get("timestamp", 0),
                            rate=round(entry.get("interest_8h", 0), 8),
                        )
                    )
        except DeribitError:
            notes.append("history_unavailable")

        # Calculate 8h annualized rate
        rate_8h = None
        if current_funding is not None:
            # Funding is paid every 8 hours (3x per day)
            rate_8h = current_funding * 3 * 365  # Annualized

        # Next funding timestamp
        next_ts = ticker.get("funding_8h")

        return FundingResponse(
            ccy=currency,
            perp=perp_name,
            rate=round(current_funding or 0, 8),
            rate_8h=_round_or_none(rate_8h, 4),
            next_ts=next_ts,
            history=history,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"perp:{perp_name}", "funding_fetch_failed"],
        ).model_dump()


# =============================================================================
# Tool 9: get_option_chain
# =============================================================================


async def get_option_chain(
    currency: Currency,
    expiry: str,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get option chain for a specific expiry.

    Returns strike list with mark_iv, delta, gamma, vega, open_interest, volume.
    Designed for compact output suitable for LLM consumption.

    Args:
        currency: BTC or ETH
        expiry: Expiry label (e.g., "28JUN24", "27DEC24")

    Returns:
        OptionChainResponse with strike data and summary
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            return ErrorResponse(
                code=-1,
                message="Could not fetch spot price",
                notes=["spot_unavailable"],
            ).model_dump()

        # Get instruments for this expiry
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []

        # Filter by expiry
        expiry_upper = expiry.upper()
        matching_options = []
        expiry_ts = 0

        for opt in all_options:
            inst_name = opt.get("instrument_name", "")
            # Format: BTC-28JUN24-50000-C
            parts = inst_name.split("-")
            if len(parts) >= 3 and parts[1].upper() == expiry_upper:
                matching_options.append(opt)
                if expiry_ts == 0:
                    expiry_ts = opt.get("expiration_timestamp", 0)

        if not matching_options:
            return ErrorResponse(
                code=404,
                message=f"No options found for expiry: {expiry}",
                notes=[f"currency:{currency}", f"expiry:{expiry}"],
            ).model_dump()

        current_ts = _current_ts_ms()
        days_to_expiry = days_to_expiry_from_ts(expiry_ts, current_ts)

        # Get ticker data for each option (limited to avoid rate limits)
        # Only fetch for unique strikes, prioritize ATM
        strikes_set = set()
        for opt in matching_options:
            strike = opt.get("strike")
            if strike:
                strikes_set.add(strike)

        strikes_sorted = sorted(strikes_set)

        # Find ATM strike
        atm_strike = None
        min_distance = float("inf")
        for s in strikes_sorted:
            dist = abs(s - spot)
            if dist < min_distance:
                min_distance = dist
                atm_strike = s

        # Limit strikes around ATM for compact output
        atm_idx = strikes_sorted.index(atm_strike) if atm_strike in strikes_sorted else len(strikes_sorted) // 2
        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(strikes_sorted), atm_idx + 11)
        selected_strikes = set(strikes_sorted[start_idx:end_idx])

        if len(strikes_sorted) > 21:
            notes.append(f"strikes_limited:{len(selected_strikes)}_of_{len(strikes_sorted)}")

        # Fetch tickers for selected options
        option_data: list[OptionStrikeData] = []
        total_oi = 0.0
        total_vol = 0.0
        iv_sum = 0.0
        iv_count = 0

        for opt in matching_options:
            strike = opt.get("strike")
            if strike not in selected_strikes:
                continue

            inst_name = opt.get("instrument_name", "")
            opt_type = opt.get("option_type", "call")

            try:
                ticker = await client.call_public(
                    "public/ticker", {"instrument_name": inst_name}
                )

                iv = _safe_float(ticker.get("mark_iv"))
                if iv and iv > 1:
                    iv = iv / 100  # Convert percentage to decimal

                greeks = ticker.get("greeks", {})
                oi = _safe_float(ticker.get("open_interest"))
                vol = _safe_float(ticker.get("stats", {}).get("volume"))

                option_data.append(
                    OptionStrikeData(
                        strike=strike,
                        type=opt_type,
                        mark_iv=_round_or_none(iv, 4),
                        delta=_round_or_none(_safe_float(greeks.get("delta")), 4),
                        gamma=_round_or_none(_safe_float(greeks.get("gamma")), 6),
                        vega=_round_or_none(_safe_float(greeks.get("vega")), 4),
                        oi=_round_or_none(oi, 2),
                        vol=_round_or_none(vol, 2),
                    )
                )

                if oi:
                    total_oi += oi
                if vol:
                    total_vol += vol
                if iv:
                    iv_sum += iv
                    iv_count += 1

            except DeribitError:
                notes.append(f"ticker_error:{inst_name[:20]}")
                continue

        # Sort by strike then type
        option_data.sort(key=lambda x: (x.strike, x.type))

        # Summary
        summary = {
            "total_oi": round(total_oi, 2),
            "total_volume": round(total_vol, 2),
            "avg_iv": round(iv_sum / iv_count, 4) if iv_count > 0 else None,
            "num_strikes": len(selected_strikes),
        }

        return OptionChainResponse(
            ccy=currency,
            expiry=expiry_upper,
            expiry_ts=expiry_ts,
            spot=round(spot, 2),
            atm_strike=atm_strike,
            days_to_expiry=round(days_to_expiry, 2),
            strikes=option_data[:100],
            summary=summary,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", f"expiry:{expiry}"],
        ).model_dump()


# =============================================================================
# Tool 10: get_open_interest_by_strike
# =============================================================================


async def get_open_interest_by_strike(
    currency: Currency,
    expiry: str,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get open interest aggregated by strike for a specific expiry.

    Returns OI distribution, peak areas, and top strikes.

    Args:
        currency: BTC or ETH
        expiry: Expiry label (e.g., "28JUN24")

    Returns:
        OpenInterestByStrikeResponse with OI analysis
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        # Get instruments for this expiry
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []

        # Filter by expiry
        expiry_upper = expiry.upper()
        matching_options = []

        for opt in all_options:
            inst_name = opt.get("instrument_name", "")
            parts = inst_name.split("-")
            if len(parts) >= 3 and parts[1].upper() == expiry_upper:
                matching_options.append(opt)

        if not matching_options:
            return ErrorResponse(
                code=404,
                message=f"No options found for expiry: {expiry}",
                notes=[f"currency:{currency}", f"expiry:{expiry}"],
            ).model_dump()

        # Collect OI for all options
        option_data_list: list[OptionData] = []

        for opt in matching_options:
            inst_name = opt.get("instrument_name", "")
            strike = _safe_float(opt.get("strike"))
            opt_type = opt.get("option_type", "call")

            try:
                ticker = await client.call_public(
                    "public/ticker", {"instrument_name": inst_name}
                )

                oi = _safe_float(ticker.get("open_interest"))

                if strike and oi:
                    option_data_list.append(
                        OptionData(
                            strike=strike,
                            option_type=opt_type,
                            instrument_name=inst_name,
                            open_interest=oi,
                        )
                    )
            except DeribitError:
                continue

        # Aggregate OI by strike
        oi_by_strike = aggregate_oi_by_strike(option_data_list)

        # Calculate totals
        total_call_oi = sum(s.call_oi for s in oi_by_strike)
        total_put_oi = sum(s.put_oi for s in oi_by_strike)
        pcr_total = total_put_oi / total_call_oi if total_call_oi > 0 else None

        # Find top strikes
        top_strikes_data = find_oi_peaks(oi_by_strike, top_n=5)

        # Find peak range
        peak_range_tuple = find_oi_peak_range(oi_by_strike, percentile=0.8)
        peak_range = None
        if peak_range_tuple:
            total_oi = sum(s.total_oi for s in oi_by_strike)
            peak_oi = sum(
                s.total_oi
                for s in oi_by_strike
                if peak_range_tuple[0] <= s.strike <= peak_range_tuple[1]
            )
            concentration = peak_oi / total_oi if total_oi > 0 else 0
            peak_range = OIPeakInfo(
                low=peak_range_tuple[0],
                high=peak_range_tuple[1],
                concentration=round(concentration, 3),
            )

        # Convert to response format (limit strikes for compact output)
        # Keep strikes around spot
        if spot:
            oi_by_strike.sort(key=lambda x: abs(x.strike - spot))
            oi_by_strike = oi_by_strike[:50]
            oi_by_strike.sort(key=lambda x: x.strike)

        strike_data = [
            StrikeOIData(
                strike=s.strike,
                call_oi=round(s.call_oi, 2),
                put_oi=round(s.put_oi, 2),
                total_oi=round(s.total_oi, 2),
                pcr=round(s.put_call_ratio, 3) if s.put_call_ratio else None,
            )
            for s in oi_by_strike
        ]

        top_strikes = [
            StrikeOIData(
                strike=s.strike,
                call_oi=round(s.call_oi, 2),
                put_oi=round(s.put_oi, 2),
                total_oi=round(s.total_oi, 2),
                pcr=round(s.put_call_ratio, 3) if s.put_call_ratio else None,
            )
            for s in top_strikes_data
        ]

        return OpenInterestByStrikeResponse(
            ccy=currency,
            expiry=expiry_upper,
            spot=round(spot, 2) if spot else 0,
            total_call_oi=round(total_call_oi, 2),
            total_put_oi=round(total_put_oi, 2),
            pcr_total=round(pcr_total, 3) if pcr_total else None,
            oi_by_strike=strike_data,
            top_strikes=top_strikes,
            peak_range=peak_range,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", f"expiry:{expiry}"],
        ).model_dump()


# =============================================================================
# Tool 11: compute_gamma_exposure
# =============================================================================


async def compute_gamma_exposure(
    currency: Currency,
    expiries: list[str] | None = None,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Calculate gamma exposure (GEX) profile.

    GEX measures the sensitivity of market makers' delta hedging.
    - Positive GEX: MMs are long gamma, will buy dips/sell rallies (stabilizing)
    - Negative GEX: MMs are short gamma, will sell dips/buy rallies (destabilizing)

    Args:
        currency: BTC or ETH
        expiries: List of expiry labels (e.g., ["28JUN24", "27DEC24"])
                  If None, uses nearest 3 expiries

    Returns:
        GammaExposureResponse with GEX by strike and flip level
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            return ErrorResponse(
                code=-1,
                message="Could not fetch spot price",
                notes=["spot_unavailable"],
            ).model_dump()

        # Get instruments
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []
        current_ts = _current_ts_ms()

        # Group by expiry
        by_expiry: dict[str, list] = {}
        for opt in all_options:
            inst_name = opt.get("instrument_name", "")
            parts = inst_name.split("-")
            if len(parts) >= 3:
                exp_label = parts[1].upper()
                exp_ts = opt.get("expiration_timestamp", 0)
                if exp_ts > current_ts:
                    if exp_label not in by_expiry:
                        by_expiry[exp_label] = []
                    by_expiry[exp_label].append(opt)

        # Determine which expiries to use
        if expiries:
            target_expiries = [e.upper() for e in expiries]
        else:
            # Use nearest 3 expiries
            sorted_expiries = sorted(
                by_expiry.keys(),
                key=lambda x: by_expiry[x][0].get("expiration_timestamp", 0),
            )
            target_expiries = sorted_expiries[:3]

        notes.append(f"expiries:{len(target_expiries)}")

        # Collect option data with gamma and OI
        option_data_list: list[OptionData] = []
        expiries_included = []

        for exp_label in target_expiries:
            if exp_label not in by_expiry:
                notes.append(f"missing_expiry:{exp_label}")
                continue

            expiries_included.append(exp_label)

            for opt in by_expiry[exp_label]:
                inst_name = opt.get("instrument_name", "")
                strike = _safe_float(opt.get("strike"))
                opt_type = opt.get("option_type", "call")

                try:
                    ticker = await client.call_public(
                        "public/ticker", {"instrument_name": inst_name}
                    )

                    greeks = ticker.get("greeks", {})
                    gamma = _safe_float(greeks.get("gamma"))
                    oi = _safe_float(ticker.get("open_interest"))

                    if strike and gamma is not None and oi:
                        option_data_list.append(
                            OptionData(
                                strike=strike,
                                option_type=opt_type,
                                instrument_name=inst_name,
                                gamma=gamma,
                                open_interest=oi,
                            )
                        )
                except DeribitError:
                    continue

        if not option_data_list:
            return ErrorResponse(
                code=-1,
                message="No valid gamma/OI data found",
                notes=[f"currency:{currency}"],
            ).model_dump()

        # Calculate GEX profile
        gex_profile = calculate_gamma_exposure_profile(option_data_list, spot)

        # Scale GEX to millions for readability
        scale = 1_000_000

        # Get top positive and negative GEX strikes
        sorted_positive = sorted(
            [g for g in gex_profile.gex_by_strike if g.net_gex > 0],
            key=lambda x: x.net_gex,
            reverse=True,
        )[:3]

        sorted_negative = sorted(
            [g for g in gex_profile.gex_by_strike if g.net_gex < 0],
            key=lambda x: x.net_gex,
        )[:3]

        # Filter strikes around spot for compact output
        gex_by_strike = gex_profile.gex_by_strike
        gex_by_strike.sort(key=lambda x: abs(x.strike - spot))
        gex_by_strike = gex_by_strike[:50]
        gex_by_strike.sort(key=lambda x: x.strike)

        # Convert to response format
        gex_data = [
            StrikeGEX(
                strike=g.strike,
                call_gex=round(g.call_gex / scale, 3),
                put_gex=round(g.put_gex / scale, 3),
                net_gex=round(g.net_gex / scale, 3),
            )
            for g in gex_by_strike
        ]

        top_positive = [
            StrikeGEX(
                strike=g.strike,
                call_gex=round(g.call_gex / scale, 3),
                put_gex=round(g.put_gex / scale, 3),
                net_gex=round(g.net_gex / scale, 3),
            )
            for g in sorted_positive
        ]

        top_negative = [
            StrikeGEX(
                strike=g.strike,
                call_gex=round(g.call_gex / scale, 3),
                put_gex=round(g.put_gex / scale, 3),
                net_gex=round(g.net_gex / scale, 3),
            )
            for g in sorted_negative
        ]

        # Determine MM positioning
        net_gex_scaled = gex_profile.net_gex / scale
        if net_gex_scaled > 0.5:
            mm_positioning = "long_gamma"
        elif net_gex_scaled < -0.5:
            mm_positioning = "short_gamma"
        else:
            mm_positioning = "neutral"

        return GammaExposureResponse(
            ccy=currency,
            spot=round(spot, 2),
            expiries_included=expiries_included,
            net_gex=round(net_gex_scaled, 3),
            gamma_flip=round(gex_profile.gamma_flip_level, 2) if gex_profile.gamma_flip_level else None,
            max_pos_gex_strike=gex_profile.max_positive_gex_strike,
            max_neg_gex_strike=gex_profile.max_negative_gex_strike,
            gex_by_strike=gex_data,
            top_positive=top_positive,
            top_negative=top_negative,
            market_maker_positioning=mm_positioning,
            notes=notes[:6] + gex_profile.calculation_notes,
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}"],
        ).model_dump()


# =============================================================================
# Tool 12: compute_max_pain
# =============================================================================


async def compute_max_pain(
    currency: Currency,
    expiry: str,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Calculate max pain strike for a specific expiry.

    Max pain is the strike price where option buyers (long calls + long puts)
    would experience maximum loss, i.e., where most options expire worthless.

    Theory: Price tends to gravitate toward max pain at expiry.

    Args:
        currency: BTC or ETH
        expiry: Expiry label (e.g., "28JUN24")

    Returns:
        MaxPainResponse with max pain strike and pain curve
    """
    client = client or get_client()
    notes: list[str] = []

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            return ErrorResponse(
                code=-1,
                message="Could not fetch spot price",
                notes=["spot_unavailable"],
            ).model_dump()

        # Get instruments for this expiry
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []

        # Filter by expiry
        expiry_upper = expiry.upper()
        matching_options = []
        expiry_ts = 0

        for opt in all_options:
            inst_name = opt.get("instrument_name", "")
            parts = inst_name.split("-")
            if len(parts) >= 3 and parts[1].upper() == expiry_upper:
                matching_options.append(opt)
                if expiry_ts == 0:
                    expiry_ts = opt.get("expiration_timestamp", 0)

        if not matching_options:
            return ErrorResponse(
                code=404,
                message=f"No options found for expiry: {expiry}",
                notes=[f"currency:{currency}", f"expiry:{expiry}"],
            ).model_dump()

        # Collect OI for all options
        option_data_list: list[OptionData] = []

        for opt in matching_options:
            inst_name = opt.get("instrument_name", "")
            strike = _safe_float(opt.get("strike"))
            opt_type = opt.get("option_type", "call")

            try:
                ticker = await client.call_public(
                    "public/ticker", {"instrument_name": inst_name}
                )

                oi = _safe_float(ticker.get("open_interest"))

                if strike and oi:
                    option_data_list.append(
                        OptionData(
                            strike=strike,
                            option_type=opt_type,
                            instrument_name=inst_name,
                            open_interest=oi,
                        )
                    )
            except DeribitError:
                continue

        if not option_data_list:
            return ErrorResponse(
                code=-1,
                message="No valid OI data found",
                notes=[f"currency:{currency}", f"expiry:{expiry}"],
            ).model_dump()

        # Calculate max pain
        max_pain_result = calculate_max_pain(option_data_list, spot)

        # Calculate distance from spot
        distance_pct = ((max_pain_result.max_pain_strike - spot) / spot) * 100

        # Convert pain curve to response format
        pain_curve_top3 = [
            PainCurvePoint(strike=p[0], pain=round(p[1], 2))
            for p in max_pain_result.pain_curve_top3
        ]

        # PCR
        pcr = (
            max_pain_result.total_put_oi / max_pain_result.total_call_oi
            if max_pain_result.total_call_oi > 0
            else None
        )

        return MaxPainResponse(
            ccy=currency,
            expiry=expiry_upper,
            expiry_ts=expiry_ts,
            spot=round(spot, 2),
            max_pain_strike=max_pain_result.max_pain_strike,
            distance_from_spot_pct=round(distance_pct, 2),
            pain_curve_top3=pain_curve_top3,
            total_call_oi=round(max_pain_result.total_call_oi, 2),
            total_put_oi=round(max_pain_result.total_put_oi, 2),
            pcr=round(pcr, 3) if pcr else None,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", f"expiry:{expiry}"],
        ).model_dump()


# =============================================================================
# Tool 13: get_iv_term_structure
# =============================================================================


async def get_iv_term_structure(
    currency: Currency,
    tenors_days: list[int] | None = None,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get ATM IV term structure across tenors.

    Returns IV at each tenor, term structure slope, and shape analysis.

    Args:
        currency: BTC or ETH
        tenors_days: Target tenors in days (default: [7, 14, 30, 60, 90])

    Returns:
        IVTermStructureResponse with term structure data
    """
    client = client or get_client()
    notes: list[str] = []
    tenors_days = tenors_days or [7, 14, 30, 60, 90]

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            return ErrorResponse(
                code=-1,
                message="Could not fetch spot price",
                notes=["spot_unavailable"],
            ).model_dump()

        # Get DVOL if available
        dvol_current = None
        try:
            dvol_result = await dvol_snapshot(currency, client)
            if not dvol_result.get("error") and dvol_result.get("dvol", 0) > 0:
                dvol_current = dvol_result.get("dvol")
        except Exception:
            pass

        # Get instruments
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []
        current_ts = _current_ts_ms()

        # Group by expiration
        by_expiry: dict[int, list] = {}
        for opt in all_options:
            exp = opt.get("expiration_timestamp", 0)
            if exp <= current_ts:
                continue
            if exp not in by_expiry:
                by_expiry[exp] = []
            by_expiry[exp].append(opt)

        # For each target tenor, find closest expiry and ATM IV
        term_structure_points: list[TSPoint] = []

        for target_days in tenors_days[:6]:  # Limit to 6 tenors
            # Find closest expiration
            best_exp = None
            best_distance = float("inf")

            for exp_ts in by_expiry:
                days = days_to_expiry_from_ts(exp_ts, current_ts)
                distance = abs(days - target_days)
                if distance < best_distance and distance < target_days * 0.5:
                    best_distance = distance
                    best_exp = exp_ts

            if best_exp is None:
                continue

            actual_days = days_to_expiry_from_ts(best_exp, current_ts)
            expiry_options = by_expiry[best_exp]

            # Find ATM strike
            atm_strike = None
            min_dist = float("inf")
            for opt in expiry_options:
                strike = _safe_float(opt.get("strike"))
                if strike:
                    dist = abs(strike - spot)
                    if dist < min_dist:
                        min_dist = dist
                        atm_strike = strike

            if not atm_strike:
                continue

            # Get ATM IV
            atm_iv = None
            atm_call_name = f"{currency}-{_format_expiry(best_exp)}-{int(atm_strike)}-C"
            try:
                atm_ticker = await client.call_public(
                    "public/ticker", {"instrument_name": atm_call_name}
                )
                iv = _safe_float(atm_ticker.get("mark_iv"))
                if iv and iv > 1:
                    iv = iv / 100
                atm_iv = iv
            except DeribitError:
                notes.append(f"atm_ticker_failed:{target_days}d")
                continue

            term_structure_points.append(
                TSPoint(
                    days=int(actual_days),
                    atm_iv=atm_iv,
                    expiry_label=_format_expiry(best_exp),
                    expiry_ts=best_exp,
                )
            )

        # Calculate slopes
        slope_short = calculate_iv_term_structure_slope(term_structure_points, 7, 30)
        slope_long = calculate_iv_term_structure_slope(term_structure_points, 30, 90)

        # Determine shape
        is_contango = analyze_term_structure_shape(term_structure_points)
        if is_contango:
            shape = "contango"
        else:
            # Check if relatively flat
            if len(term_structure_points) >= 2:
                ivs = [p.atm_iv for p in term_structure_points if p.atm_iv]
                if ivs and max(ivs) - min(ivs) < 0.02:
                    shape = "flat"
                else:
                    shape = "backwardation"
            else:
                shape = "flat"

        # Convert to response format
        ts_data = [
            TermStructurePoint(
                days=p.days,
                expiry=p.expiry_label,
                atm_iv=round(p.atm_iv, 4) if p.atm_iv else None,
                atm_iv_pct=round(p.atm_iv * 100, 2) if p.atm_iv else None,
            )
            for p in term_structure_points
        ]

        # Sort by days
        ts_data.sort(key=lambda x: x.days)

        return IVTermStructureResponse(
            ccy=currency,
            spot=round(spot, 2),
            term_structure=ts_data,
            slope_7d_30d=round(slope_short * 100, 4) if slope_short else None,  # Convert to %
            slope_30d_90d=round(slope_long * 100, 4) if slope_long else None,
            shape=shape,
            dvol_current=dvol_current,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}"],
        ).model_dump()


# =============================================================================
# Tool 14: get_skew_metrics
# =============================================================================


async def get_skew_metrics(
    currency: Currency,
    tenors_days: list[int] | None = None,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get volatility skew metrics (RR25d, BF25d) across tenors.

    Risk Reversal (RR25d): Call_IV(25d) - Put_IV(25d)
        - Positive: Bullish skew (upside demand)
        - Negative: Bearish skew (downside protection)

    Butterfly (BF25d): (Call_IV + Put_IV) / 2 - ATM_IV
        - Positive: Fat tails pricing (wing demand)

    Args:
        currency: BTC or ETH
        tenors_days: Target tenors in days (default: [7, 30])

    Returns:
        SkewMetricsResponse with skew by tenor and trend
    """
    client = client or get_client()
    notes: list[str] = []
    tenors_days = tenors_days or [7, 30]

    try:
        # Get spot price
        index_result = await client.call_public(
            "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}
        )
        spot = _safe_float(index_result.get("index_price", 0))

        if not spot or spot <= 0:
            return ErrorResponse(
                code=-1,
                message="Could not fetch spot price",
                notes=["spot_unavailable"],
            ).model_dump()

        # Get instruments
        instruments_result = await client.call_public(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        )

        all_options = instruments_result if isinstance(instruments_result, list) else []
        current_ts = _current_ts_ms()

        # Group by expiration
        by_expiry: dict[int, list] = {}
        for opt in all_options:
            exp = opt.get("expiration_timestamp", 0)
            if exp <= current_ts:
                continue
            if exp not in by_expiry:
                by_expiry[exp] = []
            by_expiry[exp].append(opt)

        # For each target tenor, calculate skew metrics
        skew_metrics_list: list[SkewMetrics] = []
        skew_by_tenor: list[TenorSkew] = []

        for target_days in tenors_days[:6]:  # Limit to 6 tenors
            # Find closest expiration
            best_exp = None
            best_distance = float("inf")

            for exp_ts in by_expiry:
                days = days_to_expiry_from_ts(exp_ts, current_ts)
                distance = abs(days - target_days)
                if distance < best_distance and distance < target_days * 0.5:
                    best_distance = distance
                    best_exp = exp_ts

            if best_exp is None:
                continue

            actual_days = days_to_expiry_from_ts(best_exp, current_ts)
            expiry_label = _format_expiry(best_exp)
            expiry_options = by_expiry[best_exp]

            # Find ATM strike and IV
            atm_strike = None
            min_dist = float("inf")
            for opt in expiry_options:
                strike = _safe_float(opt.get("strike"))
                if strike:
                    dist = abs(strike - spot)
                    if dist < min_dist:
                        min_dist = dist
                        atm_strike = strike

            if not atm_strike:
                continue

            atm_iv = None
            atm_call_name = f"{currency}-{expiry_label}-{int(atm_strike)}-C"
            try:
                atm_ticker = await client.call_public(
                    "public/ticker", {"instrument_name": atm_call_name}
                )
                iv = _safe_float(atm_ticker.get("mark_iv"))
                if iv and iv > 1:
                    iv = iv / 100
                atm_iv = iv
            except DeribitError:
                continue

            if not atm_iv:
                continue

            # Estimate 25d strikes
            call_25d_strike = estimate_25d_strike(spot, atm_iv, actual_days, is_call=True)
            put_25d_strike = estimate_25d_strike(spot, atm_iv, actual_days, is_call=False)

            # Round to nearest available strike
            available_strikes = sorted(
                {opt.get("strike") for opt in expiry_options if opt.get("strike")}
            )

            def find_nearest_strike(target: float, strikes: list[float]) -> float | None:
                if not strikes:
                    return None
                return min(strikes, key=lambda x: abs(x - target))

            call_25d_strike = find_nearest_strike(call_25d_strike, available_strikes)
            put_25d_strike = find_nearest_strike(put_25d_strike, available_strikes)

            # Get 25d IVs
            call_25d_iv = None
            put_25d_iv = None

            if call_25d_strike:
                call_25d_name = f"{currency}-{expiry_label}-{int(call_25d_strike)}-C"
                try:
                    ticker = await client.call_public(
                        "public/ticker", {"instrument_name": call_25d_name}
                    )
                    iv = _safe_float(ticker.get("mark_iv"))
                    if iv and iv > 1:
                        iv = iv / 100
                    call_25d_iv = iv
                except DeribitError:
                    pass

            if put_25d_strike:
                put_25d_name = f"{currency}-{expiry_label}-{int(put_25d_strike)}-P"
                try:
                    ticker = await client.call_public(
                        "public/ticker", {"instrument_name": put_25d_name}
                    )
                    iv = _safe_float(ticker.get("mark_iv"))
                    if iv and iv > 1:
                        iv = iv / 100
                    put_25d_iv = iv
                except DeribitError:
                    pass

            # Calculate RR and BF
            rr25d = calculate_risk_reversal(call_25d_iv, put_25d_iv)
            bf25d = calculate_butterfly(call_25d_iv, put_25d_iv, atm_iv)
            skew_dir = determine_skew_direction(rr25d)

            skew_metrics_list.append(
                SkewMetrics(
                    days=int(actual_days),
                    rr25d=rr25d,
                    bf25d=bf25d,
                    atm_iv=atm_iv,
                    call_25d_iv=call_25d_iv,
                    put_25d_iv=put_25d_iv,
                    skew_direction=skew_dir,
                )
            )

            skew_by_tenor.append(
                TenorSkew(
                    days=int(actual_days),
                    expiry=expiry_label,
                    atm_iv=round(atm_iv, 4) if atm_iv else None,
                    rr25d=round(rr25d, 4) if rr25d else None,
                    rr25d_pct=round(rr25d * 100, 2) if rr25d else None,
                    bf25d=round(bf25d, 4) if bf25d else None,
                    bf25d_pct=round(bf25d * 100, 2) if bf25d else None,
                    skew_dir=skew_dir,
                )
            )

        # Determine skew trend
        skew_trend = determine_skew_trend(skew_metrics_list)

        # Sort by days
        skew_by_tenor.sort(key=lambda x: x.days)

        # Calculate summary
        valid_rr = [m.rr25d for m in skew_metrics_list if m.rr25d is not None]
        valid_bf = [m.bf25d for m in skew_metrics_list if m.bf25d is not None]

        avg_rr = sum(valid_rr) / len(valid_rr) if valid_rr else None
        avg_bf = sum(valid_bf) / len(valid_bf) if valid_bf else None

        # Determine dominant direction
        directions = [m.skew_direction for m in skew_metrics_list if m.skew_direction]
        if directions:
            bullish_count = directions.count("bullish")
            bearish_count = directions.count("bearish")
            if bullish_count > bearish_count:
                dominant = "bullish"
            elif bearish_count > bullish_count:
                dominant = "bearish"
            else:
                dominant = "neutral"
        else:
            dominant = None

        summary = {
            "avg_rr25d_pct": round(avg_rr * 100, 2) if avg_rr else None,
            "avg_bf25d_pct": round(avg_bf * 100, 2) if avg_bf else None,
            "dominant_direction": dominant,
            "tenors_analyzed": len(skew_by_tenor),
        }

        return SkewMetricsResponse(
            ccy=currency,
            spot=round(spot, 2),
            skew_by_tenor=skew_by_tenor,
            skew_trend=skew_trend,
            summary=summary,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}"],
        ).model_dump()


# =============================================================================
# Private Tools (only enabled when DERIBIT_ENABLE_PRIVATE=true)
# =============================================================================


async def account_summary(
    currency: Currency,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get account summary (requires authentication).

    Args:
        currency: BTC or ETH

    Returns:
        AccountSummaryResponse with equity and margin info.
    """
    settings = get_settings()
    if not settings.enable_private:
        return ErrorResponse(
            code=403,
            message="Private API disabled. Set DERIBIT_ENABLE_PRIVATE=true",
            notes=["private_api_disabled"],
        ).model_dump()

    client = client or get_client()

    try:
        result = await client.call_private(
            "private/get_account_summary", {"currency": currency, "extended": True}
        )

        return AccountSummaryResponse(
            ccy=currency,
            equity=round(_safe_float(result.get("equity"), 0), 8),
            avail=round(_safe_float(result.get("available_funds"), 0), 8),
            margin=round(_safe_float(result.get("margin_balance"), 0), 8),
            mm=_round_or_none(_safe_float(result.get("maintenance_margin")), 8),
            im=_round_or_none(_safe_float(result.get("initial_margin")), 8),
            delta_total=_round_or_none(_safe_float(result.get("delta_total")), 4),
            notes=[],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", "auth_required"],
        ).model_dump()


async def positions(
    currency: Currency,
    kind: InstrumentKind = "future",
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get open positions (requires authentication).

    Args:
        currency: BTC or ETH
        kind: future or option

    Returns:
        PositionsResponse with compact position list (max 20).
    """
    settings = get_settings()
    if not settings.enable_private:
        return ErrorResponse(
            code=403,
            message="Private API disabled. Set DERIBIT_ENABLE_PRIVATE=true",
            notes=["private_api_disabled"],
        ).model_dump()

    client = client or get_client()
    notes: list[str] = []

    try:
        result = await client.call_private(
            "private/get_positions", {"currency": currency, "kind": kind}
        )

        positions_list = result if isinstance(result, list) else []
        total = len(positions_list)

        if total > 20:
            notes.append(f"truncated_from:{total}")
            positions_list = positions_list[:20]

        compact_positions = []
        for pos in positions_list:
            size = _safe_float(pos.get("size"), 0)
            if size == 0:
                continue

            compact_positions.append(
                PositionCompact(
                    inst=pos.get("instrument_name", ""),
                    size=abs(size),
                    side="long" if size > 0 else "short",
                    entry=round(_safe_float(pos.get("average_price"), 0), 4),
                    mark=round(_safe_float(pos.get("mark_price"), 0), 4),
                    pnl=round(_safe_float(pos.get("floating_profit_loss"), 0), 4),
                    liq=_round_or_none(_safe_float(pos.get("estimated_liquidation_price")), 2),
                )
            )

        return PositionsResponse(
            ccy=currency,
            count=total,
            positions=compact_positions,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"currency:{currency}", f"kind:{kind}"],
        ).model_dump()


async def open_orders(
    currency: Currency | None = None,
    instrument_name: str | None = None,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Get open orders (requires authentication).

    Args:
        currency: BTC or ETH (required if instrument_name not provided)
        instrument_name: Specific instrument (optional)

    Returns:
        OpenOrdersResponse with compact order list (max 20).
    """
    settings = get_settings()
    if not settings.enable_private:
        return ErrorResponse(
            code=403,
            message="Private API disabled. Set DERIBIT_ENABLE_PRIVATE=true",
            notes=["private_api_disabled"],
        ).model_dump()

    client = client or get_client()
    notes: list[str] = []

    try:
        if instrument_name:
            result = await client.call_private(
                "private/get_open_orders_by_instrument", {"instrument_name": instrument_name}
            )
        elif currency:
            result = await client.call_private(
                "private/get_open_orders_by_currency", {"currency": currency}
            )
        else:
            return ErrorResponse(
                code=400,
                message="Either currency or instrument_name required",
                notes=[],
            ).model_dump()

        orders_list = result if isinstance(result, list) else []
        total = len(orders_list)

        if total > 20:
            notes.append(f"truncated_from:{total}")
            orders_list = orders_list[:20]

        compact_orders = []
        for order in orders_list:
            compact_orders.append(
                OrderCompact(
                    id=order.get("order_id", ""),
                    inst=order.get("instrument_name", ""),
                    side=order.get("direction", "buy"),
                    type=order.get("order_type", "limit"),
                    price=_round_or_none(_safe_float(order.get("price")), 4),
                    amount=round(_safe_float(order.get("amount"), 0), 4),
                    filled=round(_safe_float(order.get("filled_amount"), 0), 4),
                    state=order.get("order_state", "unknown"),
                )
            )

        return OpenOrdersResponse(
            count=total,
            orders=compact_orders,
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=["auth_required"],
        ).model_dump()


async def place_order(
    request: PlaceOrderRequest,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Place an order (requires authentication).

    SAFETY: By default, this runs in DRY_RUN mode and only shows
    what would be sent. Set DERIBIT_DRY_RUN=false to enable live trading.

    Args:
        request: Order parameters

    Returns:
        PlaceOrderResponse with order ID or simulation result.
    """
    settings = get_settings()
    if not settings.enable_private:
        return ErrorResponse(
            code=403,
            message="Private API disabled. Set DERIBIT_ENABLE_PRIVATE=true",
            notes=["private_api_disabled"],
        ).model_dump()

    client = client or get_client()
    notes: list[str] = []

    # Build the request params
    params = {
        "instrument_name": request.instrument,
        "amount": request.amount,
        "type": request.type,
    }

    if request.type == "limit" and request.price:
        params["price"] = request.price

    if request.post_only:
        params["post_only"] = True

    if request.reduce_only:
        params["reduce_only"] = True

    # DRY RUN MODE
    if settings.dry_run:
        notes.append("DRY_RUN_MODE")
        notes.append("Set DERIBIT_DRY_RUN=false for live trading")

        return PlaceOrderResponse(
            dry_run=True,
            would_send={
                "method": f"private/{request.side}",
                "params": params,
            },
            order_id=None,
            status="simulated",
            notes=notes[:6],
        ).model_dump()

    # LIVE TRADING
    try:
        method = f"private/{request.side}"
        result = await client.call_private(method, params)

        order_data = result.get("order", {})

        return PlaceOrderResponse(
            dry_run=False,
            would_send=None,
            order_id=order_data.get("order_id"),
            status=order_data.get("order_state", "submitted"),
            notes=notes[:6],
        ).model_dump()

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"instrument:{request.instrument}", "order_failed"],
        ).model_dump()


async def cancel_order(
    order_id: str,
    client: DeribitJsonRpcClient | None = None,
) -> dict:
    """
    Cancel an order (requires authentication).

    SAFETY: Respects DRY_RUN mode.

    Args:
        order_id: Order ID to cancel

    Returns:
        Status of cancellation.
    """
    settings = get_settings()
    if not settings.enable_private:
        return ErrorResponse(
            code=403,
            message="Private API disabled. Set DERIBIT_ENABLE_PRIVATE=true",
            notes=["private_api_disabled"],
        ).model_dump()

    client = client or get_client()
    notes: list[str] = []

    if settings.dry_run:
        notes.append("DRY_RUN_MODE")
        return {
            "dry_run": True,
            "would_cancel": order_id,
            "status": "simulated",
            "notes": notes,
        }

    try:
        result = await client.call_private("private/cancel", {"order_id": order_id})

        return {
            "dry_run": False,
            "order_id": order_id,
            "status": result.get("order_state", "cancelled"),
            "notes": notes[:6],
        }

    except DeribitError as e:
        return ErrorResponse(
            code=e.code,
            message=e.message[:100],
            notes=[f"order_id:{order_id}", "cancel_failed"],
        ).model_dump()
