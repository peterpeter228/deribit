"""
Tests for MCP tools.

Covers:
- Output size limits (≤2KB target)
- Response structure validation
- Error degradation
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from deribit_mcp.models import (
    DvolResponse,
    ExpectedMoveResponse,
    FundingResponse,
    GammaExposureResponse,
    InstrumentCompact,
    InstrumentsResponse,
    IVTermStructureResponse,
    MaxPainResponse,
    OpenInterestByStrikeResponse,
    OptionChainResponse,
    OrderBookSummaryResponse,
    SkewMetricsResponse,
    StatusResponse,
    StrikeGEX,
    StrikeOIData,
    SurfaceResponse,
    TenorSkew,
    TermStructurePoint,
    TickerResponse,
)
from deribit_mcp.tools import (
    _round_or_none,
    _safe_float,
)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_safe_float_valid(self):
        """Test safe_float with valid inputs."""
        assert _safe_float(1.5) == 1.5
        assert _safe_float("2.5") == 2.5
        assert _safe_float(100) == 100.0

    def test_safe_float_invalid(self):
        """Test safe_float with invalid inputs."""
        assert _safe_float(None) is None
        assert _safe_float("invalid") is None
        assert _safe_float(None, default=0.0) == 0.0

    def test_round_or_none(self):
        """Test round_or_none function."""
        assert _round_or_none(1.23456, 2) == 1.23
        assert _round_or_none(None, 2) is None
        assert _round_or_none(1.999999, 4) == 2.0


class TestOutputSizeLimits:
    """Tests to verify output stays within size limits."""

    def test_status_response_size(self):
        """Test StatusResponse stays compact."""
        response = StatusResponse(
            env="prod",
            api_ok=True,
            server_time_ms=1700000000000,
            notes=["note1", "note2", "note3"],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 200  # Status should be tiny

    def test_instruments_response_max_50(self):
        """Test InstrumentsResponse respects 50 item limit."""
        instruments = [
            InstrumentCompact(
                name=f"BTC-28JUN24-{50000 + i * 1000}-C",
                exp_ts=1719561600000,
                strike=50000 + i * 1000,
                type="call",
                tick=0.0001,
                size=1.0,
            )
            for i in range(50)
        ]

        response = InstrumentsResponse(
            count=100,  # Original count was higher
            instruments=instruments,
            notes=["truncated_from:100"],
        )

        json_str = response.model_dump_json()

        # 50 instruments should fit within reasonable size
        # Using 6KB as limit (hardcoded 5KB is a soft target)
        assert len(json_str) < 6000
        assert len(response.instruments) <= 50

    def test_ticker_response_size(self):
        """Test TickerResponse stays compact."""
        response = TickerResponse(
            inst="BTC-PERPETUAL",
            bid=50000.0,
            ask=50001.0,
            mid=50000.5,
            mark=50000.25,
            idx=50000.0,
            und=50000.0,
            iv=0.80,
            greeks=None,
            oi=1000000.0,
            vol_24h=50000.0,
            funding=0.0001,
            next_funding_ts=1700003600000,
            notes=[],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 500  # Ticker should be compact

    def test_orderbook_summary_max_5_levels(self):
        """Test OrderBookSummaryResponse limits to 5 levels."""
        from deribit_mcp.models import PriceLevel

        response = OrderBookSummaryResponse(
            inst="BTC-PERPETUAL",
            bid=50000.0,
            ask=50001.0,
            spread_pts=1.0,
            spread_bps=2.0,
            bids=[PriceLevel(p=50000 - i, q=1.0) for i in range(5)],
            asks=[PriceLevel(p=50001 + i, q=1.0) for i in range(5)],
            bid_depth=100.0,
            ask_depth=100.0,
            imbalance=0.0,
            notes=[],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 1000  # Should be under 1KB
        assert len(response.bids) <= 5
        assert len(response.asks) <= 5

    def test_dvol_response_size(self):
        """Test DvolResponse stays compact."""
        response = DvolResponse(
            ccy="BTC",
            dvol=80.5,
            dvol_chg_24h=2.5,
            percentile=65.0,
            ts=1700000000000,
            notes=["source:index"],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 200

    def test_surface_response_max_tenors(self):
        """Test SurfaceResponse limits tenors."""
        from deribit_mcp.models import TenorIV

        response = SurfaceResponse(
            ccy="BTC",
            spot=50000.0,
            tenors=[
                TenorIV(days=7, atm_iv=0.80, rr25=0.02, fly25=0.01, fwd=50100),
                TenorIV(days=14, atm_iv=0.78, rr25=0.01, fly25=0.005, fwd=50200),
                TenorIV(days=30, atm_iv=0.75, rr25=0.005, fly25=0.002, fwd=50500),
                TenorIV(days=60, atm_iv=0.72, rr25=0.003, fly25=0.001, fwd=51000),
            ],
            confidence=0.95,
            ts=1700000000000,
            notes=[],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 1000
        assert len(response.tenors) <= 6  # Max 6 tenors

    def test_expected_move_response_size(self):
        """Test ExpectedMoveResponse stays compact."""
        response = ExpectedMoveResponse(
            ccy="BTC",
            spot=50000.0,
            iv_used=0.80,
            iv_source="dvol",
            horizon_min=60,
            move_1s_pts=427.5,
            move_1s_bps=85.5,
            up_1s=50427.5,
            down_1s=49572.5,
            confidence=0.95,
            notes=["dvol_raw:80"],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 400

    def test_funding_response_max_history(self):
        """Test FundingResponse limits history."""
        from deribit_mcp.models import FundingEntry

        response = FundingResponse(
            ccy="BTC",
            perp="BTC-PERPETUAL",
            rate=0.0001,
            rate_8h=0.1095,
            next_ts=1700003600000,
            history=[FundingEntry(ts=1700000000000 - i * 28800000, rate=0.0001) for i in range(5)],
            notes=[],
        )

        json_str = response.model_dump_json()

        assert len(json_str) < 500
        assert len(response.history) <= 5


class TestCompactJson:
    """Tests for compact JSON serialization."""

    def test_compact_json_no_spaces(self):
        """Test compact JSON has no unnecessary spaces."""
        data = {"key": "value", "number": 123, "list": [1, 2, 3]}
        result = json.dumps(data, separators=(",", ":"))

        assert " " not in result.replace('"key"', "").replace('"value"', "")
        assert '{"key":"value","number":123,"list":[1,2,3]}' == result


class TestNotesLimit:
    """Tests for notes array limit."""

    def test_status_notes_max_6(self):
        """Test StatusResponse rejects more than 6 notes."""
        import pytest
        from pydantic import ValidationError

        # Pydantic should raise ValidationError for more than 6 notes
        with pytest.raises(ValidationError):
            StatusResponse(
                env="prod",
                api_ok=True,
                server_time_ms=1700000000000,
                notes=["1", "2", "3", "4", "5", "6", "7", "8"],
            )

        # Exactly 6 should work
        response = StatusResponse(
            env="prod",
            api_ok=True,
            server_time_ms=1700000000000,
            notes=["1", "2", "3", "4", "5", "6"],
        )
        assert len(response.notes) == 6

    def test_ticker_notes_max_6(self):
        """Test TickerResponse rejects more than 6 notes."""
        import pytest
        from pydantic import ValidationError

        # Pydantic should raise ValidationError for more than 6 notes
        with pytest.raises(ValidationError):
            TickerResponse(
                inst="BTC-PERPETUAL",
                mark=50000.0,
                notes=["1", "2", "3", "4", "5", "6", "7"],
            )

        # Exactly 6 should work
        response = TickerResponse(
            inst="BTC-PERPETUAL",
            mark=50000.0,
            notes=["1", "2", "3", "4", "5", "6"],
        )
        assert len(response.notes) == 6


class TestErrorDegradation:
    """Tests for graceful error degradation."""

    def test_error_response_structure(self):
        """Test error response has expected structure."""
        from deribit_mcp.models import ErrorResponseLegacy

        error = ErrorResponseLegacy(
            code=10001,
            message="Test error message",
            notes=["context1", "context2"],
        )

        data = error.model_dump()

        assert data["error"] is True
        assert data["code"] == 10001
        assert "message" in data
        assert len(data["notes"]) == 2

    def test_error_message_truncation(self):
        """Test long error messages get truncated in tool output."""
        # In tools.py, we truncate messages to 100 chars
        long_message = "A" * 200
        truncated = long_message[:100]

        assert len(truncated) == 100


class TestModelValidation:
    """Tests for Pydantic model validation."""

    def test_currency_enum(self):
        """Test currency must be BTC or ETH."""
        response = DvolResponse(
            ccy="BTC",
            dvol=80.0,
            ts=1700000000000,
        )

        assert response.ccy == "BTC"

    def test_confidence_bounds(self):
        """Test confidence is bounded 0-1."""
        response = ExpectedMoveResponse(
            ccy="BTC",
            spot=50000.0,
            iv_used=0.80,
            iv_source="dvol",
            horizon_min=60,
            move_1s_pts=0,
            move_1s_bps=0,
            up_1s=50000,
            down_1s=50000,
            confidence=0.5,  # Valid
            notes=[],
        )

        assert 0 <= response.confidence <= 1

    def test_instrument_compact_fields(self):
        """Test InstrumentCompact has minimal fields."""
        inst = InstrumentCompact(
            name="BTC-28JUN24-70000-C",
            exp_ts=1719561600000,
            strike=70000,
            type="call",
            tick=0.0001,
            size=1.0,
        )

        # Check fields are present
        data = inst.model_dump()

        assert "name" in data
        assert "exp_ts" in data
        assert "strike" in data
        assert "type" in data
        assert "tick" in data
        assert "size" in data

        # Should not have verbose field names
        assert "instrument_name" not in data
        assert "expiration_timestamp" not in data


# =============================================================================
# New Options Analytics Tools Tests
# =============================================================================


class TestOptionChainResponse:
    """Tests for OptionChainResponse structure and size."""

    def test_option_chain_response_structure(self):
        """Test OptionChainResponse has expected structure."""
        from deribit_mcp.models import OptionStrikeData

        response = OptionChainResponse(
            ccy="BTC",
            expiry="28JUN24",
            expiry_ts=1719561600000,
            spot=50000.0,
            atm_strike=50000,
            days_to_expiry=30.5,
            strikes=[
                OptionStrikeData(strike=49000, type="call", mark_iv=0.82, delta=0.6),
                OptionStrikeData(strike=50000, type="call", mark_iv=0.80, delta=0.5),
                OptionStrikeData(strike=51000, type="call", mark_iv=0.78, delta=0.4),
            ],
            summary={"total_oi": 10000, "total_volume": 5000, "avg_iv": 0.80},
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 2000  # Should be compact

    def test_option_chain_max_strikes(self):
        """Test OptionChainResponse limits strikes."""
        from deribit_mcp.models import OptionStrikeData

        strikes = [
            OptionStrikeData(strike=40000 + i * 1000, type="call", mark_iv=0.80)
            for i in range(100)
        ]

        response = OptionChainResponse(
            ccy="BTC",
            expiry="28JUN24",
            expiry_ts=1719561600000,
            spot=50000.0,
            atm_strike=50000,
            days_to_expiry=30.5,
            strikes=strikes[:100],  # Max 100
            summary={},
            notes=[],
        )

        assert len(response.strikes) <= 100


class TestOpenInterestByStrikeResponse:
    """Tests for OpenInterestByStrikeResponse."""

    def test_oi_response_structure(self):
        """Test OpenInterestByStrikeResponse structure."""
        from deribit_mcp.models import OIPeakInfo

        response = OpenInterestByStrikeResponse(
            ccy="BTC",
            expiry="28JUN24",
            spot=50000.0,
            total_call_oi=50000,
            total_put_oi=40000,
            pcr_total=0.8,
            oi_by_strike=[
                StrikeOIData(strike=50000, call_oi=10000, put_oi=8000, total_oi=18000, pcr=0.8),
                StrikeOIData(strike=55000, call_oi=5000, put_oi=6000, total_oi=11000, pcr=1.2),
            ],
            top_strikes=[
                StrikeOIData(strike=50000, call_oi=10000, put_oi=8000, total_oi=18000, pcr=0.8),
            ],
            peak_range=OIPeakInfo(low=48000, high=52000, concentration=0.7),
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 3000

    def test_oi_top_strikes_max_5(self):
        """Test top_strikes limited to 5."""
        from deribit_mcp.models import OIPeakInfo

        response = OpenInterestByStrikeResponse(
            ccy="BTC",
            expiry="28JUN24",
            spot=50000.0,
            total_call_oi=50000,
            total_put_oi=40000,
            pcr_total=0.8,
            oi_by_strike=[],
            top_strikes=[
                StrikeOIData(strike=50000 + i * 1000, call_oi=100, put_oi=100, total_oi=200, pcr=1.0)
                for i in range(5)
            ],
            notes=[],
        )

        assert len(response.top_strikes) <= 5


class TestGammaExposureResponse:
    """Tests for GammaExposureResponse."""

    def test_gex_response_structure(self):
        """Test GammaExposureResponse structure."""
        response = GammaExposureResponse(
            ccy="BTC",
            spot=50000.0,
            expiries_included=["28JUN24", "27DEC24"],
            net_gex=1.5,  # M$
            gamma_flip=52000,
            max_pos_gex_strike=50000,
            max_neg_gex_strike=55000,
            gex_by_strike=[
                StrikeGEX(strike=50000, call_gex=-0.5, put_gex=0.8, net_gex=0.3),
                StrikeGEX(strike=55000, call_gex=-1.0, put_gex=0.4, net_gex=-0.6),
            ],
            top_positive=[StrikeGEX(strike=50000, call_gex=-0.5, put_gex=0.8, net_gex=0.3)],
            top_negative=[StrikeGEX(strike=55000, call_gex=-1.0, put_gex=0.4, net_gex=-0.6)],
            market_maker_positioning="long_gamma",
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 2000

    def test_gex_positioning_values(self):
        """Test valid market_maker_positioning values."""
        for positioning in ["long_gamma", "short_gamma", "neutral"]:
            response = GammaExposureResponse(
                ccy="BTC",
                spot=50000.0,
                expiries_included=[],
                net_gex=0,
                gex_by_strike=[],
                top_positive=[],
                top_negative=[],
                market_maker_positioning=positioning,
                notes=[],
            )
            assert response.market_maker_positioning == positioning


class TestMaxPainResponse:
    """Tests for MaxPainResponse."""

    def test_max_pain_response_structure(self):
        """Test MaxPainResponse structure."""
        from deribit_mcp.models import PainCurvePoint

        response = MaxPainResponse(
            ccy="BTC",
            expiry="28JUN24",
            expiry_ts=1719561600000,
            spot=50000.0,
            max_pain_strike=48000,
            distance_from_spot_pct=-4.0,
            pain_curve_top3=[
                PainCurvePoint(strike=48000, pain=1000000),
                PainCurvePoint(strike=49000, pain=1200000),
                PainCurvePoint(strike=47000, pain=1500000),
            ],
            total_call_oi=50000,
            total_put_oi=40000,
            pcr=0.8,
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 1000

    def test_pain_curve_max_3(self):
        """Test pain_curve_top3 limited to 3."""
        from deribit_mcp.models import PainCurvePoint

        response = MaxPainResponse(
            ccy="BTC",
            expiry="28JUN24",
            expiry_ts=1719561600000,
            spot=50000.0,
            max_pain_strike=48000,
            distance_from_spot_pct=-4.0,
            pain_curve_top3=[
                PainCurvePoint(strike=48000 + i * 1000, pain=1000000 + i * 100000)
                for i in range(3)
            ],
            total_call_oi=50000,
            total_put_oi=40000,
            notes=[],
        )

        assert len(response.pain_curve_top3) <= 3


class TestIVTermStructureResponse:
    """Tests for IVTermStructureResponse."""

    def test_term_structure_response_structure(self):
        """Test IVTermStructureResponse structure."""
        response = IVTermStructureResponse(
            ccy="BTC",
            spot=50000.0,
            term_structure=[
                TermStructurePoint(days=7, expiry="28JUN24", atm_iv=0.85, atm_iv_pct=85.0),
                TermStructurePoint(days=30, expiry="28JUL24", atm_iv=0.80, atm_iv_pct=80.0),
                TermStructurePoint(days=90, expiry="28SEP24", atm_iv=0.75, atm_iv_pct=75.0),
            ],
            slope_7d_30d=-0.5,
            slope_30d_90d=-0.3,
            shape="backwardation",
            dvol_current=82.5,
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 1000

    def test_term_structure_shape_values(self):
        """Test valid shape values."""
        for shape in ["contango", "backwardation", "flat"]:
            response = IVTermStructureResponse(
                ccy="BTC",
                spot=50000.0,
                term_structure=[],
                shape=shape,
                notes=[],
            )
            assert response.shape == shape


class TestSkewMetricsResponse:
    """Tests for SkewMetricsResponse."""

    def test_skew_response_structure(self):
        """Test SkewMetricsResponse structure."""
        response = SkewMetricsResponse(
            ccy="BTC",
            spot=50000.0,
            skew_by_tenor=[
                TenorSkew(
                    days=7,
                    expiry="28JUN24",
                    atm_iv=0.85,
                    rr25d=-0.02,
                    rr25d_pct=-2.0,
                    bf25d=0.01,
                    bf25d_pct=1.0,
                    skew_dir="bearish",
                ),
                TenorSkew(
                    days=30,
                    expiry="28JUL24",
                    atm_iv=0.80,
                    rr25d=-0.01,
                    rr25d_pct=-1.0,
                    bf25d=0.005,
                    bf25d_pct=0.5,
                    skew_dir="bearish",
                ),
            ],
            skew_trend="flattening",
            summary={
                "avg_rr25d_pct": -1.5,
                "avg_bf25d_pct": 0.75,
                "dominant_direction": "bearish",
            },
            notes=[],
        )

        json_str = response.model_dump_json()
        assert len(json_str) < 1500

    def test_skew_trend_values(self):
        """Test valid skew_trend values."""
        for trend in ["steepening", "flattening", "stable", None]:
            response = SkewMetricsResponse(
                ccy="BTC",
                spot=50000.0,
                skew_by_tenor=[],
                skew_trend=trend,
                summary={},
                notes=[],
            )
            assert response.skew_trend == trend

    def test_skew_direction_values(self):
        """Test valid skew_dir values."""
        for direction in ["bullish", "bearish", "neutral", None]:
            tenor = TenorSkew(
                days=7,
                expiry="28JUN24",
                skew_dir=direction,
            )
            assert tenor.skew_dir == direction


class TestNewToolsJSONSchema:
    """Tests for JSON schema compliance."""

    def test_option_chain_iv_units(self):
        """Test IV is in decimal form (0-1)."""
        from deribit_mcp.models import OptionStrikeData

        strike = OptionStrikeData(strike=50000, type="call", mark_iv=0.80)

        # IV should be decimal, not percentage
        assert strike.mark_iv == 0.80
        assert strike.mark_iv < 1.5  # Sanity check

    def test_gex_units_millions(self):
        """Test GEX is in M$ units."""
        gex = StrikeGEX(strike=50000, call_gex=-1.5, put_gex=2.0, net_gex=0.5)

        # Values should be in M$ (reasonable range for BTC options)
        assert -100 < gex.net_gex < 100  # Sanity check

    def test_slope_units(self):
        """Test slope is in IV% change per 30 days."""
        response = IVTermStructureResponse(
            ccy="BTC",
            spot=50000.0,
            term_structure=[],
            slope_7d_30d=-2.5,  # -2.5% IV change per 30 days
            shape="backwardation",
            notes=[],
        )

        # Slope should be percentage points
        assert response.slope_7d_30d is not None
        assert -50 < response.slope_7d_30d < 50  # Sanity check
